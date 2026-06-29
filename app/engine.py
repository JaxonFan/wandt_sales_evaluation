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


# =====================================================================================
# Direct-formula bonus (per 4-week period) — the understandable model used by the app.
#   Contribution = items placed x item_rate
#   Growth       = max(0, sales - target) x growth_payout_rate ; target size-tiered + part-time
#   Acquisition  = landing (% of a new account's first-period sales) + ramp (% for ~1 quarter)
# Each piece is a direct formula on the rep's OWN numbers — no pool, no peer ranking.
# =====================================================================================
ONE_YEAR = pd.Timedelta(days=364)   # 52 weeks — keeps weekday composition aligned


def growth_tier_pct(annual_sales, thresholds, pcts):
    """Pick a growth % by the account's trailing-year size. thresholds=(large_min, medium_min)."""
    large_min, medium_min = thresholds
    if annual_sales >= large_min:
        return pcts["large"]
    if annual_sales >= medium_min:
        return pcts["medium"]
    return pcts["small"]


# Lunar New Year (Gregorian dates) — a moving holiday with a big demand spike. For periods that
# overlap CNY we align the year-ago baseline to LAST year's CNY (not a fixed 364 days) so the spike
# lines up on both sides of the growth comparison.
CNY_DATES = [pd.Timestamp(d) for d in
             ["2023-01-22", "2024-02-10", "2025-01-29", "2026-02-17", "2027-02-06", "2028-01-26",
              "2029-02-13", "2030-02-03", "2031-01-23", "2032-02-11", "2033-01-31", "2034-02-19", "2035-02-08"]]


def cny_aligned_offset_days(period_start, period_end, default_days=364, window_days=21):
    """If the period OVERLAPS a CNY +/- window_days (its ~3-week spike), return days to the prior-year
    CNY so the year-ago window lines up the spike (catches the adjacent spillover period too); else 364."""
    for i in range(1, len(CNY_DATES)):
        cny = CNY_DATES[i]
        if period_start <= cny + pd.Timedelta(days=window_days) and period_end >= cny - pd.Timedelta(days=window_days):
            return (cny - CNY_DATES[i - 1]).days
    return default_days


def item_cost_inflation(df, reference_date, offset_days, weeks=8, clip=(0.5, 2.0)):
    """Per-item cost-inflation factor known BEFORE the period: recent unit cost (the `weeks` before
    `reference_date`) vs the same window a year (offset_days) earlier. Returns (dict item->factor, overall)."""
    recent = df[(df["document_date"] <= reference_date) & (df["document_date"] > reference_date - pd.Timedelta(weeks=weeks))]
    base_end = reference_date - pd.Timedelta(days=offset_days)
    base = df[(df["document_date"] <= base_end) & (df["document_date"] > base_end - pd.Timedelta(weeks=weeks))]

    def unit_cost(frame):
        g = frame.groupby("item_number").agg(cost=("extended_cost", "sum"), qty=("qty", "sum"))
        return g["cost"] / g["qty"].replace(0, np.nan)

    recent_cost, base_cost = unit_cost(recent), unit_cost(base)
    factor = (recent_cost / base_cost).replace([np.inf, -np.inf], np.nan).clip(*clip).dropna()
    # fallback for brand-new/unmatched items = the MEDIAN per-item inflation (robust; a cost-weighted
    # overall is mix-sensitive and overstates inflation). Default 1.0 if no matched items.
    overall = float(factor.median()) if len(factor) else 1.0
    return factor.to_dict(), overall


def _size_band_factors(baseline_q, recent_q, n_bands):
    """Median (recent/baseline) per size band of baseline_q — the 'typical move for accounts your size'.
    Captures the market tide + inflation, so it subsumes both. Returns dict account -> band factor."""
    pairs = pd.DataFrame({"base": baseline_q, "recent": recent_q.reindex(baseline_q.index).fillna(0.0)})
    pairs = pairs[pairs["base"] > 300]
    if len(pairs) < n_bands * 2:
        overall = float((pairs["recent"] / pairs["base"]).median()) if len(pairs) else 1.0
        return {a: overall for a in pairs.index}, overall
    pairs["ratio"] = pairs["recent"] / pairs["base"]
    band = pd.qcut(pairs["base"], n_bands, labels=False, duplicates="drop")
    band_median = pairs.groupby(band)["ratio"].transform("median")
    overall = float(pairs["ratio"].median())
    return band_median.to_dict(), overall


def _glide_levels(df, window_end, alpha, step_weeks=4, value_col="extended_price"):
    """Per-account 'established level' = EWMA (recursive, factor `alpha`) of the account's prior
    `step_weeks`-week run-rate windows, EXCLUDING the current window. Dormant gaps count as $0 and
    pull the level down; the series is seeded at the account's first window. This is the moving bar
    the glide compares against for accounts whose year-ago window is too small to use."""
    step = pd.Timedelta(weeks=step_weeks)
    hist = df[df["document_date"] <= window_end]
    if not len(hist):
        return {}
    bucket = np.floor((window_end - hist["document_date"]) / step).astype(int)   # 0 = current window
    sums = hist.assign(_b=bucket).groupby(["account", "_b"])[value_col].sum()
    levels = {}
    for acct, s in sums.groupby(level=0):
        prior = s.droplevel(0)
        prior = prior[prior.index >= 1]                                          # drop the current window
        if not len(prior):
            continue
        oldest = int(prior.index.max())
        series = prior.reindex(range(oldest, 0, -1), fill_value=0.0)             # oldest -> newest (bucket 1)
        levels[acct] = float(series.ewm(alpha=alpha, adjust=False).mean().iloc[-1])
    return levels


def _cost_inflation_factor(df, recent_lo, recent_hi, base_lo, base_hi, lo_clamp=0.7, hi_clamp=1.5):
    """Company cost-inflation index = LAST YEAR's basket repriced at TODAY's cost (matched-item Laspeyres):
    sum(base_qty x recent_unit_cost) / sum(base_qty x base_unit_cost), over items sold in BOTH windows.
    Used to lift the year-ago bar so passing higher costs through isn't counted as growth. 1.0 if too few matches."""
    def unit_cost(lo, hi):
        w = df[(df["document_date"] > lo) & (df["document_date"] <= hi)]
        g = w.groupby("item_number").agg(c=("extended_cost", "sum"), q=("qty", "sum"))
        g = g[g["q"] > 0]
        return g.assign(u=g["c"] / g["q"])
    rec, base = unit_cost(recent_lo, recent_hi), unit_cost(base_lo, base_hi)
    m = base.join(rec["u"].rename("ru"), how="inner")            # items in both; base qty + both unit costs
    if len(m) < 10:
        return 1.0
    den = (m["q"] * m["u"]).sum()
    return float(min(max((m["q"] * m["ru"]).sum() / den, lo_clamp), hi_clamp)) if den > 0 else 1.0


def _cost_adjusted_baseline(df, lo, hi, cost_factor, scale=1.0):
    """Per-account bar = 'cover TODAY's cost of the (lo, hi] basket and still clear its profit':
    (extended_cost x cost_factor) + line_profit, optionally scaled. Shared by the period + annual engines."""
    w = df[(df["document_date"] > lo) & (df["document_date"] <= hi)]
    cost = w.groupby("account")["extended_cost"].sum() * scale
    profit = w.groupby("account")["line_profit"].sum() * scale
    return (cost * cost_factor).add(profit, fill_value=0.0)


def compute_period_bonus(df, period_start, period_end, sales_team, *, as_of=None,
                         self_acquired=frozenset(), exempt_accounts=frozenset(),
                         jump_released=frozenset(),
                         period_days=28, holiday_weight=0.0, item_rate=0.10,
                         growth_window_weeks=13, size_band_count=5,
                         growth_payout_rate=0.045, growth_cap_multiple=2.0, growth_review_min=20000,
                         glide_alpha=0.35, jump_multiple=2.0, min_baseline_ratio=0.30,
                         mature_smooth_weeks=4, sporadic_gap_weeks=4, cost_inflation_weeks=13,
                         featured_new_products=frozenset(), new_product_weeks=26, new_product_attribution=0.20,
                         acq_tier_small_max=15000, acq_tier_medium_max=65000,
                         acq_flat_small=100, acq_flat_medium=200, acq_flat_large=300,
                         acq_revenue_pct=0.01, acq_ramp_periods=3):
    """Return dict(scorecards: per-rep DataFrame, accounts: per (rep, account) detail DataFrame).

    Bonus = Contribution (line items x item_rate) + Growth + Acquisition (acq_revenue_pct x a new
    account's revenue, for ~1 quarter). GROWTH is measured on the TRAILING QUARTER and de-trended by
    the typical move of accounts the same size:
      target_q = baseline_q x size_band_factor
      growth_bonus = max(0, recent_q - target_q) x growth_payout_rate x (period_weeks / window_weeks)
    Contribution & acquisition use the current period; growth uses trailing windows (smooths lumps).
    """
    period_start = pd.Timestamp(period_start).normalize()
    period_end = pd.Timestamp(period_end).normalize()
    as_of = period_end if as_of is None else min(pd.Timestamp(as_of).normalize(), period_end)
    self_acquired = set(self_acquired)                            # manager-confirmed self-won -> earns the 1%
    exempt_accounts = set(exempt_accounts)                        # manager-exempted -> removed from GROWTH only
    jump_released = set(jump_released)                             # manager-confirmed rep-won big jump -> pay windfall
    if not len(df):
        return dict(scorecards=pd.DataFrame(), accounts=pd.DataFrame())

    # --- current period (Contribution items + Acquisition) ---
    current = df[(df["document_date"] > period_start) & (df["document_date"] <= as_of)]
    first_seen = df.groupby("account")["document_date"].min()

    def account_status(account_id):
        seen = first_seen.get(account_id)
        if seen is not None:
            is_new = (seen > period_start) or ((period_end - seen).days <= acq_ramp_periods * period_days)
            if is_new:
                # a new account earns the 1% only if the manager confirmed it self-acquired; else "assigned"
                if account_id in self_acquired:
                    return "landing" if seen > period_start else "ramp"
                return "assigned"
        return "scored"

    # --- GROWTH value: a confirmed-NEW product's revenue counts at new_product_attribution (e.g. 20%) toward
    # growth for its first new_product_weeks (the company made the product; the rep is credited but discounted).
    # Line-item contribution & acquisition still use full extended_price.
    featured_new_products = set(featured_new_products)
    GV = "extended_price"
    if featured_new_products:
        item_first = df.groupby("item_number")["document_date"].min()
        is_new = (df["item_number"].isin(featured_new_products) &
                  ((period_end - df["item_number"].map(item_first)) <= pd.Timedelta(weeks=new_product_weeks)))
        df = df.assign(growth_value=np.where(is_new, df["extended_price"] * new_product_attribution,
                                             df["extended_price"]))
        GV = "growth_value"

    # --- trailing windows for GROWTH ---
    win = pd.Timedelta(weeks=growth_window_weeks)
    qend = period_end                                            # measure to period end (or as_of for live)
    qstart = qend - win
    offset = pd.Timedelta(days=cny_aligned_offset_days(qstart, qend))   # CNY-aligned year-ago shift
    recent_q_df = df[(df["document_date"] > qstart) & (df["document_date"] <= qend)]
    prior_q_df = df[(df["document_date"] > qstart - win) & (df["document_date"] <= qend - win)]   # for provisional
    account_recent_q = recent_q_df.groupby("account")[GV].sum()
    account_prior_q = prior_q_df.groupby("account")[GV].sum()
    # company cost-inflation factor (last year's basket repriced at today's cost) — lifts the year-ago bar so a
    # rep isn't credited for merely passing higher costs through. One scalar over the trailing cost_inflation_weeks.
    ci = pd.Timedelta(weeks=cost_inflation_weeks)
    cost_factor = _cost_inflation_factor(df, qend - ci, qend, qend - ci - ONE_YEAR, qend - ONE_YEAR)

    # mature baseline = same weeks last year (CNY-aligned), cost-adjusted. mature_smooth_weeks=0 = exact window.
    smooth = pd.Timedelta(weeks=mature_smooth_weeks)
    base_lo, base_hi = qstart - offset - smooth, qend - offset + smooth
    account_baseline_q = _cost_adjusted_baseline(df, base_lo, base_hi, cost_factor, win / (base_hi - base_lo))

    # sporadic = median order-gap longer than the measurement window -> empty 4-week windows. These accounts
    # are EXCLUDED here and scored on the Annual Review track (compute_annual_review) instead.
    _dd = df.assign(_d=df["document_date"].dt.normalize()).drop_duplicates(["account", "_d"]).sort_values(["account", "_d"])
    _dd["_gap"] = _dd.groupby("account")["_d"].diff().dt.days
    _median_gap = _dd.groupby("account")["_gap"].median()
    sporadic_accounts = set(_median_gap[_median_gap > sporadic_gap_weeks * 7].index)
    # company quarter-over-quarter seasonal swing (for provisional accounts with no year-ago baseline)
    company_seasonal_factor = 1.0
    if account_prior_q.sum() and account_recent_q.sum():
        company_seasonal_factor = min(max(account_recent_q.sum() / account_prior_q.sum(), 0.5), 2.0)
    # size-band de-trend factor per account (typical move for accounts its size) — subsumes market + inflation
    band_factor, overall_band_factor = _size_band_factors(account_baseline_q, account_recent_q, size_band_count)
    # glide: each account's own adaptive run-rate level (for activations with no usable year-ago window)
    glide_levels = _glide_levels(df, qend, glide_alpha, step_weeks=growth_window_weeks, value_col=GV)
    # cross-account seasonal lift for glide accounts: how accounts THIS size are moving this period vs their
    # own recent run-rate (median recent/glide per size band) — adds seasonality (holidays/CNY) without
    # leaning on a lumpy per-account same-weeks-last-year window. Restrict to accounts WITH current sales so
    # dormant accounts (recent=0) don't drag a band's median ratio to 0.
    glide_level_series = pd.Series({acc: lv for acc, lv in glide_levels.items()
                                    if account_recent_q.get(acc, 0.0) > 0}, dtype="float64")
    glide_band_factor, glide_overall_factor = _size_band_factors(glide_level_series, account_recent_q, size_band_count)

    BASELINE_MIN = 300.0          # window baseline needed to score growth (else line-items only)
    period_fraction = period_days / (growth_window_weeks * 7.0)   # prorate window outperformance to the period

    # rep x account trailing growth-value (for growth attribution / work-share) + current items
    team_recent_q = recent_q_df[recent_q_df["associate"].isin(sales_team)]
    rep_account_q = team_recent_q.groupby(["associate", "account"])[GV].sum().reset_index(name="rep_q")
    # iterate regular (4-week) accounts only; sporadic accounts are scored on the Annual Review track
    iter_rows = rep_account_q[~rep_account_q["account"].isin(sporadic_accounts)]
    items_by_rep_account = (current[current["associate"].isin(sales_team)]
                            .groupby(["associate", "account"])["extended_price"].size())
    # acquisition: a FLAT bonus by the new account's size (rewards landing, not raw size), paid ONCE at the
    # ~quarter mark — once the account has a quarter of history we can size it by its real annualized run-rate
    # (not a noisy first-period guess). Self-acquired accounts only.
    def _acq_flat(annual_rev):
        return acq_flat_small if annual_rev < acq_tier_small_max else (
            acq_flat_medium if annual_rev < acq_tier_medium_max else acq_flat_large)
    pay_lo, pay_hi = (acq_ramp_periods - 1) * period_days, acq_ramp_periods * period_days   # the quarter-mark period
    qwin = pd.Timedelta(weeks=13)
    acq_by_rep = {a: 0.0 for a in sales_team}
    new_count_by_rep = {}
    for acc in self_acquired:                       # manager-confirmed self-acquired only
        seen = first_seen.get(acc)
        if seen is None or not (pay_lo <= (period_end - seen).days < pay_hi):
            continue                                # only the one period at its quarter mark
        q = df[(df["account"] == acc) & (df["document_date"] > period_end - qwin) & (df["document_date"] <= period_end)]
        qt = q[q["associate"].isin(sales_team)]
        if not len(qt):
            continue
        rep = qt.groupby("associate")["extended_price"].sum().idxmax()         # primary rep over the quarter
        annual_rev = float(q["extended_price"].sum()) * (52.0 / 13.0)          # annualized first-quarter run-rate
        acq_by_rep[rep] = acq_by_rep.get(rep, 0.0) + _acq_flat(annual_rev)
        new_count_by_rep[rep] = new_count_by_rep.get(rep, 0) + 1

    account_rows = []
    rep_totals = {a: dict(items=0, growth_base_raw=0.0, growth_base=0.0, growth_target=0.0,
                          growth_actual=0.0, held_back=0.0, flagged=0) for a in sales_team}
    for _, r in iter_rows.iterrows():
        rep, account_id = r["associate"], r["account"]
        rep_q = float(r["rep_q"])
        account_q = float(account_recent_q.get(account_id, 0.0))   # regular: 4-week window
        acct_baseline = float(account_baseline_q.get(account_id, 0.0))
        acct_fraction = period_fraction
        work_share = rep_q / account_q if account_q else 0.0
        status = account_status(account_id)
        prior_q = float(account_prior_q.get(account_id, 0.0))

        established = float(glide_levels.get(account_id, 0.0))
        raw_for_rep = lift = None
        if status in ("landing", "ramp", "assigned"):
            pass                                                  # new (self-acquired -> 1%) or assigned: items/acq only, no growth
        elif account_id in exempt_accounts:
            status = "exempt"                                     # manager removed from GROWTH (e.g. closed); items/acq untouched
        elif acct_baseline > BASELINE_MIN and acct_baseline >= min_baseline_ratio * account_q:
            # mature: a representative (smoothed) same-weeks-last-year window -> YoY x size-band move
            raw_for_rep, lift, status = acct_baseline * work_share, band_factor.get(account_id, overall_band_factor), "mature"
        elif established > BASELINE_MIN:
            # activation / level-shifted: year-ago window too small to compare to; use the account's own
            # glide level, lifted by how accounts its size are moving this period (cross-account seasonality)
            raw_for_rep, lift, status = established * work_share, glide_band_factor.get(account_id, glide_overall_factor), "glide"
        elif prior_q > BASELINE_MIN:                              # provisional: own prior window x seasonal swing
            raw_for_rep, lift, status = prior_q * work_share, company_seasonal_factor, "provisional"
        else:
            status = "no_basis"

        released = account_id in jump_released
        t = rep_totals[rep]
        target_for_rep = None
        jump = False
        jump_bar = jump_ratio = None
        held = windfall = 0.0
        if raw_for_rep is not None:
            base_for_rep = raw_for_rep * lift                     # baseline x size/market lift
            target_for_rep = base_for_rep                       # bar = cost-adjusted last-year (cost+profit) x real-market move; no stretch hurdle
            # jump review: an account that DOUBLED its NORMAL LEVEL (recent >= jump_multiple x normal) is the
            # anomaly itself — a 10x is usually the customer growing, not the rep. "Normal level" is the HIGHER of
            # the account's recent run-rate and its seasonally-adjusted year-ago bar, so a weak year-ago comp can't
            # flag an account that's merely at/below its own pace. The over-GROWTH-bar amount is withheld for the
            # manager to investigate (no dollar floor); ordinary growth pays through; released if the rep won it.
            jump_bar = max(target_for_rep, established * work_share)   # higher of seasonal year-ago bar and recent pace
            jump = target_for_rep > BASELINE_MIN and jump_bar > 0 and rep_q >= jump_multiple * jump_bar
            jump_ratio = round(rep_q / jump_bar, 1) if jump_bar > 0 else None
            # account-level "normal" for display (work_share cancels, so account_q/jump_bar_acct == jump_ratio)
            jump_bar = jump_bar / work_share if work_share else jump_bar
            if jump and not released:
                effective_recent = target_for_rep               # withhold ALL over-bar growth pending review
                windfall = held = rep_q - target_for_rep
            else:
                effective_recent, held = rep_q, 0.0             # pay in full (normal overage, or released)
                windfall = max(0.0, rep_q - target_for_rep) if jump else 0.0
            # accumulate as PERIOD-equivalents (x acct_fraction): regular x1, annual x period_days/364
            t["growth_base_raw"] += raw_for_rep * acct_fraction
            t["growth_base"] += base_for_rep * acct_fraction
            t["growth_target"] += target_for_rep * acct_fraction
            t["growth_actual"] += effective_recent * acct_fraction
            t["held_back"] += held * acct_fraction
            t["flagged"] += int(jump and not released)
        account_rows.append(dict(associate=rep, account=account_id, status=status,
                                 rep_quarter_sales=rep_q, baseline_quarter=raw_for_rep,
                                 account_target=target_for_rep, capped=jump, held_back=round(held),
                                 windfall=round(windfall if raw_for_rep is not None else 0.0),
                                 released=released, account_recent=round(account_q),
                                 established=round(established),
                                 jump_bar=(round(jump_bar) if jump_bar is not None else None),
                                 jump_ratio=jump_ratio))

    # contribution (line items, current period) per rep
    items_per_rep = items_by_rep_account.groupby(level=0).sum().to_dict() if len(items_by_rep_account) else {}

    cards = []
    for rep in sales_team:
        t = rep_totals[rep]
        items = int(items_per_rep.get(rep, 0))
        contribution_bonus = items * item_rate
        # growth_actual/target are already period-prorated per account (regular 4-week + sporadic annual/13)
        growth_bonus = max(0.0, t["growth_actual"] - t["growth_target"]) * growth_payout_rate
        acquisition_bonus = acq_by_rep.get(rep, 0.0)            # size-tiered flat, paid once when an account lands
        cards.append(dict(
            associate=rep, items_placed=items, contribution_bonus=contribution_bonus,
            growth_base_raw=t["growth_base_raw"], growth_base=t["growth_base"],
            growth_target=t["growth_target"], growth_actual=t["growth_actual"],
            growth_bonus=growth_bonus, acquisition_bonus=acquisition_bonus,
            new_accounts=int(new_count_by_rep.get(rep, 0)),
            held_back=t["held_back"], flagged=t["flagged"],
            total_bonus=contribution_bonus + growth_bonus + acquisition_bonus))
    return dict(scorecards=pd.DataFrame(cards), accounts=pd.DataFrame(account_rows),
                company_seasonal_factor=company_seasonal_factor, overall_band_factor=overall_band_factor,
                period_fraction=period_fraction)


def compute_annual_review(df, as_of, sales_team, *, exempt_accounts=frozenset(),
                          size_band_count=5, growth_payout_rate=0.01,
                          sporadic_gap_weeks=4, cost_inflation_weeks=13,
                          featured_new_products=frozenset(), new_product_weeks=26,
                          new_product_attribution=0.20, **_ignore):
    """Annual Review track. Sporadic accounts (median order-gap longer than the 4-week window — they order
    too infrequently for a per-period measure) are scored on a ROLLING trailing 12 months vs the prior 12
    months, cost-adjusted and de-trended by the typical move of accounts their size. Growth here is paid
    ONCE A YEAR and is NOT folded into the per-period bonus. Returns dict(scorecards, accounts).

      annual_target = (prior-year cost x cost_factor + prior-year profit) x work_share x size-band move
      annual_growth_bonus = max(0, sum(annual_actual) - sum(annual_target)) x growth_payout_rate
    """
    as_of = pd.Timestamp(as_of).normalize()
    sales_team = list(sales_team)
    exempt_accounts = set(exempt_accounts)
    empty = dict(scorecards=pd.DataFrame(), accounts=pd.DataFrame())
    if not len(df):
        return empty

    # growth value: a featured-new product's revenue counts at new_product_attribution (same rule as the
    # period engine), so the annual measure doesn't over-credit company-launched products.
    featured_new_products = set(featured_new_products)
    GV = "extended_price"
    if featured_new_products:
        item_first = df.groupby("item_number")["document_date"].min()
        is_new = (df["item_number"].isin(featured_new_products) &
                  ((as_of - df["item_number"].map(item_first)) <= pd.Timedelta(weeks=new_product_weeks)))
        df = df.assign(growth_value=np.where(is_new, df["extended_price"] * new_product_attribution,
                                             df["extended_price"]))
        GV = "growth_value"

    # sporadic = median order-gap longer than the 4-week measurement window
    _dd = df.assign(_d=df["document_date"].dt.normalize()).drop_duplicates(["account", "_d"]).sort_values(["account", "_d"])
    _dd["_gap"] = _dd.groupby("account")["_d"].diff().dt.days
    _median_gap = _dd.groupby("account")["_gap"].median()
    sporadic = set(_median_gap[_median_gap > sporadic_gap_weeks * 7].index)
    if not sporadic:
        return empty

    year = pd.Timedelta(weeks=52)
    ci = pd.Timedelta(weeks=cost_inflation_weeks)
    cost_factor = _cost_inflation_factor(df, as_of - ci, as_of, as_of - ci - ONE_YEAR, as_of - ONE_YEAR)
    annual_recent = df[(df["document_date"] > as_of - year) & (df["document_date"] <= as_of)].groupby("account")[GV].sum()
    annual_baseline = _cost_adjusted_baseline(df, as_of - 2 * year, as_of - year, cost_factor)
    band_factor, overall_band = _size_band_factors(annual_baseline, annual_recent, size_band_count)

    team_annual = df[(df["document_date"] > as_of - year) & (df["document_date"] <= as_of) & df["associate"].isin(sales_team)]
    rep_account = (team_annual[team_annual["account"].isin(sporadic)]
                   .groupby(["associate", "account"])[GV].sum().reset_index(name="rep_q"))

    BASELINE_MIN = 300.0
    rep_totals = {a: dict(actual=0.0, target=0.0, accounts=0) for a in sales_team}
    account_rows = []
    for _, r in rep_account.iterrows():
        rep, account_id, rep_q = r["associate"], r["account"], float(r["rep_q"])
        account_q = float(annual_recent.get(account_id, 0.0))
        baseline = float(annual_baseline.get(account_id, 0.0))
        work_share = rep_q / account_q if account_q else 0.0
        status, target = "annual", None
        if account_id in exempt_accounts:
            status = "exempt"                                    # manager removed from annual growth (e.g. closed)
        elif baseline > BASELINE_MIN:
            target = baseline * work_share * band_factor.get(account_id, overall_band)
            t = rep_totals[rep]
            t["actual"] += rep_q
            t["target"] += target
            t["accounts"] += 1
        else:
            status = "no_basis"                                  # no usable prior-year window -> not scored
        perf = ((rep_q / target - 1) * 100) if target else None
        account_rows.append(dict(associate=rep, account=account_id, status=status,
                                 sales=round(rep_q), target=(round(target) if target is not None else None),
                                 perf=(round(perf) if perf is not None else None)))

    cards = []
    for rep in sales_team:
        t = rep_totals[rep]
        bonus = max(0.0, t["actual"] - t["target"]) * growth_payout_rate
        cards.append(dict(associate=rep, annual_accounts=int(t["accounts"]),
                          annual_actual=t["actual"], annual_target=t["target"],
                          annual_growth_bonus=bonus))
    return dict(scorecards=pd.DataFrame(cards), accounts=pd.DataFrame(account_rows))
