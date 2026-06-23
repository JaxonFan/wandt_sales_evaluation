"""The W&T metric engine — ported from the validated `wandt_metric_design.ipynb`.

Pure functions over a pandas DataFrame of item-level sales lines (rep transactions
only), so it can be unit-tested against the notebook. Metric = profit dollars
(extended_price - extended_cost); headline = market-adjusted profit; real growth =
volume$ (price-volume-mix). Baseline ladder by history length: new / provisional
(prior quarter) / mature (year-over-year). No margin floor (reps don't set margin).

Expected DataFrame columns (snake_case):
  account, associate, document_date(datetime64), line_profit, extended_price, qty,
  item_number  [, customer_name]
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

FEDERAL_HOLIDAYS = set(USFederalHolidayCalendar().holidays(start="2018-01-01", end="2031-01-01").normalize())


def day_of_week_weights(df):
    """Each weekday's share of a normal week's revenue (sums to 1). Index 0=Mon..6=Sun."""
    daily_revenue = df.groupby(df["document_date"].dt.normalize())["extended_price"].sum()
    by_day = pd.DataFrame({"revenue": daily_revenue.values, "day_of_week": daily_revenue.index.dayofweek})
    mean_per_day = by_day.groupby("day_of_week")["revenue"].mean()
    if mean_per_day.sum() == 0:
        return pd.Series([1 / 7] * 7, index=range(7))
    return (mean_per_day / mean_per_day.sum()).reindex(range(7)).fillna(0.0)


def selling_day_capacity(dow_weight, start_date, end_date, holiday_weight=0.0):
    """Selling-day-equivalents in [start_date, end_date] (federal holidays down-weighted)."""
    days = pd.date_range(start_date, end_date, freq="D")
    return float(np.sum([(holiday_weight if day.normalize() in FEDERAL_HOLIDAYS else 1.0) * dow_weight[day.dayofweek]
                         for day in days]))


def whole_week_window(df, window_end, weeks):
    window_start = window_end - pd.Timedelta(weeks=weeks)
    return df[(df["document_date"] > window_start) & (df["document_date"] <= window_end)]


def price_volume_mix_bridge(recent, baseline, baseline_scale):
    """Per-account volume$ / price$ bridge (in dollars; unit-safe). volume$ = real growth."""
    def by_item(df):
        grouped = df.groupby(["account", "item_number"]).agg(qty=("qty", "sum"),
                                                            revenue=("extended_price", "sum"))
        grouped["unit_price"] = grouped["revenue"] / grouped["qty"].replace(0, np.nan)
        return grouped
    merged = by_item(recent).join(by_item(baseline), how="outer",
                                  lsuffix="_recent", rsuffix="_baseline").fillna(0.0)
    merged["qty_baseline"] *= baseline_scale
    merged["revenue_baseline"] *= baseline_scale
    baseline_price = merged["unit_price_baseline"].where(merged["unit_price_baseline"] > 0,
                                                         merged["unit_price_recent"])
    merged["volume_dollars"] = (merged["qty_recent"] - merged["qty_baseline"]) * baseline_price
    merged["price_dollars"] = np.where(merged["unit_price_baseline"] > 0,
                                       merged["qty_recent"] * (merged["unit_price_recent"] - merged["unit_price_baseline"]),
                                       0.0)
    return merged.groupby("account").agg(volume_dollars=("volume_dollars", "sum"),
                                         price_dollars=("price_dollars", "sum"),
                                         revenue_recent=("revenue_recent", "sum"),
                                         revenue_baseline=("revenue_baseline", "sum"))


def classify_account_tier(history_weeks, has_year_ago_profit, has_prior_quarter_profit,
                          provisional_min_weeks=13):
    if history_weeks < provisional_min_weeks:
        return "new"                       # genuinely new -> acquisition
    if has_year_ago_profit:
        return "mature"                    # >=1yr history with a year-ago window -> YoY
    if has_prior_quarter_profit:
        return "provisional"               # history but no clean year-ago -> prior quarter
    return "new"                            # long-dormant reactivation, no comparable baseline


def exclude_constrained_items(df, constrained_item_numbers):
    """Drop the given item numbers. Called on each window the engine builds -> symmetric."""
    if not constrained_item_numbers:
        return df
    return df[~df["item_number"].isin(set(constrained_item_numbers))]


def compute_wandt(df, as_of, sales_team, *, window_weeks=13, provisional_min_weeks=13,
                  defend_pct=0.35, acquisition_pct=0.02, acquisition_ramp_periods=3,
                  bonus_pool=1000.0, constrained_item_numbers=None,
                  familiar_min_weeks=4, familiar_max_gap_weeks=26, holiday_weight=0.0,
                  exempt_accounts=None):
    """Returns dict(as_of, market_drift, account, lines, scorecards, capacity_recent, capacity_year_ago)."""
    as_of = pd.Timestamp(as_of).normalize()
    exempt_accounts = set(exempt_accounts or [])
    df = exclude_constrained_items(df, constrained_item_numbers)
    if not len(df):
        return dict(as_of=as_of, market_drift=1.0, account=pd.DataFrame(), lines=pd.DataFrame(),
                    scorecards=pd.DataFrame(), capacity_recent=1.0, capacity_year_ago=1.0)
    dow_weight = day_of_week_weights(df)

    recent = whole_week_window(df, as_of, window_weeks)
    year_ago = whole_week_window(df, as_of - pd.Timedelta(weeks=52), window_weeks)
    prior_quarter = whole_week_window(df, as_of - pd.Timedelta(weeks=window_weeks), window_weeks)
    capacity_recent = selling_day_capacity(dow_weight, as_of - pd.Timedelta(weeks=window_weeks) + pd.Timedelta(days=1), as_of, holiday_weight)
    capacity_year_ago = selling_day_capacity(dow_weight, as_of - pd.Timedelta(weeks=52 + window_weeks) + pd.Timedelta(days=1), as_of - pd.Timedelta(weeks=52), holiday_weight)
    capacity_prior_quarter = selling_day_capacity(dow_weight, as_of - pd.Timedelta(weeks=2 * window_weeks) + pd.Timedelta(days=1), as_of - pd.Timedelta(weeks=window_weeks), holiday_weight)
    scale_year_ago = capacity_recent / capacity_year_ago if capacity_year_ago else 1.0
    scale_prior_quarter = capacity_recent / capacity_prior_quarter if capacity_prior_quarter else 1.0

    account = pd.DataFrame({
        "recent_profit": recent.groupby("account")["line_profit"].sum(),
        "recent_revenue": recent.groupby("account")["extended_price"].sum(),
        "year_ago_profit": year_ago.groupby("account")["line_profit"].sum() * scale_year_ago,
        "year_ago_revenue": year_ago.groupby("account")["extended_price"].sum() * scale_year_ago,
        "prior_quarter_profit": prior_quarter.groupby("account")["line_profit"].sum() * scale_prior_quarter,
        "prior_quarter_revenue": prior_quarter.groupby("account")["extended_price"].sum() * scale_prior_quarter,
    }).fillna(0.0)
    first_seen = df.groupby("account")["document_date"].min()
    account["history_weeks"] = (as_of - first_seen.reindex(account.index)).dt.days / 7

    account["tier"] = [classify_account_tier(history_weeks, year_ago_profit > 0, prior_quarter_profit > 0,
                                             provisional_min_weeks)
                       for history_weeks, year_ago_profit, prior_quarter_profit
                       in zip(account.history_weeks, account.year_ago_profit, account.prior_quarter_profit)]
    account["baseline_profit"] = np.where(account.tier == "mature", account.year_ago_profit,
                                 np.where(account.tier == "provisional", account.prior_quarter_profit, 0.0))
    account["baseline_revenue"] = np.where(account.tier == "mature", account.year_ago_revenue,
                                  np.where(account.tier == "provisional", account.prior_quarter_revenue, 0.0))

    # size de-trend (relative to market) + market drift, both on profit
    scored_accounts = account[(account.baseline_profit > 50) & (account.recent_profit != 0)].copy()
    if len(scored_accounts) >= 10:
        scored_accounts["ratio"] = scored_accounts.recent_profit / scored_accounts.baseline_profit
        decile = pd.qcut(scored_accounts.baseline_profit, 10, labels=False, duplicates="drop")
        decile_median_ratio = scored_accounts.groupby(decile)["ratio"].transform("median")
        market_drift = float(scored_accounts["ratio"].median())
        account["size_factor"] = (decile_median_ratio / market_drift).reindex(account.index).fillna(1.0)
    else:
        account["size_factor"] = 1.0
        market_drift = 1.0

    # familiarity in the year before the window -> ramping flag
    window_start = as_of - pd.Timedelta(weeks=window_weeks)
    before_window = df[(df["document_date"] <= window_start)
                       & (df["document_date"] > window_start - pd.Timedelta(weeks=52))].copy()
    familiar_weeks, weeks_since_last_touch = {}, {}
    if len(before_window):
        before_window["iso_week"] = before_window["document_date"].dt.to_period("W")
        familiarity = before_window.groupby(["associate", "account"]).agg(
            weeks=("iso_week", "nunique"), last_touch=("document_date", "max"))
        familiar_weeks = familiarity["weeks"].to_dict()
        weeks_since_last_touch = ((window_start - familiarity["last_touch"]).dt.days / 7).to_dict()

    volume_bridge = price_volume_mix_bridge(recent, year_ago, scale_year_ago)

    # per (associate, account) lines — credit each rep their own profit
    line_records = []
    recent_team = recent[recent["associate"].isin(sales_team)]
    for (associate, account_id), group in recent_team.groupby(["associate", "account"]):
        if account_id in exempt_accounts:
            continue
        account_row = account.loc[account_id] if account_id in account.index else None
        actual_profit = group["line_profit"].sum()
        tier = account_row["tier"] if account_row is not None else "new"
        has_baseline = account_row is not None and account_row["baseline_profit"] > 0
        work_share = (actual_profit / account_row["recent_profit"]) if (account_row is not None and account_row["recent_profit"]) else np.nan
        size_factor = account_row["size_factor"] if account_row is not None else 1.0
        profit_target = work_share * account_row["baseline_profit"] * size_factor if has_baseline else np.nan
        if not has_baseline:
            status = "new"
        else:
            is_familiar = (familiar_weeks.get((associate, account_id), 0) >= familiar_min_weeks
                           and weeks_since_last_touch.get((associate, account_id), 1e9) <= familiar_max_gap_weeks)
            status = "scored" if is_familiar else "ramping"
        volume_dollars = volume_bridge.loc[account_id, "volume_dollars"] * work_share if account_id in volume_bridge.index else 0.0
        baseline_revenue_share = (account_row["baseline_revenue"] * work_share) if account_row is not None else 0.0
        line_records.append(dict(associate=associate, account=account_id, tier=tier, status=status,
                                 actual_profit=actual_profit, profit_target=profit_target, work_share=work_share,
                                 volume_dollars=volume_dollars, baseline_revenue_share=baseline_revenue_share))
    lines = pd.DataFrame(line_records)

    new_account_rollup = (lines[lines.status == "new"].groupby("associate")
                          .agg(new_accounts=("account", "nunique"), new_account_profit=("actual_profit", "sum"))
                          if len(lines) else pd.DataFrame())
    if len(new_account_rollup):
        new_account_rollup["acquisition_bonus"] = new_account_rollup["new_account_profit"] * acquisition_pct

    scorecard_rows = []
    for associate in sales_team:
        scored = lines[(lines.associate == associate) & (lines.status.isin(["scored", "ramping"]))] if len(lines) else lines
        actual_profit = scored.actual_profit.sum() if len(scored) else 0.0
        profit_target = scored.profit_target.sum() if len(scored) else 0.0
        baseline_revenue = scored.baseline_revenue_share.sum() if len(scored) else 0.0
        fully_scored = scored[scored.status == "scored"] if len(scored) else scored
        grow_dollars = ((max(0.0, fully_scored.actual_profit.sum() - fully_scored.profit_target.sum())
                         + scored[scored.status == "ramping"]["volume_dollars"].clip(lower=0).sum())
                        if len(scored) else 0.0)
        acquisition = new_account_rollup.loc[associate] if (len(new_account_rollup) and associate in new_account_rollup.index) else None
        scorecard_rows.append(dict(
            associate=associate, accounts=int(scored.account.nunique()) if len(scored) else 0,
            actual_profit=actual_profit, profit_target=profit_target,
            profit_perf_pct=(actual_profit / profit_target - 1) * 100 if profit_target else None,
            profit_vs_market_pct=((actual_profit / profit_target) / market_drift - 1) * 100 if (profit_target and market_drift) else None,
            real_growth_pct=(scored.volume_dollars.sum() / baseline_revenue * 100) if baseline_revenue else None,
            defend_dollars=actual_profit, grow_dollars=grow_dollars,
            new_accounts=int(acquisition.new_accounts) if acquisition is not None else 0,
            acquisition_bonus=float(acquisition.acquisition_bonus) if acquisition is not None else 0.0))
    scorecards = pd.DataFrame(scorecard_rows)

    # Grow-heavy fixed-pool split; acquisition paid separately (commission-style)
    defend_pool, grow_pool = bonus_pool * defend_pct, bonus_pool * (1 - defend_pct)
    total_defend = scorecards.defend_dollars.clip(lower=0).sum() if len(scorecards) else 0
    total_grow = scorecards.grow_dollars.clip(lower=0).sum() if len(scorecards) else 0
    scorecards["defend_bonus"] = defend_pool * scorecards.defend_dollars.clip(lower=0) / total_defend if total_defend else 0.0
    scorecards["grow_bonus"] = grow_pool * scorecards.grow_dollars.clip(lower=0) / total_grow if total_grow else 0.0
    scorecards["total_bonus"] = scorecards.defend_bonus + scorecards.grow_bonus + scorecards.acquisition_bonus
    return dict(as_of=as_of, market_drift=market_drift, account=account, lines=lines, scorecards=scorecards,
                capacity_recent=capacity_recent, capacity_year_ago=capacity_year_ago)
