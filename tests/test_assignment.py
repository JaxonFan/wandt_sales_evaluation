"""Team ownership, batch-prefix attribution, and the month-level pay math — on the hermetic book."""
import pytest

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


def test_manual_pin_beats_the_rule_and_clears(db):
    db.add(M.AccountAssignment(account="ACCT1", team="Team 2")); db.commit()
    assert {r["account"]: r["team"] for r in service.account_assignments(db)}["ACCT1"] == "Team 2"
    db.query(M.AccountAssignment).delete(); db.commit()
    assert {r["account"]: r["team"] for r in service.account_assignments(db)}["ACCT1"] == "Team 1"


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
