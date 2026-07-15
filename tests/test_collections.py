"""Service-layer tests for pay-on-collection (Target vs Collected + true-up). Uses a throwaway in-memory DB
since the collected logic lives in the service layer (DB-backed), not the pure engine."""
import datetime as dt
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app import models as M, service


def _fresh_db():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(M.Associate(name="Rep A", batch_initial="RA", role="full time sales", status="Active"))
    base = pd.Timestamp("2025-06-28")
    for i in range(10):                      # 10 invoices (10 line items) in the current period
        db.add(M.SalesLine(sop_type="Invoice", sop_number=f"INV{i:04d}", item_number=f"IT{i}",
                           qty=1.0, unit_price=100.0, extended_price=100.0, unit_cost=60.0, extended_cost=60.0,
                           line_profit=40.0, customer_number=f"ACCT{i % 3}", customer_name=f"ACCT{i % 3}",
                           document_date=(base - pd.Timedelta(days=2 * i)).date(),
                           batch_number="RA0628", associate="Rep A", imported_at=dt.datetime(2025, 7, 1)))
    db.commit()
    service._LINES_CACHE.clear(); service._ENGINE_CACHE.clear()
    return db


def _set_collected(db, sops):
    db.query(M.CollectedInvoice).delete()
    for s in sops:
        db.add(M.CollectedInvoice(sop_number=s))
    db.commit()
    service._LINES_CACHE.clear()


def test_load_lines_df_includes_sop_number():
    assert "sop_number" in service.load_lines_df(_fresh_db()).columns


def test_collected_scales_and_never_exceeds_target():
    db = _fresh_db()
    target = service.collected_scorecard(db)[0]["Rep A"]["total"]
    assert target > 0

    _set_collected(db, [])                                   # nothing collected -> $0
    assert service.collected_scorecard(db)[0]["Rep A"]["total_collected"] == pytest.approx(0.0, abs=1e-6)

    _set_collected(db, [f"INV{i:04d}" for i in range(10)])   # all collected -> equals Target
    assert service.collected_scorecard(db)[0]["Rep A"]["total_collected"] == pytest.approx(target)

    _set_collected(db, [f"INV{i:04d}" for i in range(5)])    # half of the line items -> half (contribution is per-line)
    half = service.collected_scorecard(db)[0]["Rep A"]["total_collected"]
    assert 0 < half < target and half == pytest.approx(target * 0.5, rel=0.02)


def test_bounced_invoice_claws_back():
    db = _fresh_db()
    _set_collected(db, [f"INV{i:04d}" for i in range(10)])
    full = service.collected_scorecard(db)[0]["Rep A"]["total_collected"]
    _set_collected(db, [f"INV{i:04d}" for i in range(9)])    # one invoice reverses (drops from the snapshot)
    assert service.collected_scorecard(db)[0]["Rep A"]["total_collected"] < full


def test_payrun_payable_is_collected_minus_paid():
    db = _fresh_db()
    _set_collected(db, [f"INV{i:04d}" for i in range(6)])    # 60% collected
    by_rep, _ = service.collections_payrun(db)
    row = by_rep["Rep A"][0]                                  # the current period
    assert row["payable"] == row["collected"] - row["paid"]
    assert row["paid"] == 0 and row["collected"] > 0         # nothing paid yet -> all collected is payable
    assert row["collected"] <= row["target"]
