"""The pay ledger: earnings stay payable until the money is actually collected.

The rule the manager insisted on: pay-on-collection means a cycle rolling over must NOT strand earnings.
An invoice written in the launch chapter and collected two months later still pays out — in full, once,
oldest chapter first, and never clawed back.
"""
import datetime as dt
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app import models as M, service

LAUNCH = pd.Timestamp("2026-08-01")      # contribution-only chapter: Aug 1 - Sep 30
GROWTH = pd.Timestamp("2026-10-01")      # the first full cycle opens here
ITEM_RATE = 0.10


def _db(invoice_dates):
    """One rep; one 1-line invoice per date given. Chapters are pinned so the test is date-independent."""
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(M.Associate(name="Rep A", batch_initial="RA", role="full time sales", status="Active"))
    for key, val in (("program_start", str(LAUNCH.date())), ("growth_start", str(GROWTH.date())),
                     ("fiscal_start_month", "10"), ("item_rate", str(ITEM_RATE))):
        db.add(M.Setting(key=key, value=val))
    for i, day in enumerate(invoice_dates):
        db.add(M.SalesLine(sop_type="Invoice", sop_number=f"INV{i:04d}", item_number=f"IT{i}",
                           qty=1.0, unit_price=100.0, extended_price=100.0, unit_cost=60.0,
                           extended_cost=60.0, line_profit=40.0, customer_number="ACCT1",
                           customer_name="ACCT1", document_date=pd.Timestamp(day).date(),
                           batch_number="RA0101", associate="Rep A",
                           imported_at=dt.datetime(2026, 10, 1)))
    db.commit()
    _bust(db)
    return db


def _bust(db):
    service._LINES_CACHE.clear(); service._ENGINE_CACHE.clear()


def _collect(db, sops):
    db.query(M.CollectedInvoice).delete()
    for s in sops:
        db.add(M.CollectedInvoice(sop_number=s))
    db.commit(); _bust(db)


def _totals(db):
    return service.pay_ledger(db)["totals"]["Rep A"]


def test_launch_chapter_pays_only_what_is_collected():
    db = _db(["2026-08-10", "2026-08-20", "2026-09-05", "2026-09-20"])
    _collect(db, ["INV0000"])                                  # 1 of 4 invoices paid
    t = _totals(db)
    assert t["earned"] == pytest.approx(4 * ITEM_RATE)
    assert t["owed"] == pytest.approx(4 * ITEM_RATE * 0.25)
    assert t["unreleased"] == pytest.approx(4 * ITEM_RATE * 0.75)


def test_closed_chapter_keeps_releasing_after_the_cycle_rolls_over():
    """THE one that matters: October trading opens a new chapter, and the August money still pays."""
    db = _db(["2026-08-10", "2026-08-20", "2026-09-05", "2026-09-20", "2026-10-12"])
    _collect(db, ["INV0000"])
    assert len(service.pay_chapters(db)) == 2                  # launch chapter + the October cycle
    launch_owed = service.pay_ledger(db)["rows"]["Rep A"][0]["owed"]
    assert launch_owed == pytest.approx(4 * ITEM_RATE * 0.25)  # still live, not stranded

    _collect(db, ["INV0000", "INV0001", "INV0002", "INV0003"])  # the August/September invoices finally pay
    after = service.pay_ledger(db)["rows"]["Rep A"][0]
    assert after["owed"] == pytest.approx(4 * ITEM_RATE)        # the whole launch chapter is now payable
    assert after["unreleased"] == pytest.approx(0.0)


def test_payment_settles_the_oldest_chapter_first():
    db = _db(["2026-08-10", "2026-08-20", "2026-10-12", "2026-10-20"])
    _collect(db, ["INV0000", "INV0001", "INV0002", "INV0003"])   # everything collected
    owed = _totals(db)["owed"]
    assert owed == pytest.approx(4 * ITEM_RATE)
    applied = service.allocate_payment(db, "Rep A", 0.20, user_id=1)
    _bust(db)
    assert applied[0]["chapter"].startswith("Aug 2026")          # launch chapter cleared first
    rows = service.pay_ledger(db)["rows"]["Rep A"]
    assert rows[0]["paid"] == pytest.approx(0.20) and rows[0]["owed"] == pytest.approx(0.0)
    assert rows[1]["owed"] == pytest.approx(0.20)                # the October chapter is still owed


def test_paying_everything_leaves_nothing_owed_and_never_goes_negative():
    db = _db(["2026-08-10", "2026-09-05"])
    _collect(db, ["INV0000", "INV0001"])
    service.allocate_payment(db, "Rep A", _totals(db)["owed"], user_id=1); _bust(db)
    assert _totals(db)["owed"] == pytest.approx(0.0)
    # a later recomputation that LOWERS the collected share must not create a negative balance
    _collect(db, [])
    assert _totals(db)["owed"] == pytest.approx(0.0)
    assert _totals(db)["paid"] == pytest.approx(2 * ITEM_RATE)


def test_a_payment_is_never_more_than_the_chapter_collected():
    db = _db(["2026-08-10", "2026-08-20"])
    _collect(db, ["INV0000"])                                   # only half collected
    applied = service.allocate_payment(db, "Rep A", 999.0, user_id=1); _bust(db)
    assert sum(a["amount"] for a in applied) == pytest.approx(2 * ITEM_RATE * 0.5)
    assert _totals(db)["paid"] == pytest.approx(2 * ITEM_RATE * 0.5)
