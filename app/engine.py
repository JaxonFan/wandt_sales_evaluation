"""The W&T bonus engine — pure functions over a pandas DataFrame of item-level sales lines (rep
transactions only). The bonus is THREE direct, self-computable pieces per 4-week period (no pool, no
peer ranking):
  Contribution = line items placed x item_rate
  Growth       = max(0, recent revenue - target) x growth_payout_rate, where the target is the
                 cost-adjusted same-weeks-last-year bar (or a glide/provisional bar for newer accounts)
                 lifted by the typical move of accounts your size; gated by a quarter-PROFIT redline and
                 an annual reality check (must be genuinely up year-over-year on revenue).
  Acquisition  = a size-tiered flat bonus, paid once when a self-acquired new account lands.

Expected DataFrame columns (snake_case):
  account, associate, document_date(datetime64), line_profit, extended_price, extended_cost, qty,
  item_number  [, customer_name]
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def exclude_constrained_items(df, constrained_item_numbers):
    """Drop the given item numbers. Called on each window the engine builds -> symmetric."""
    if not constrained_item_numbers:
        return df
    return df[~df["item_number"].isin(set(constrained_item_numbers))]


# =====================================================================================
# Direct-formula bonus (per 4-week period) — the understandable model used by the app.
#   Contribution = items placed x item_rate
#   Growth       = max(0, sales - target) x growth_payout_rate ; target size-tiered + part-time
#   Acquisition  = landing (% of a new account's first-period sales) + ramp (% for ~1 quarter)
# Each piece is a direct formula on the rep's OWN numbers — no pool, no peer ranking.
# =====================================================================================
ONE_YEAR = pd.Timedelta(days=364)   # 52 weeks — keeps weekday composition aligned


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


def _size_band_factors(baseline_q, recent_q, n_bands, floor=0.0):
    """Median (recent/baseline) per size band of baseline_q — the 'typical move for accounts your size'.
    Captures the market tide + inflation, so it subsumes both. Clamped to >= `floor`: floor=1.0 means the
    bar is never DISCOUNTED below cost-adjusted last year (a soft segment can't lower it), while up-segments
    still lift it. Returns dict account -> band factor."""
    pairs = pd.DataFrame({"base": baseline_q, "recent": recent_q.reindex(baseline_q.index).fillna(0.0)})
    pairs = pairs[pairs["base"] > 300]
    if len(pairs) < n_bands * 2:
        overall = float((pairs["recent"] / pairs["base"]).median()) if len(pairs) else 1.0
        overall = max(floor, overall)
        return {a: overall for a in pairs.index}, overall
    pairs["ratio"] = pairs["recent"] / pairs["base"]
    band = pd.qcut(pairs["base"], n_bands, labels=False, duplicates="drop")
    band_median = pairs.groupby(band)["ratio"].transform("median").clip(lower=floor)
    overall = max(floor, float(pairs["ratio"].median()))
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
                         jump_released=frozenset(), constrained_item_numbers=frozenset(),
                         period_days=28, holiday_weight=0.0, item_rate=0.10,
                         growth_window_weeks=13, size_band_count=5,
                         size_band_window_weeks=52, size_band_floor=0.0,
                         growth_payout_rate=0.045, growth_cap_multiple=2.0, growth_review_min=20000,
                         glide_alpha=0.35, jump_multiple=2.0, min_baseline_ratio=0.30,
                         mature_smooth_weeks=4, sporadic_gap_weeks=4, cost_inflation_weeks=13,
                         growth_quarter_floor=0.0, growth_quarter_min_prior=3000, growth_quarter_min_profit=600,
                         growth_annual_floor=1.0, growth_annual_min_prior=50000,
                         featured_new_products=frozenset(), new_product_weeks=26, new_product_attribution=0.20,
                         substitute_products=frozenset(), substitute_attribution=0.60,
                         acq_tier_small_max=15000, acq_tier_medium_max=65000,
                         acq_flat_small=100, acq_flat_medium=200, acq_flat_large=300,
                         acq_revenue_pct=0.01, acq_ramp_periods=3):
    """Return dict(scorecards: per-rep DataFrame, accounts: per (rep, account) detail DataFrame).

    Bonus = Contribution (line items x item_rate) + Growth + Acquisition (a size-tiered FLAT bonus
    $acq_flat_small/medium/large, paid once when a self-acquired new account lands at its ~quarter mark).
    GROWTH is measured over the trailing growth_window_weeks and de-trended by the typical move of
    accounts the same size:
      target_q = baseline_q x size_band_factor
      growth_bonus = max(0, recent_q - target_q) x growth_payout_rate
    gated when the account's quarter PROFIT is down (>5%) or it isn't genuinely up year-over-year on
    revenue. Contribution & acquisition use the current period; growth uses trailing windows (smooths lumps).
    Constrained items are excluded from GROWTH only (contribution/acquisition keep them).
    """
    period_start = pd.Timestamp(period_start).normalize()
    period_end = pd.Timestamp(period_end).normalize()
    as_of = period_end if as_of is None else min(pd.Timestamp(as_of).normalize(), period_end)
    self_acquired = set(self_acquired)                            # manager-confirmed self-won -> earns the 1%
    exempt_accounts = set(exempt_accounts)                        # manager-exempted -> removed from GROWTH only
    jump_released = set(jump_released)                             # manager-confirmed rep-won big jump -> pay windfall
    if not len(df):
        return dict(scorecards=pd.DataFrame(), accounts=pd.DataFrame())

    # --- current period (Contribution items + Acquisition) — keep the FULL data here: supply-constrained
    # items still count for line-item contribution & acquisition sizing (the rep did ship those lines).
    # Only GROWTH excludes constrained items (below), so a shortage isn't charged against the rep. ---
    full_df = df
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

    # GROWTH only: drop manager-flagged supply-constrained items so they're removed from BOTH the recent and
    # the baseline/year-ago windows symmetrically — a drop the rep "couldn't ship" isn't charged against them.
    # (Contribution & acquisition above keep the full data.)
    df = exclude_constrained_items(df, constrained_item_numbers)
    if not len(df):
        return dict(scorecards=pd.DataFrame(), accounts=pd.DataFrame())

    # --- GROWTH value: a confirmed-NEW product's revenue counts at new_product_attribution (e.g. 20%) toward
    # growth for its first new_product_weeks (the company made the product; the rep is credited but discounted).
    # Line-item contribution & acquisition still use full extended_price.
    featured_new_products = set(featured_new_products)
    substitute_products = set(substitute_products)
    GV = "extended_price"
    if featured_new_products or substitute_products:
        item_first = df.groupby("item_number")["document_date"].min()
        # A featured product's growth credit RAMPS smoothly from its STARTING attribution at first sale up to 100%
        # by new_product_weeks of age (no cliff). The start differs by kind: a cheaper substitute starts higher
        # (substitute_attribution, e.g. 60%) than a brand-new product (new_product_attribution, e.g. 40%);
        # everything else starts at 100% (no discount). Ramp is time-based on the product's age.
        start_by_item = {**{it: new_product_attribution for it in featured_new_products},
                         **{it: substitute_attribution for it in substitute_products}}   # substitute wins on overlap
        start = df["item_number"].map(start_by_item).fillna(1.0)
        age_weeks = (period_end - df["item_number"].map(item_first)).dt.days / 7.0
        ramp = start + (1.0 - start) * (age_weeks / new_product_weeks).clip(0.0, 1.0)
        df = df.assign(growth_value=df["extended_price"] * ramp)
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
    # account's growth-window (4-week) profit + revenue -> margin, for the /jumps review (spot low-margin spikes)
    account_recent_rev4 = recent_q_df.groupby("account")["extended_price"].sum()
    account_recent_profit4 = recent_q_df.groupby("account")["line_profit"].sum()
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
    _gb = _dd.groupby("account")["_gap"]
    _cut = sporadic_gap_weeks * 7   # sporadic if MEDIAN or MEAN order gap >= 4 weeks (mean catches burst-then-dormant accounts)
    sporadic_accounts = set(_gb.median()[lambda s: s >= _cut].index) | set(_gb.mean()[lambda s: s >= _cut].index)
    # trailing-QUARTER context (display-only) — used to flag a likely order-TIMING shift: when the account's
    # last 12 months are flat vs the prior 12 but a single 4-week window swings hard, the swing is probably a
    # recurring bulk order landing on a different week, not real growth/decline.
    _ql = pd.Timedelta(weeks=13)
    q_recent_by = df[(df["document_date"] > qend - _ql) & (df["document_date"] <= qend)].groupby("account")[GV].sum()
    q_prior_by = df[(df["document_date"] > qend - _ql - ONE_YEAR) & (df["document_date"] <= qend - ONE_YEAR)].groupby("account")[GV].sum()
    # quarter-health gate is on PROFIT, shown to reps as a revenue "redline" (cover today's cost + hold last
    # year's quarter profit). Use RAW price/cost/profit (not GV) so a discounted new product doesn't distort
    # whether the account is actually shrinking. recent_rev < redline  <=>  recent_profit < floor x prior_profit.
    _rec = df[(df["document_date"] > qend - _ql) & (df["document_date"] <= qend)].groupby("account")
    _pri = df[(df["document_date"] > qend - _ql - ONE_YEAR) & (df["document_date"] <= qend - ONE_YEAR)].groupby("account")
    q_recent_rev_by = _rec["extended_price"].sum()
    q_recent_cost_by = _rec["extended_cost"].sum()
    q_recent_profit_by = _rec["line_profit"].sum()
    q_prior_profit_by = _pri["line_profit"].sum()
    # annual-net gate: an account earns growth only if it's genuinely up YEAR-OVER-YEAR on cost-adjusted
    # revenue. Growth is paid on a lumpy 4-week window, so a flat/declining account can otherwise harvest
    # its up-swings (the quarter-profit gate only blocks shrinking QUARTERS). Uses RAW revenue over 52 weeks.
    ann_recent_rev_by = df[(df["document_date"] > qend - ONE_YEAR) & (df["document_date"] <= qend)].groupby("account")["extended_price"].sum()
    ann_prior_rev_by = df[(df["document_date"] > qend - 2 * ONE_YEAR) & (df["document_date"] <= qend - ONE_YEAR)].groupby("account")["extended_price"].sum()
    # company quarter-over-quarter seasonal swing (for provisional accounts with no year-ago baseline)
    company_seasonal_factor = 1.0
    if account_prior_q.sum() and account_recent_q.sum():
        company_seasonal_factor = min(max(account_recent_q.sum() / account_prior_q.sum(), 0.5), 2.0)
    # size-band de-trend factor per account (typical move for accounts its size) — subsumes market + inflation.
    # Measured over a LONG window (size_band_window_weeks, default 52) vs the same window one year prior, so it
    # tracks the real year-over-year trend instead of a noisy 4-week slice (a single 4-week window can read
    # -16% in a year the book is +24%). Floored (size_band_floor, default 1.0) so a soft segment can never
    # DISCOUNT the bar below cost-adjusted last year; up-segments still lift it.
    sbw = pd.Timedelta(weeks=size_band_window_weeks)
    trend_recent = df[(df["document_date"] > qend - sbw) & (df["document_date"] <= qend)].groupby("account")[GV].sum()
    trend_base = _cost_adjusted_baseline(df, qend - sbw - ONE_YEAR, qend - ONE_YEAR, cost_factor)
    band_factor, overall_band_factor = _size_band_factors(trend_base, trend_recent, size_band_count, floor=size_band_floor)
    # glide: each account's own adaptive run-rate level (for activations with no usable year-ago window)
    glide_levels = _glide_levels(df, qend, glide_alpha, step_weeks=growth_window_weeks, value_col=GV)
    # cross-account seasonal lift for glide accounts: how accounts THIS size are moving this period vs their
    # own recent run-rate (median recent/glide per size band) — adds seasonality (holidays/CNY) without
    # leaning on a lumpy per-account same-weeks-last-year window. Restrict to accounts WITH current sales so
    # dormant accounts (recent=0) don't drag a band's median ratio to 0.
    glide_level_series = pd.Series({acc: lv for acc, lv in glide_levels.items()
                                    if account_recent_q.get(acc, 0.0) > 0}, dtype="float64")
    glide_band_factor, glide_overall_factor = _size_band_factors(glide_level_series, account_recent_q, size_band_count, floor=size_band_floor)

    BASELINE_MIN = 300.0          # window baseline needed to score growth (else line-items only)
    period_fraction = period_days / (growth_window_weeks * 7.0)   # prorate window outperformance to the period

    # rep x account trailing growth-value (for growth attribution / work-share) + current items
    team_recent_q = recent_q_df[recent_q_df["associate"].isin(sales_team)]
    rep_account_q = team_recent_q.groupby(["associate", "account"])[GV].sum().reset_index(name="rep_q")
    # rep x account SAME 4 weeks LAST YEAR (CNY-aligned base window) — the raw prior-year sales, for display
    prior_year_df = df[(df["document_date"] > base_lo) & (df["document_date"] <= base_hi) & df["associate"].isin(sales_team)]
    rep_last_year = prior_year_df.groupby(["associate", "account"])["extended_price"].sum()
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
        q = full_df[(full_df["account"] == acc) & (full_df["document_date"] > period_end - qwin) & (full_df["document_date"] <= period_end)]
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
        period_rev4 = float(account_recent_rev4.get(account_id, 0.0))
        period_profit4 = float(account_recent_profit4.get(account_id, 0.0))
        period_margin4 = (period_profit4 / period_rev4) if period_rev4 else 0.0
        acct_baseline = float(account_baseline_q.get(account_id, 0.0))
        acct_fraction = period_fraction
        work_share = rep_q / account_q if account_q else 0.0
        status = account_status(account_id)
        prior_q = float(account_prior_q.get(account_id, 0.0))
        # trailing-QUARTER context (for the quarter-health gate + the timing flag)
        q_rec = float(q_recent_by.get(account_id, 0.0)); q_pri = float(q_prior_by.get(account_id, 0.0))
        # profit-health gate, shown as a revenue redline (cover today's cost + hold last year's quarter profit)
        q_rec_rev = float(q_recent_rev_by.get(account_id, 0.0))
        q_rec_cost = float(q_recent_cost_by.get(account_id, 0.0))
        q_rec_profit = float(q_recent_profit_by.get(account_id, 0.0))
        q_pri_profit = float(q_prior_profit_by.get(account_id, 0.0))
        q_redline = q_rec_cost + growth_quarter_floor * q_pri_profit   # revenue needed at today's cost
        # annual-net gate: trailing-52wk revenue vs the cost-adjusted prior year
        ann_rec = float(ann_recent_rev_by.get(account_id, 0.0))
        ann_pri = float(ann_prior_rev_by.get(account_id, 0.0)) * cost_factor
        annual_gated = (ann_pri > growth_annual_min_prior) and (ann_rec < growth_annual_floor * ann_pri)
        gated = False
        gate_reason = ""

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
            # quarter-health gate: a 4-week pop on an account whose PROFIT is shrinking over the quarter doesn't
            # count as growth. If its trailing-13-week profit is below growth_quarter_floor x the same 13 weeks
            # last year (and it had a real prior-year quarter), drop it from growth entirely (neutral, not a
            # review). Shown to reps as a revenue redline: recent revenue below (today's cost + floor x prior
            # profit) == recent profit below floor x prior profit.
            quarter_gated = (q_pri_profit > growth_quarter_min_profit) and (q_rec_profit < growth_quarter_floor * q_pri_profit)
            # annual reality check: no growth on an account that isn't genuinely up year-over-year (a flat/
            # declining account can't earn growth just from a lumpy 4-week pop). Complementary to the quarter gate.
            gated = (quarter_gated or annual_gated) and rep_q > target_for_rep
            if gated:
                gate_reason = "qtr_profit" if quarter_gated else "annual_flat"
            if not gated:
                # jump review: an account that DOUBLED its NORMAL LEVEL (recent >= jump_multiple x normal) is the
                # anomaly itself — "Normal level" is the HIGHER of the account's recent run-rate and its
                # seasonally-adjusted year-ago bar. The over-bar amount is withheld for the manager to investigate;
                # ordinary growth pays through; released if the rep won it.
                jump_bar = max(target_for_rep, established * work_share)   # higher of seasonal year-ago bar and recent pace
                jump = target_for_rep > BASELINE_MIN and jump_bar > 0 and rep_q >= jump_multiple * jump_bar
                jump_ratio = round(rep_q / jump_bar, 1) if jump_bar > 0 else None
                # account-level "normal" for display (work_share cancels, so account_q/jump_bar_acct == jump_ratio)
                jump_bar = jump_bar / work_share if work_share else jump_bar
                if jump and not released:
                    effective_recent = target_for_rep           # withhold ALL over-bar growth pending review
                    windfall = held = rep_q - target_for_rep
                else:
                    effective_recent, held = rep_q, 0.0         # pay in full (normal overage, or released)
                    windfall = max(0.0, rep_q - target_for_rep) if jump else 0.0
                # accumulate as PERIOD-equivalents (x acct_fraction): regular x1, annual x period_days/364
                t["growth_base_raw"] += raw_for_rep * acct_fraction
                t["growth_base"] += base_for_rep * acct_fraction
                t["growth_target"] += target_for_rep * acct_fraction
                t["growth_actual"] += effective_recent * acct_fraction
                t["held_back"] += held * acct_fraction
                t["flagged"] += int(jump and not released)
        # likely order-TIMING shift: last 12 months flat YoY, but this 4-week window swings far from the
        # quarter's own 4-week pace -> a recurring bulk order probably landed on a different week (not growth).
        q_pace4 = q_rec * 4.0 / 13.0
        quarter_flat = q_pri > 3000.0 and 0.75 <= (q_rec / q_pri) <= 1.33
        timing = bool(quarter_flat and q_pace4 > 0 and (account_q > 1.5 * q_pace4 or account_q < 0.6 * q_pace4))
        account_rows.append(dict(associate=rep, account=account_id, status=status,
                                 rep_quarter_sales=rep_q, baseline_quarter=raw_for_rep,
                                 account_target=target_for_rep, capped=jump, held_back=round(held),
                                 windfall=round(windfall if raw_for_rep is not None else 0.0),
                                 released=released, account_recent=round(account_q),
                                 last_year=round(float(rep_last_year.get((rep, account_id), 0.0))),
                                 period_profit=round(period_profit4), period_margin=round(period_margin4 * 100, 1),
                                 established=round(established),
                                 jump_bar=(round(jump_bar) if jump_bar is not None else None),
                                 jump_ratio=jump_ratio,
                                 q_recent=round(q_rec), q_prior=round(q_pri), timing=timing, gated=gated,
                                 gate_reason=gate_reason,
                                 q_recent_rev=round(q_rec_rev), q_redline=round(q_redline),
                                 q_recent_profit=round(q_rec_profit), q_prior_profit=round(q_pri_profit),
                                 ann_recent_rev=round(ann_rec), ann_prior_rev=round(ann_pri),
                                 new_account=bool((period_end - first_seen.get(account_id, period_end)).days < 364)))

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
                          size_band_count=5, size_band_window_weeks=52, size_band_floor=0.0,
                          growth_payout_rate=0.01,
                          sporadic_gap_weeks=4, cost_inflation_weeks=13,
                          featured_new_products=frozenset(), new_product_weeks=26,
                          new_product_attribution=0.20, substitute_products=frozenset(),
                          substitute_attribution=0.60, **_ignore):
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
    substitute_products = set(substitute_products)
    GV = "extended_price"
    if featured_new_products or substitute_products:
        item_first = df.groupby("item_number")["document_date"].min()
        within = (as_of - df["item_number"].map(item_first)) <= pd.Timedelta(weeks=new_product_weeks)
        attr_by_item = {**{it: new_product_attribution for it in featured_new_products},
                        **{it: substitute_attribution for it in substitute_products}}   # substitute wins on overlap
        attr = df["item_number"].map(attr_by_item).fillna(1.0)
        df = df.assign(growth_value=np.where(within, df["extended_price"] * attr, df["extended_price"]))
        GV = "growth_value"

    # sporadic = median order-gap longer than the 4-week measurement window
    _dd = df.assign(_d=df["document_date"].dt.normalize()).drop_duplicates(["account", "_d"]).sort_values(["account", "_d"])
    _dd["_gap"] = _dd.groupby("account")["_d"].diff().dt.days
    _gb = _dd.groupby("account")["_gap"]
    _cut = sporadic_gap_weeks * 7   # sporadic if MEDIAN or MEAN order gap >= 4 weeks (mean catches burst-then-dormant accounts)
    sporadic = set(_gb.median()[lambda s: s >= _cut].index) | set(_gb.mean()[lambda s: s >= _cut].index)
    if not sporadic:
        return empty

    year = pd.Timedelta(weeks=52)
    ci = pd.Timedelta(weeks=cost_inflation_weeks)
    cost_factor = _cost_inflation_factor(df, as_of - ci, as_of, as_of - ci - ONE_YEAR, as_of - ONE_YEAR)
    annual_recent = df[(df["document_date"] > as_of - year) & (df["document_date"] <= as_of)].groupby("account")[GV].sum()
    annual_baseline = _cost_adjusted_baseline(df, as_of - 2 * year, as_of - year, cost_factor)
    band_factor, overall_band = _size_band_factors(annual_baseline, annual_recent, size_band_count, floor=size_band_floor)
    # raw (un-adjusted) account revenue, trailing 12 months vs the prior 12 months -> shown to reps as plain YoY
    raw_recent = df[(df["document_date"] > as_of - year) & (df["document_date"] <= as_of)].groupby("account")["extended_price"].sum()
    raw_prior = df[(df["document_date"] > as_of - 2 * year) & (df["document_date"] <= as_of - year)].groupby("account")["extended_price"].sum()

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
        rr = float(raw_recent.get(account_id, 0.0)); rp = float(raw_prior.get(account_id, 0.0))
        yoy = round((rr / rp - 1) * 100) if rp > 0 else None      # plain account-level year-over-year (shown to reps)
        account_rows.append(dict(associate=rep, account=account_id, status=status,
                                 sales=round(rep_q), target=(round(target) if target is not None else None),
                                 perf=(round(perf) if perf is not None else None),
                                 acct12=round(rr), prior12=round(rp), yoy=yoy))

    cards = []
    for rep in sales_team:
        t = rep_totals[rep]
        bonus = max(0.0, t["actual"] - t["target"]) * growth_payout_rate
        cards.append(dict(associate=rep, annual_accounts=int(t["accounts"]),
                          annual_actual=t["actual"], annual_target=t["target"],
                          annual_growth_bonus=bonus))
    return dict(scorecards=pd.DataFrame(cards), accounts=pd.DataFrame(account_rows))
