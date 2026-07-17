"""Behavioral tests for compute_cumulative_growth — the 'what-if' cumulative profit-growth model.

Key properties: measured PER ACCOUNT vs the same account a year ago (so taking over a flat account pays ~$0),
young accounts (<12mo history) credited at a flat % of profit, and the monthly true-up self-corrects (a down
month pays $0, total paid never exceeds the peak earned).
"""
import pandas as pd
import pytest

from app import engine
from builders import mk, df_from

AS_OF = pd.Timestamp("2026-06-30")
FISCAL = pd.Timestamp("2025-08-01")       # August start
HIST = pd.Timestamp("2023-08-01")          # 2yr history -> year-ago window exists, accounts are "mature"
DAY = pd.Timedelta(days=1)
RATE = 0.01


def run(df, team, rate=RATE, young_pct=0.01):
    return engine.compute_cumulative_growth(df, FISCAL, AS_OF, team, cumulative_rate=rate,
                                            young_account_pct=young_pct, young_account_months=12)


def cum(res, rep):
    r = res["reps"]
    row = r[r["associate"] == rep]
    return float(row.iloc[0]["cum_growth"]) if len(row) else 0.0


def acct(res, account):
    a = res["accounts"]
    m = a[a["account"] == account]
    return m.iloc[0].to_dict() if len(m) else None


def test_flat_account_earns_nothing():
    # same weekly profit across both years -> this cycle ~= a year ago -> ~$0 growth
    lines = mk("FLAT", "Rep A", "X", HIST, AS_OF, 7, 100, 90)   # $10 profit/wk, steady
    res = run(df_from(lines), ["Rep A"])
    assert abs(cum(res, "Rep A")) < 45          # within a month's noise of zero


def test_grown_account_pays_rate_times_delta():
    # $5/wk profit last year -> $15/wk this cycle: a real, sustained increase
    lines = (mk("G", "Rep A", "X", HIST, FISCAL - DAY, 7, 105, 100)
             + mk("G", "Rep A", "X", FISCAL, AS_OF, 7, 115, 100))
    res = run(df_from(lines), ["Rep A"])
    a = acct(res, "G")
    assert not a["is_young"]
    assert a["growth"] == pytest.approx(a["ty_profit"] - a["ly_profit"])   # mature = YoY delta
    assert a["growth"] > 300 and cum(res, "Rep A") > 300
    r = res["reps"]; row = r[r["associate"] == "Rep A"].iloc[0]
    assert row["earned"] == pytest.approx(RATE * row["cum_growth"])


def test_taken_over_flat_account_kills_the_phantom():
    # account X: Rep A held it last year, Rep B this cycle, SAME volume. B inherits a flat account -> ~$0.
    lines = (mk("X", "Rep A", "I", HIST, FISCAL - DAY, 7, 100, 90)
             + mk("X", "Rep B", "I", FISCAL, AS_OF, 7, 100, 90))
    res = run(df_from(lines), ["Rep A", "Rep B"])
    assert acct(res, "X")["holder"] == "Rep B"          # B is the current holder
    assert abs(cum(res, "Rep B")) < 45                  # but earns ~nothing for the reshuffle
    assert cum(res, "Rep A") == pytest.approx(0.0)      # A holds nothing this cycle


def test_young_account_credited_at_flat_pct_not_yoy():
    # first sold Jan 2026 (<12mo before as_of) -> growth = young_pct x its profit, NOT a YoY delta
    lines = mk("NEW", "Rep A", "I", pd.Timestamp("2026-01-05"), AS_OF, 7, 100, 80)  # $20 profit/wk
    res = run(df_from(lines), ["Rep A"], young_pct=0.05)
    a = acct(res, "NEW")
    assert a["is_young"]
    assert a["growth"] == pytest.approx(0.05 * a["ty_profit"])
    assert cum(res, "Rep A") == pytest.approx(a["growth"])


def test_trueup_self_corrects_on_a_down_month():
    # up Aug->May ($20/wk vs $10/wk a year ago), then NOTHING in June -> June cumulative dips -> pay pauses
    lines = (mk("D", "Rep A", "I", HIST, FISCAL - DAY, 7, 110, 100)                    # $10/wk incl last year
             + mk("D", "Rep A", "I", FISCAL, pd.Timestamp("2026-05-31"), 7, 120, 100))  # $20/wk, none in June
    res = run(df_from(lines), ["Rep A"])
    traj = res["trajectory"]["Rep A"]
    june = traj[-1]
    assert june["month"].endswith("-06")
    assert june["mo_growth"] < 0 and june["pay"] == pytest.approx(0.0)   # down month -> $0, no clawback
    assert all(row["pay"] >= 0 for row in traj)
    paid_total = sum(row["pay"] for row in traj)
    peak = max(row["earned"] for row in traj)
    assert paid_total == pytest.approx(max(0.0, peak))                    # never overpays; captures the peak
