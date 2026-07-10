"""Behavioral tests for compute_period_bonus — pin the intended logic of each bonus piece and gate.

Each test builds a `flat_book` (a market bar of ~1.0) plus one or two shaped accounts, then asserts
invariants/relationships rather than brittle exact dollars.
"""
import pandas as pd
import pytest

from app import engine
from builders import (AS_OF, HISTORY_START, PERIOD_START, TEAM, acct_row, card,
                      df_from, dials, flat_book, mk, run_period)

WK = pd.Timedelta(weeks=1)
DAY = pd.Timedelta(days=1)


def _flats():
    return flat_book(n=12)


# --- 7. Contribution -------------------------------------------------------------------------------
def test_contribution_is_line_items_times_rate():
    # 10 lines inside the current period on a brand-new account: it earns contribution regardless of status.
    lines = mk("C", "Rep A", "X", PERIOD_START + DAY, PERIOD_START + 10 * DAY, 1, 100, 80)
    res = run_period(df_from(lines))
    c = card(res)
    assert c["items_placed"] == 10
    assert c["contribution_bonus"] == pytest.approx(10 * dials()["item_rate"])


# --- 8. Growth pays above target (mature) ----------------------------------------------------------
def test_growth_pays_above_target_and_flat_pays_zero():
    # mild grower (recent ~1.2x its own year-ago) stays on the mature YoY path and earns growth.
    grow = (mk("GROW", "Rep A", "X", HISTORY_START, AS_OF - 4 * WK, 7, 1000, 850)
            + mk("GROW", "Rep A", "X", AS_OF - 4 * WK + DAY, AS_OF, 7, 1200, 1020))
    res = run_period(df_from(_flats(), grow))
    r = acct_row(res, "GROW")
    assert r["status"] == "mature" and not r["gated"]
    assert card(res)["growth_bonus"] > 0
    # a purely flat book (every account at its own year-ago) earns ~no growth
    assert card(run_period(df_from(_flats())))["growth_bonus"] == pytest.approx(0.0, abs=1e-6)


# --- 9. Growth is floored at zero ------------------------------------------------------------------
def test_growth_never_negative():
    decline = (mk("DEC", "Rep A", "X", HISTORY_START, AS_OF - 4 * WK, 7, 1000, 850)
               + mk("DEC", "Rep A", "X", AS_OF - 4 * WK + DAY, AS_OF, 7, 500, 425))  # recent well below bar
    res = run_period(df_from(_flats(), decline))
    assert card(res)["growth_bonus"] == pytest.approx(0.0, abs=1e-6)


# --- 10. Big-jump windfall withheld, released on confirmation --------------------------------------
def test_big_jump_withheld_then_released():
    jump = (mk("JMP", "Rep A", "X", HISTORY_START, AS_OF - 4 * WK, 7, 1000, 850)
            + mk("JMP", "Rep A", "X", AS_OF - 4 * WK + DAY, AS_OF, 7, 3000, 2550))  # 3x pop
    df = df_from(_flats(), jump)
    held = run_period(df)
    r = acct_row(held, "JMP")
    assert r["capped"] is True and card(held)["flagged"] >= 1
    released = run_period(df, jump_released=["JMP"])
    assert card(released)["growth_bonus"] > card(held)["growth_bonus"]  # releasing pays the windfall


# --- 11. Quarter-health gate is on PROFIT ----------------------------------------------------------
def test_quarter_profit_gate_blocks_low_margin_pop():
    # revenue pops in the last 4 weeks, but the trailing-quarter PROFIT is crushed vs a year ago -> gated.
    q13 = AS_OF - pd.Timedelta(weeks=13)
    margin = (mk("MRG", "Rep A", "X", HISTORY_START, q13, 7, 1000, 700)          # last yr: healthy margin
              + mk("MRG", "Rep A", "X", q13 + DAY, AS_OF - 4 * WK, 7, 1000, 950)  # recent quarter: margin crushed
              + mk("MRG", "Rep A", "X", AS_OF - 4 * WK + DAY, AS_OF, 7, 1500, 1425))  # recent 4wk revenue pop
    res = run_period(df_from(_flats(), margin))
    r = acct_row(res, "MRG")
    assert r["gated"] is True and r["gate_reason"] == "qtr_profit"


# --- 12. Annual reality-check gate is on REVENUE, min-prior spares small accounts ------------------
def test_annual_gate_blocks_flat_year_but_spares_small():
    yr, q, w4 = pd.Timedelta(weeks=52), pd.Timedelta(weeks=13), 4 * WK
    # Down over the YEAR (the dip is in the middle of the recent year), but the recent QUARTER recovered and
    # the last 4 weeks pop above the year-ago bar. So: annual-gated, NOT quarter-gated, and over target.
    # margins held constant (15%) so the quarter gate tracks revenue and stays open.
    def book(name, s):
        return (mk(name, "Rep A", "X", HISTORY_START, AS_OF - yr, 7, 1200 * s, 1020 * s)       # prior year high
                + mk(name, "Rep A", "X", AS_OF - yr + DAY, AS_OF - q, 7, 800 * s, 680 * s)      # mid dip
                + mk(name, "Rep A", "X", AS_OF - q + DAY, AS_OF - w4, 7, 1300 * s, 1105 * s)     # recent recovery
                + mk(name, "Rep A", "X", AS_OF - w4 + DAY, AS_OF, 7, 1440 * s, 1224 * s))        # 4wk pop
    big = book("YOY", 1.0)          # prior-year rev ~ $62k  >= growth_annual_min_prior -> gated
    small = book("TINY", 0.25)      # prior-year rev ~ $15.6k < growth_annual_min_prior -> spared
    res = run_period(df_from(_flats(), big, small))
    assert acct_row(res, "YOY")["gate_reason"] == "annual_flat"
    assert acct_row(res, "TINY")["gate_reason"] != "annual_flat"


# --- 13. New-product attribution ramps 20% -> 100% by age -----------------------------------------
def test_new_product_ramp_discounts_young_items_only():
    # established base account that also just started selling item "NEW" in the last 4 weeks.
    base = mk("NP", "Rep A", "OLD_BASE", HISTORY_START, AS_OF, 7, 1000, 850)
    new = mk("NP", "Rep A", "NEW", AS_OF - 4 * WK + DAY, AS_OF, 7, 300, 255)   # first sold ~4 wks ago
    df = df_from(_flats(), base, new)
    g_plain = card(run_period(df))["growth_bonus"]
    g_featured = card(run_period(df, featured_new_products=["NEW"]))["growth_bonus"]
    assert g_featured < g_plain            # a young featured item counts at a discount -> less growth
    # a fully-aged item (first sold > new_product_weeks ago) featured -> factor 1.0 -> no change
    g_old_featured = card(run_period(df, featured_new_products=["OLD_BASE"]))["growth_bonus"]
    assert g_old_featured == pytest.approx(g_plain, abs=1e-6)


# --- 14. Constrained items are excluded from GROWTH only (not contribution) ------------------------
def test_constrained_is_growth_only():
    base = mk("NP", "Rep A", "OLD_BASE", HISTORY_START, AS_OF, 7, 1000, 850)
    new = mk("NP", "Rep A", "NEW", AS_OF - 4 * WK + DAY, AS_OF, 7, 300, 255)
    df = df_from(_flats(), base, new)
    plain = card(run_period(df))
    constrained = card(run_period(df, constrained_item_numbers=["NEW"]))
    assert constrained["items_placed"] == plain["items_placed"]          # contribution untouched
    assert constrained["growth_bonus"] < plain["growth_bonus"]           # removed from growth comparison


# --- 15. Acquisition = flat tier, only when self-acquired & at the quarter mark --------------------
def test_acquisition_flat_tier_and_assigned_pays_nothing():
    seen = AS_OF - pd.Timedelta(days=70)          # ~2.5 periods old -> inside the [56,84) pay window
    acq = mk("ACQ", "Rep A", "X", seen, AS_OF, 7, 250, 210)   # ~small annualized size
    df = df_from(_flats(), acq)
    self_acq = card(run_period(df, self_acquired=["ACQ"]))
    assigned = card(run_period(df))                            # not confirmed -> no landing bonus
    assert self_acq["acquisition_bonus"] == pytest.approx(dials()["acq_flat_small"])
    assert assigned["acquisition_bonus"] == pytest.approx(0.0)


# --- 16. Exempt removes an account from growth but keeps its line items ----------------------------
def test_exempt_removes_growth_keeps_items():
    grow = (mk("GROW", "Rep A", "X", HISTORY_START, AS_OF - 4 * WK, 7, 1000, 850)
            + mk("GROW", "Rep A", "X", AS_OF - 4 * WK + DAY, AS_OF, 7, 1200, 1020))
    df = df_from(_flats(), grow)
    normal = card(run_period(df))
    exempt = card(run_period(df, exempt_accounts=["GROW"]))
    assert exempt["growth_bonus"] < normal["growth_bonus"]
    assert exempt["items_placed"] == normal["items_placed"]


# --- 17. Sparse accounts leave the period engine and are scored on the annual track ---------------
def test_sparse_account_goes_to_annual_track():
    sparse = mk("SPR", "Rep A", "X", HISTORY_START, AS_OF, 42, 4000, 3400)   # orders every 6 weeks
    df = df_from(_flats(), sparse)
    period_res = run_period(df)
    assert acct_row(period_res, "SPR") is None                              # excluded from per-period growth
    annual = engine.compute_annual_review(df, AS_OF, TEAM, **dials())
    a = annual["accounts"]
    assert "SPR" in set(a["account"])                                       # scored on the annual track
