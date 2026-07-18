"""Behavioral tests for compute_cumulative_growth — the 'what-if' cumulative profit-growth model.

Model: per ACCOUNT, cumulative profit this cycle vs the same account a year ago, progressive true-up (pay only
new ground above the prior peak — a down month adds $0, never clawed back), split by work-share, at 1%.
A brand-new account has no year-ago, so its whole profit is the gap. A rep's payable is never negative.
"""
import pandas as pd
import pytest

from app import engine
from builders import mk, df_from

AS_OF = pd.Timestamp("2026-06-30")
FISCAL = pd.Timestamp("2025-08-01")       # explicit cycle anchor (independent of the config default)
HIST = pd.Timestamp("2023-08-01")          # 2yr history -> year-ago window exists, accounts are "mature"
DAY = pd.Timedelta(days=1)
RATE = 0.01


def run(df, team, rate=RATE):
    return engine.compute_cumulative_growth(df, FISCAL, AS_OF, team, cumulative_rate=rate,
                                            young_account_pct=rate, young_account_months=12)


def cum(res, rep):
    r = res["reps"]; row = r[r["associate"] == rep]
    return float(row.iloc[0]["cum_growth"]) if len(row) else 0.0


def earned(res, rep):
    r = res["reps"]; row = r[r["associate"] == rep]
    return float(row.iloc[0]["earned"]) if len(row) else 0.0


def acct(res, account):
    a = res["accounts"]; m = a[a["account"] == account]
    return m.iloc[0].to_dict() if len(m) else None


def test_flat_account_earns_nothing():
    lines = mk("FLAT", "Rep A", "X", HIST, AS_OF, 7, 100, 90)   # $10 profit/wk, steady both years
    res = run(df_from(lines), ["Rep A"])
    assert cum(res, "Rep A") < 150                              # peak gap ~ zero vs a grower's hundreds


def test_grown_account_pays_rate_times_peak():
    # $5/wk last year -> $15/wk this cycle: a sustained increase, so the gap climbs monotonically (peak = final)
    lines = (mk("G", "Rep A", "X", HIST, FISCAL - DAY, 7, 105, 100)
             + mk("G", "Rep A", "X", FISCAL, AS_OF, 7, 115, 100))
    res = run(df_from(lines), ["Rep A"])
    a = acct(res, "G")
    assert not a["is_young"]
    assert a["growth"] == pytest.approx(a["ty_profit"] - a["ly_profit"])   # monotonic grower: peak == final gap
    assert a["growth"] > 300
    assert earned(res, "Rep A") == pytest.approx(RATE * a["growth"])


def test_taken_over_flat_account_kills_the_phantom():
    lines = (mk("X", "Rep A", "I", HIST, FISCAL - DAY, 7, 100, 90)          # A held it last year
             + mk("X", "Rep B", "I", FISCAL, AS_OF, 7, 100, 90))            # B holds it now, same volume
    res = run(df_from(lines), ["Rep A", "Rep B"])
    assert acct(res, "X")["holder"] == "Rep B"
    assert cum(res, "Rep B") < 150                              # inherited flat -> ~$0
    assert earned(res, "Rep A") == pytest.approx(0.0)           # A sells nothing this cycle


def test_new_account_gap_equals_its_whole_profit():
    # first sold Jan 2026 (<12mo) -> no year-ago -> the gap is its own cumulative profit -> earns 1% of it
    lines = mk("NEW", "Rep A", "I", pd.Timestamp("2026-01-05"), AS_OF, 7, 100, 80)  # $20 profit/wk
    res = run(df_from(lines), ["Rep A"])
    a = acct(res, "NEW")
    assert a["is_young"]
    assert a["growth"] == pytest.approx(a["ty_profit"])         # whole profit is the increment
    assert earned(res, "Rep A") == pytest.approx(RATE * a["ty_profit"])   # = 1% of profit


def test_trueup_keeps_the_peak_and_pauses_on_a_down_month():
    # up hard Aug-Nov ($30/wk vs $10/wk a year ago), then well below ($2/wk) Dec-Jun -> gap peaks, then declines
    lines = (mk("PK", "Rep A", "I", HIST, FISCAL - DAY, 7, 110, 100)                     # $10/wk incl last year
             + mk("PK", "Rep A", "I", FISCAL, pd.Timestamp("2025-11-30"), 7, 130, 100)   # $30/wk Aug-Nov
             + mk("PK", "Rep A", "I", pd.Timestamp("2025-12-01"), AS_OF, 7, 102, 100))   # $2/wk Dec-Jun (down)
    res = run(df_from(lines), ["Rep A"])
    a = acct(res, "PK")
    traj = res["trajectory"]["Rep A"]
    assert a["growth"] > (a["ty_profit"] - a["ly_profit"])      # credited on the PEAK, above the (lower) final gap
    assert sum(r["pay"] for r in traj) == pytest.approx(RATE * a["growth"])   # total paid = 1% of the peak
    assert traj[-1]["pay"] == pytest.approx(0.0)               # a down month adds $0 (no clawback)
    assert [r["cum_pay"] for r in traj] == sorted(r["cum_pay"] for r in traj)  # pay only ever accrues


def test_shrinking_account_never_drags_a_rep_negative():
    # Rep A holds one grower and one shrinker; the shrinker contributes $0, not a negative
    gro = (mk("GRO", "Rep A", "X", HIST, FISCAL - DAY, 7, 105, 100)
           + mk("GRO", "Rep A", "X", FISCAL, AS_OF, 7, 115, 100))          # $5 -> $15/wk
    shr = (mk("SHR", "Rep A", "Y", HIST, FISCAL - DAY, 7, 115, 100)
           + mk("SHR", "Rep A", "Y", FISCAL, AS_OF, 7, 105, 100))          # $15 -> $5/wk
    res = run(df_from(gro + shr), ["Rep A"])
    assert acct(res, "GRO")["growth"] > 0
    assert acct(res, "SHR")["growth"] == pytest.approx(0.0)     # peak gap <= 0 -> floored
    assert cum(res, "Rep A") == pytest.approx(acct(res, "GRO")["growth"])   # only the grower counts
    assert earned(res, "Rep A") > 0
