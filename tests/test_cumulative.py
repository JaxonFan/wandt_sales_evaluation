"""Behavioral tests for compute_cumulative_growth — the 'what-if' cumulative profit-growth model.

Model: per account, raw cumulative profit gap vs the same account a year ago (no per-account floor), split by
work-share, then NETTED WITHIN EACH REP'S BOOK — declines offset growers. Progressive true-up at the REP level:
each month pays rate x new ground above the rep's running net peak (a down month pays $0, never clawed back).
A brand-new account has no year-ago, so its gap = its own profit. A rep's earned = rate x max(0, net peak).
"""
import pandas as pd
import pytest

from app import engine
from builders import mk, df_from

AS_OF = pd.Timestamp("2026-06-30")
FISCAL = pd.Timestamp("2025-08-01")       # explicit cycle anchor (independent of the config default)
HIST = pd.Timestamp("2023-08-01")          # 2yr history -> year-ago window exists, accounts are "mature"
DAY = pd.Timedelta(days=1)
RATE = 0.05


def run(df, team, rate=RATE):
    return engine.compute_cumulative_growth(df, FISCAL, AS_OF, team, cumulative_rate=rate,
                                            young_account_pct=rate, young_account_months=12)


def rep_row(res, rep):
    r = res["reps"]; row = r[r["associate"] == rep]
    return row.iloc[0].to_dict() if len(row) else dict(cum_growth=0.0, earned=0.0)


def acct(res, account):
    a = res["accounts"]; m = a[a["account"] == account]
    return m.iloc[0].to_dict() if len(m) else None


def test_flat_account_earns_nothing():
    lines = mk("FLAT", "Rep A", "X", HIST, AS_OF, 7, 100, 90)   # $10 profit/wk, steady both years
    res = run(df_from(lines), ["Rep A"])
    assert abs(rep_row(res, "Rep A")["cum_growth"]) < 45


def test_grown_account_pays_rate_times_net():
    # $5/wk last year -> $15/wk this cycle: sustained increase, net gap climbs monotonically (peak = final)
    lines = (mk("G", "Rep A", "X", HIST, FISCAL - DAY, 7, 105, 100)
             + mk("G", "Rep A", "X", FISCAL, AS_OF, 7, 115, 100))
    res = run(df_from(lines), ["Rep A"])
    a = acct(res, "G")
    assert not a["is_young"]
    assert a["growth"] == pytest.approx(a["ty_profit"] - a["ly_profit"])   # raw gap
    assert a["growth"] > 300
    assert rep_row(res, "Rep A")["earned"] == pytest.approx(RATE * a["growth"])


def test_taken_over_flat_account_kills_the_phantom():
    # full history exists (as after the full-data import): B inherits a flat account -> ~$0 net
    lines = (mk("X", "Rep A", "I", HIST, FISCAL - DAY, 7, 100, 90)          # A held it last year
             + mk("X", "Rep B", "I", FISCAL, AS_OF, 7, 100, 90))            # B holds it now, same volume
    res = run(df_from(lines), ["Rep A", "Rep B"])
    assert acct(res, "X")["holder"] == "Rep B"
    assert abs(rep_row(res, "Rep B")["cum_growth"]) < 45
    assert rep_row(res, "Rep A")["earned"] == pytest.approx(0.0)


def test_untracked_history_builds_the_baseline():
    # last year's seller isn't on the team (e.g. MT, associate outside `team`): the account's history still
    # forms the baseline, so the tracked rep who inherits it flat earns ~$0 (no phantom growth).
    lines = (mk("X", "Old Timer", "I", HIST, FISCAL - DAY, 7, 100, 90)      # untracked history
             + mk("X", "Rep B", "I", FISCAL, AS_OF, 7, 100, 90))            # tracked rep now, same volume
    res = run(df_from(lines), ["Rep B"])                                     # team excludes Old Timer
    assert abs(rep_row(res, "Rep B")["cum_growth"]) < 45


def test_new_account_gap_is_its_whole_profit():
    lines = mk("NEW", "Rep A", "I", pd.Timestamp("2026-01-05"), AS_OF, 7, 100, 80)  # $20 profit/wk
    res = run(df_from(lines), ["Rep A"])
    a = acct(res, "NEW")
    assert a["is_young"]
    assert a["growth"] == pytest.approx(a["ty_profit"])
    assert rep_row(res, "Rep A")["earned"] == pytest.approx(RATE * a["ty_profit"])


def test_rep_level_netting_declines_offset_growers():
    # same rep holds a grower (+$10/wk) and an equal shrinker (-$10/wk): net ~0 -> earned ~0.
    gro = (mk("GRO", "Rep A", "X", HIST, FISCAL - DAY, 7, 105, 100)
           + mk("GRO", "Rep A", "X", FISCAL, AS_OF, 7, 115, 100))          # $5 -> $15/wk
    shr = (mk("SHR", "Rep A", "Y", HIST, FISCAL - DAY, 7, 115, 100)
           + mk("SHR", "Rep A", "Y", FISCAL, AS_OF, 7, 105, 100))          # $15 -> $5/wk
    res = run(df_from(gro + shr), ["Rep A"])
    assert acct(res, "GRO")["growth"] > 0
    assert acct(res, "SHR")["growth"] < 0                                   # raw, signable
    assert abs(rep_row(res, "Rep A")["cum_growth"]) < 45                    # nets to ~0
    assert rep_row(res, "Rep A")["earned"] < RATE * acct(res, "GRO")["growth"] * 0.2   # ~nothing vs grower alone


def test_trueup_pays_the_net_peak_and_pauses_after():
    # book up hard Aug-Nov, then well below last year Dec-Jun -> net peaks, then declines; pay = 5% of peak
    lines = (mk("PK", "Rep A", "I", HIST, FISCAL - DAY, 7, 110, 100)                     # $10/wk incl last year
             + mk("PK", "Rep A", "I", FISCAL, pd.Timestamp("2025-11-30"), 7, 130, 100)   # $30/wk Aug-Nov
             + mk("PK", "Rep A", "I", pd.Timestamp("2025-12-01"), AS_OF, 7, 102, 100))   # $2/wk Dec-Jun (down)
    res = run(df_from(lines), ["Rep A"])
    traj = res["trajectory"]["Rep A"]
    r = rep_row(res, "Rep A")
    peak = max(row["cum_growth"] for row in traj)
    assert peak > r["cum_growth"]                                # peaked above the (lower) final net gap
    assert r["earned"] == pytest.approx(RATE * peak)             # paid on the peak
    assert sum(row["pay"] for row in traj) == pytest.approx(r["earned"])
    assert traj[-1]["pay"] == pytest.approx(0.0)                 # down month adds $0 (no clawback)
    assert all(row["pay"] >= 0 for row in traj)
    assert [row["cum_pay"] for row in traj] == sorted(row["cum_pay"] for row in traj)


def test_net_down_book_pays_zero():
    # a book that's below last year the whole cycle earns $0 (never negative pay)
    shr = (mk("SHR", "Rep A", "Y", HIST, FISCAL - DAY, 7, 115, 100)
           + mk("SHR", "Rep A", "Y", FISCAL, AS_OF, 7, 105, 100))          # $15 -> $5/wk
    res = run(df_from(shr), ["Rep A"])
    r = rep_row(res, "Rep A")
    assert r["cum_growth"] < 0
    assert r["earned"] == pytest.approx(0.0)
    assert all(row["pay"] == pytest.approx(0.0) for row in res["trajectory"]["Rep A"])
