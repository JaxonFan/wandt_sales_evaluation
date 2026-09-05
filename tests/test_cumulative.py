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


def test_constrained_item_excluded_from_both_years():
    # base flat book + item "SHORT" that the company couldn't supply this year (sold last year only):
    # unconstrained, that reads as a decline; constrained, it's removed from BOTH years -> ~flat, apples-to-apples.
    base = mk("A", "Rep A", "BASE", HIST, AS_OF, 7, 100, 90)
    short_ly = mk("A", "Rep A", "SHORT", HIST, FISCAL - DAY, 7, 60, 40)     # $20/wk profit, stops at cycle start
    df = df_from(base + short_ly)
    plain = engine.compute_cumulative_growth(df, FISCAL, AS_OF, ["Rep A"], cumulative_rate=RATE,
                                             young_account_pct=RATE)
    fixed = engine.compute_cumulative_growth(df, FISCAL, AS_OF, ["Rep A"], cumulative_rate=RATE,
                                             young_account_pct=RATE, constrained_item_numbers=["SHORT"])
    assert rep_row(plain, "Rep A")["cum_growth"] < -700       # unconstrained: charged the missing item
    assert abs(rep_row(fixed, "Rep A")["cum_growth"]) < 45    # constrained: symmetric removal -> ~flat


def test_book_columns_subtract_to_the_gap():
    # trajectory ty_book/ly_book must satisfy: ty - ly == the month's net-gap step (display consistency)
    lines = (mk("G", "Rep A", "X", HIST, FISCAL - DAY, 7, 105, 100)
             + mk("G", "Rep A", "X", FISCAL, AS_OF, 7, 115, 100))
    res = run(df_from(lines), ["Rep A"])
    traj = res["trajectory"]["Rep A"]
    prev = 0.0
    for t in traj:
        assert t["ty_book"] - t["ly_book"] == pytest.approx(t["cum_growth"] - prev, abs=1e-6)
        prev = t["cum_growth"]


def test_tiered_pay_marginal_above_target():
    # strong grower ($5/wk -> $45/wk) so growth far exceeds the target; base 5% up to target, 7.5% above.
    lines = (mk("G", "Rep A", "X", HIST, FISCAL - DAY, 7, 105, 100)
             + mk("G", "Rep A", "X", FISCAL, AS_OF, 7, 145, 100))
    df = df_from(lines)
    res = engine.compute_cumulative_growth(df, FISCAL, AS_OF, ["Rep A"], cumulative_rate=0.05,
                                           accel_rate=0.075, rep_targets={"Rep A": 0.5}, young_account_pct=0.05)
    row = rep_row(res, "Rep A")
    traj = res["trajectory"]["Rep A"]
    G = row["cum_growth"]                                       # monotonic grower -> peak == final
    T = 0.5 * sum(t["ly_book"] for t in traj)                   # target$ = 50% of the last-year book
    assert G > T > 0
    assert row["earned"] == pytest.approx(0.05 * T + 0.075 * (G - T), rel=1e-4)   # marginal tiers
    # flat (no target) pays base x G, which is strictly less than the accelerated amount
    flat = engine.compute_cumulative_growth(df, FISCAL, AS_OF, ["Rep A"], cumulative_rate=0.05,
                                            young_account_pct=0.05)
    assert rep_row(flat, "Rep A")["earned"] == pytest.approx(0.05 * G, rel=1e-4)
    assert row["earned"] > rep_row(flat, "Rep A")["earned"]


def test_growth_below_target_is_just_base_rate():
    lines = (mk("G", "Rep A", "X", HIST, FISCAL - DAY, 7, 105, 100)
             + mk("G", "Rep A", "X", FISCAL, AS_OF, 7, 108, 100))    # tiny growth, well under a big target
    res = engine.compute_cumulative_growth(df_from(lines), FISCAL, AS_OF, ["Rep A"], cumulative_rate=0.05,
                                           accel_rate=0.075, rep_targets={"Rep A": 5.0}, young_account_pct=0.05)
    row = rep_row(res, "Rep A")
    assert row["earned"] == pytest.approx(0.05 * row["cum_growth"], rel=1e-4)   # all in the base tier


def test_exempt_account_dropped_from_growth():
    keep = (mk("KEEP", "Rep A", "X", HIST, FISCAL - DAY, 7, 105, 100)
            + mk("KEEP", "Rep A", "X", FISCAL, AS_OF, 7, 130, 100))
    house = (mk("HOUSE", "Rep A", "Y", HIST, FISCAL - DAY, 7, 105, 100)
             + mk("HOUSE", "Rep A", "Y", FISCAL, AS_OF, 7, 160, 100))
    df = df_from(keep + house)
    full = run(df, ["Rep A"])
    exempt = engine.compute_cumulative_growth(df, FISCAL, AS_OF, ["Rep A"], cumulative_rate=RATE,
                                              young_account_pct=RATE, exempt_accounts={"HOUSE"})
    assert acct(exempt, "HOUSE") is None and acct(full, "HOUSE") is not None
    assert rep_row(exempt, "Rep A")["cum_growth"] < rep_row(full, "Rep A")["cum_growth"]


def test_net_down_book_pays_zero():
    # a book that's below last year the whole cycle earns $0 (never negative pay)
    shr = (mk("SHR", "Rep A", "Y", HIST, FISCAL - DAY, 7, 115, 100)
           + mk("SHR", "Rep A", "Y", FISCAL, AS_OF, 7, 105, 100))          # $15 -> $5/wk
    res = run(df_from(shr), ["Rep A"])
    r = rep_row(res, "Rep A")
    assert r["cum_growth"] < 0
    assert r["earned"] == pytest.approx(0.0)
    assert all(row["pay"] == pytest.approx(0.0) for row in res["trajectory"]["Rep A"])


# ---------------------------------------------------------------------------------------------------
# TEAM MODE: the earner is the team that owns the account (80%-of-orders rule + the manager's
# assignments, resolved in service.account_assignments), and the team's pay splits equally.
TEAMS = {"Team 1": ["Rep A", "Rep B"], "Team 2": ["Rep C"]}


def run_teams(df, account_team, teams=None, rate=RATE, **kw):
    return engine.compute_cumulative_growth(df, FISCAL, AS_OF, sum((teams or TEAMS).values(), []),
                                            cumulative_rate=rate, young_account_pct=rate,
                                            teams=(teams or TEAMS), account_team=account_team, **kw)


def team_row(res, team):
    t = res["teams"]; row = t[t["team"] == team]
    return row.iloc[0].to_dict() if len(row) else dict(cum_growth=0.0, earned=0.0)


def test_team_owns_the_account_whoever_sold_it():
    # the account is worked by Rep C (Team 2) but ASSIGNED to Team 1 -> Team 1 earns it, Team 2 gets nothing
    lines = (mk("G", "Rep C", "X", HIST, FISCAL - DAY, 7, 105, 100)
             + mk("G", "Rep C", "X", FISCAL, AS_OF, 7, 125, 100))
    res = run_teams(df_from(lines), {"G": "Team 1"})
    assert team_row(res, "Team 1")["earned"] > 0
    assert team_row(res, "Team 2")["earned"] == pytest.approx(0.0)
    assert acct(res, "G")["holder"] == "Team 1"


def test_unassigned_account_earns_nothing_for_anyone():
    lines = (mk("SHARED", "Rep A", "X", HIST, FISCAL - DAY, 7, 105, 100)
             + mk("SHARED", "Rep A", "X", FISCAL, AS_OF, 7, 135, 100))
    res = run_teams(df_from(lines), {})                       # no team owns it
    assert acct(res, "SHARED") is None
    assert all(team_row(res, t)["earned"] == pytest.approx(0.0) for t in TEAMS)


def test_team_pay_splits_equally_among_members():
    lines = (mk("G", "Rep A", "X", HIST, FISCAL - DAY, 7, 105, 100)
             + mk("G", "Rep A", "X", FISCAL, AS_OF, 7, 125, 100))
    res = run_teams(df_from(lines), {"G": "Team 1"})
    team_pay = team_row(res, "Team 1")["earned"]
    assert team_pay > 0
    reps = res["reps"].set_index("associate")["earned"].to_dict()
    assert reps["Rep A"] == pytest.approx(team_pay / 2)        # Team 1 has two members
    assert reps["Rep B"] == pytest.approx(team_pay / 2)        # the non-selling member earns the same share
    assert sum(r["pay"] for r in res["rep_trajectory"]["Rep A"]) == pytest.approx(team_pay / 2)


def test_team_nets_its_own_accounts():
    # one grower + one decliner in the SAME team net against each other before anything is paid
    up = (mk("UP", "Rep A", "X", HIST, FISCAL - DAY, 7, 105, 100)
          + mk("UP", "Rep A", "X", FISCAL, AS_OF, 7, 125, 100))     # +$20/wk
    down = (mk("DN", "Rep B", "Y", HIST, FISCAL - DAY, 7, 125, 100)
            + mk("DN", "Rep B", "Y", FISCAL, AS_OF, 7, 105, 100))   # -$20/wk
    res = run_teams(df_from(up + down), {"UP": "Team 1", "DN": "Team 1"})
    grower_only = run_teams(df_from(up), {"UP": "Team 1"})
    assert abs(team_row(res, "Team 1")["cum_growth"]) < 45                  # the two cancel out
    # the decliner eats nearly all of the grower's pay (only an early peak in the netted book survives)
    assert team_row(res, "Team 1")["earned"] < 0.1 * team_row(grower_only, "Team 1")["earned"]


def test_house_account_is_exempt_in_team_mode():
    keep = (mk("KEEP", "Rep A", "X", HIST, FISCAL - DAY, 7, 105, 100)
            + mk("KEEP", "Rep A", "X", FISCAL, AS_OF, 7, 125, 100))
    house = (mk("HOUSE", "Rep A", "Y", HIST, FISCAL - DAY, 7, 105, 100)
             + mk("HOUSE", "Rep A", "Y", FISCAL, AS_OF, 7, 160, 100))
    res = run_teams(df_from(keep + house), {"KEEP": "Team 1", "HOUSE": "Team 1"},
                    exempt_accounts={"HOUSE"})
    assert acct(res, "HOUSE") is None
    assert acct(res, "KEEP") is not None
