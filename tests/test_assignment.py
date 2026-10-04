"""Team ownership, batch-prefix attribution, and the month-level pay math — on the hermetic book."""
import pytest
import pandas as pd

from webfix import build, teardown, ITEM_RATE
from app import service, models as M


@pytest.fixture
def db():
    _, Session = build()
    s = Session()
    yield s
    s.close(); teardown()


def test_second_batch_prefix_credits_the_rep(db):
    prefix_map, _, _ = service.attribution_maps(db)
    assert prefix_map["AC"] == "An Cao" and prefix_map["AN"] == "An Cao"
    assert service.resolve_associate("AC0926", prefix_map, {}) == "An Cao"


def test_eighty_percent_of_orders_rule(db):
    rows = {r["account"]: r for r in service.account_assignments(db)}
    assert rows["ACCT1"]["team"] == "Team 1" and rows["ACCT1"]["shares"]["Team 1"] == 1.0
    assert rows["ACCT2"]["team"] == "Team 2"
    assert rows["ACCT3"]["team"] == "Team 1"          # the AC-prefixed invoice counts for An Cao's team


def test_manual_pin_beats_the_rule_and_hands_back(db):
    db.add(M.OwnershipPin(account="ACCT1", team="Team 2", effective_from="2026-08")); db.commit()
    assert {r["account"]: r["team"] for r in service.account_assignments(db)}["ACCT1"] == "Team 2"
    db.add(M.OwnershipPin(account="ACCT1", team=None, effective_from="2026-09")); db.commit()   # back to the rule
    assert {r["account"]: r["team"] for r in service.account_assignments(db)}["ACCT1"] == "Team 1"
    # history: August still Team 2's, September the rule's (Team 1)
    by_month = service.ownership_by_month(db, ["2026-08", "2026-09"])
    assert by_month["2026-08"]["ACCT1"] == "Team 2" and by_month["2026-09"]["ACCT1"] == "Team 1"


def test_house_accounts_are_house_whoever_writes_them(db):
    import datetime as dt
    db.add(M.SalesLine(sop_type="Invoice", sop_number="INVHOUSE", item_number="X", item_description="x", qty=1.0,
                       unit_price=50.0, extended_price=50.0, unit_cost=30.0, extended_cost=30.0, line_profit=20.0,
                       customer_number="FIRSTIN01", customer_name="FIRST CHOICE", document_date=dt.date(2026, 9, 9),
                       batch_number="AN0909", associate="An Cao", imported_at=dt.datetime(2026, 10, 1)))
    db.commit(); service._LINES_CACHE.clear(); service._ENGINE_CACHE.clear()
    row = {r["account"]: r for r in service.account_assignments(db)}["FIRSTIN01"]
    assert row["team"] == "House" and row["house_by_policy"]          # 100% An Cao's orders, still House


def test_each_month_releases_on_its_own_invoices(db):
    months = {m["month"]: m for m in service.pay_ledger(db)["months"]["An Cao"]}
    aug, sep = months["2026-08"], months["2026-09"]
    assert aug["n_items"] == 10 and aug["earned"] == pytest.approx(10 * ITEM_RATE)
    assert aug["collected_pct"] == pytest.approx(40.0)                      # $400 of $1,000 collected
    assert aug["collectable"] == pytest.approx(aug["earned"] * 0.40)
    assert sep["n_items"] == 12 and sep["collected_pct"] == pytest.approx(500 / 1200 * 100)
    assert sep["collectable"] == pytest.approx(sep["earned"] * 500 / 1200)
    # never the blended rate: the two months' percentages differ and neither equals the overall
    assert aug["collected_pct"] != sep["collected_pct"]


def test_payment_settles_august_before_september(db):
    before = {m["month"]: m["owed"] for m in service.pay_ledger(db)["months"]["An Cao"]}
    service.allocate_payment(db, "An Cao", before["2026-08"] + 0.01, user_id=1)
    service._ENGINE_CACHE.clear()
    after = {m["month"]: m for m in service.pay_ledger(db)["months"]["An Cao"]}
    assert after["2026-08"]["owed"] == pytest.approx(0.0)
    assert after["2026-09"]["owed"] == pytest.approx(before["2026-09"] - 0.01)
    assert after["2026-08"]["paid"] == pytest.approx(before["2026-08"])


def test_audit_months_newest_first_and_invoices_by_date(db):
    d = service.rep_pay_detail(db, "An Cao")
    assert [m["month"] for m in d["months"]] == ["2026-09", "2026-08"]
    for m in d["months"]:
        dates = [i["date"] for i in m["invoices"]]
        assert dates == sorted(dates, reverse=True)


def test_new_account_profit_share_pays_the_winner_monthly():
    """A rep-won account pays acq_profit_share of its monthly profit to the rep who won it, for acq_share_months."""
    from webfix import build, teardown
    client, Session = build(growth_live=True)
    s = Session()
    try:
        s.add(M.AcquisitionReview(account="ACCT3", rep_won=True)); s.commit()
        service._ENGINE_CACHE.clear()
        months = {m["month"]: m for m in service.pay_ledger(s)["months"]["An Cao"]}
        # ACCT3 first ordered 2026-09-26 (2 lines x $30 profit = $60) -> 1% = $0.60 in September, nothing in August
        assert months["2026-09"]["acquisition"] == pytest.approx(0.60, abs=0.01)
        assert months["2026-08"]["acquisition"] == pytest.approx(0.0)
        assert months["2026-09"]["earned"] == pytest.approx(months["2026-09"]["contribution"] + months["2026-09"]["growth"] + 0.60, abs=0.01)
    finally:
        s.close(); teardown()


def test_silent_account_loses_ownership_and_page_still_renders():
    """Regression: an account whose last order is >12 months old carried an owner forever and 500'd /accounts."""
    from webfix import build, login, teardown
    client, Session = build()      # the fixture includes GHOST, which last ordered in 2024
    s = Session()
    try:
        by_month = service.ownership_by_month(s, ["2026-08", "2026-09"])
        assert by_month["2026-09"].get("GHOST") is None
        assert "GHOST" not in {r["account"] for r in service.account_assignments(s) if r["team"]}
        login(client)
        assert client.get("/accounts?view=all").status_code == 200
    finally:
        s.close(); teardown()


def test_moving_a_person_is_dated(db):
    """Garmi joins Team 1 from 2026-09: August orders still count for Team 2 in the rule; roster history kept."""
    db.add(M.TeamMembership(team="Team 1", members=["An Cao", "Garmi Mei"], effective_from="2026-09"))
    db.add(M.TeamMembership(team="Team 2", members=[], effective_from="2026-09"))
    db.commit(); service._ENGINE_CACHE.clear()
    assert service.team_of_rep(db, "2026-08")["Garmi Mei"] == "Team 2"
    assert service.team_of_rep(db, "2026-09")["Garmi Mei"] == "Team 1"
    # ACCT2 is 100% Garmi's orders: as of August it belongs to Team 2, by September Team 1's share is rising but
    # the August orders were written on Team 2 and stay credited there (so the rule has not flipped it yet)
    aug = service._rule_as_of(db, pd.Timestamp("2026-08-31"))["ACCT2"]
    sep = service._rule_as_of(db, pd.Timestamp("2026-09-30"))["ACCT2"]
    assert aug["auto"] == "Team 2" and aug["shares"]["Team 2"] == pytest.approx(1.0)
    assert 0 < sep["shares"]["Team 1"] < 0.8 and sep["auto"] == "Team 2"
    hist = service.roster_history(db)
    assert [h[0] for h in hist["Team 1"]] == ["2000-01", "2026-09"]


def test_assistant_product_tools_aggregate_lines():
    """The product tools see every invoice line: a SKU, a category (search term), and the ranking agree."""
    from webfix import build, teardown
    from app import assistant as AS
    _, Session = build()
    s = Session()
    try:
        T = AS.Tools(s)
        top = T.call("top_products", {"months": 24, "n": 5, "by": "revenue"})
        assert top["distinct_products"] >= 1 and top["products"][0]["revenue"] > 0
        item = top["products"][0]["item"]
        one = T.call("product_sales", {"item": item, "months": 24})
        assert one["revenue"] == pytest.approx(top["products"][0]["revenue"])
        cat = T.call("category_sales", {"q": "IT", "months": 24})          # every fixture item is IT0..ITn
        assert cat["revenue"] == pytest.approx(sum(p["revenue"] for p in T.call("top_products", {"months": 24, "n": 50})["products"]))
        rep = AS.Tools(s, rep_only="An Cao").call("category_sales", {"q": "IT", "months": 24})
        assert rep["rep"] == "An Cao" and set(rep["sellers_revenue"]) == {"An Cao"}
    finally:
        s.close(); teardown()
