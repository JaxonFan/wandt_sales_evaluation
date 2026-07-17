"""Service-layer tests for pay-on-collection (Target vs Collected + true-up). Uses a throwaway in-memory DB
since the collected logic lives in the service layer (DB-backed), not the pure engine."""
import datetime as dt
import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app import models as M, service


def _fresh_db(lines_per_invoice=1):
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    db = sessionmaker(bind=eng)()
    db.add(M.Associate(name="Rep A", batch_initial="RA", role="full time sales", status="Active"))
    base = pd.Timestamp("2025-06-28")
    for i in range(10):                      # 10 invoices (lines_per_invoice line items each) in the current period
        for j in range(lines_per_invoice):
            db.add(M.SalesLine(sop_type="Invoice", sop_number=f"INV{i:04d}", item_number=f"IT{i}_{j}",
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


def test_parse_collected_dedups_and_strips():
    # PAID file: every row collected; duplicate rows / whitespace-padded variants / blanks collapse to the set
    raw = pd.DataFrame({"Document Number": ["INV0001", "INV0001", "  INV0001  ", "INV0002", None, "", "INV0003"]})
    assert service.parse_collected(raw) == {"INV0001", "INV0002", "INV0003"}
    assert service.parse_collected(pd.DataFrame({"Invoice Number": ["INV9"]})) == {"INV9"}   # alt header
    assert service.parse_collected(pd.DataFrame({"Something Else": [1, 2]})) is None          # no invoice col


def test_parse_voided_uses_void_status_or_whole_list():
    # a combined 'invoices: normal + voided' file with a Void Status column -> only the Voided rows
    raw = pd.DataFrame({"SOP Number": ["INVA", "INVB", "INVC"], "Void Status": ["Voided", "", "Voided"]})
    assert service.parse_voided(raw) == {"INVA", "INVC"}
    # a voided-only list (no Void Status column) -> every row is voided
    assert service.parse_voided(pd.DataFrame({"SOP Number": ["INVX", "  INVX ", "INVY"]})) == {"INVX", "INVY"}
    assert service.parse_voided(pd.DataFrame({"Nope": [1]})) is None


def test_voided_invoice_excluded_from_every_bonus_and_unpaid():
    db = _fresh_db()                                            # 10 invoices, 1 line each -> 10 line items
    assert int(service.run_period_bonus(db)[0]["scorecards"].set_index("associate").loc["Rep A", "items_placed"]) == 10
    db.add(M.VoidedInvoice(sop_number="INV0000")); db.commit()  # void one invoice
    sc = service.run_period_bonus(db)[0]["scorecards"].set_index("associate")   # cache auto-invalidates
    assert int(sc.loc["Rep A", "items_placed"]) == 9           # its line is gone from contribution/growth/acq
    assert "INV0000" not in service.active_lines(db)["sop_number"].astype(str).values
    unpaid_inv = {iv["sop_number"] for a in service.unpaid_accounts(db, "Rep A")[0] for iv in a["invoice_list"]}
    assert "INV0000" not in unpaid_inv                         # voided never shows as unpaid


def test_collected_set_cannot_double_count(tmp_path):
    # sop_number is the PK, so the same invoice can only ever be stored once (no duplication in the DB)
    db = _fresh_db()
    db.add(M.CollectedInvoice(sop_number="INV0000")); db.commit()
    from sqlalchemy.exc import IntegrityError
    db.add(M.CollectedInvoice(sop_number="INV0000"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.query(M.CollectedInvoice).count() == 1


def test_payrun_payable_is_collected_minus_paid():
    db = _fresh_db()
    _set_collected(db, [f"INV{i:04d}" for i in range(6)])    # 60% collected
    by_rep, _ = service.collections_payrun(db)
    row = by_rep["Rep A"][0]                                  # the current period
    assert row["payable"] == row["collected"] - row["paid"]
    assert row["paid"] == 0 and row["collected"] > 0         # nothing paid yet -> all collected is payable
    assert row["collected"] <= row["target"]


def test_contribution_collected_is_exact_collected_line_items():
    # contribution is paid PER collected line item (exact), not scaled by revenue
    db = _fresh_db()                                          # 10 invoices x 1 line, item_rate 0.10
    _set_collected(db, [f"INV{i:04d}" for i in range(3)])
    c = service.collected_scorecard(db)[0]["Rep A"]
    assert c["contribution"] == pytest.approx(1.00)          # 10 lines x $0.10
    assert c["contribution_collected"] == pytest.approx(0.30)  # 3 collected lines x $0.10


def test_progressive_true_up_across_cycles_and_clawback():
    db = _fresh_db(lines_per_invoice=20)     # bigger amounts so per-cycle deltas don't vanish in $-rounding
    invs = [f"INV{i:04d}" for i in range(10)]

    _set_collected(db, invs[:6])                             # cycle 1: 60% collected, nothing paid
    row = service.collections_payrun(db)[0]["Rep A"][0]
    pid, c6 = row["period_id"], row["collected"]
    assert row["paid"] == 0 and row["payable"] == c6 > 0

    db.add(M.Award(period_id=pid, associate="Rep A", award_amount=c6)); db.commit()   # record pay
    assert service.collections_payrun(db)[0]["Rep A"][0]["payable"] == 0              # nothing new owed

    _set_collected(db, invs[:9])                             # cycle 2: more collected -> pay the increment
    row = service.collections_payrun(db)[0]["Rep A"][0]
    assert row["collected"] > c6 and row["payable"] == row["collected"] - c6 > 0

    _set_collected(db, invs[:4])                             # a bounce: collected drops below what was paid
    assert service.collections_payrun(db)[0]["Rep A"][0]["payable"] < 0               # clawback


def test_payrun_is_cached_and_invalidates_correctly():
    db = _fresh_db()
    service.collections_payrun(db)                                  # warm (creates period_anchor -> stable version)
    r1 = service.collections_payrun(db)
    assert service.collections_payrun(db) is r1                     # repeat call = cache hit (same object)
    idx, pid = r1[0]["Rep A"][0]["idx"], r1[0]["Rep A"][0]["period_id"]
    eng_before = service.run_period_bonus(db, idx)[0]
    # recording a pay (Award) must refresh the pay-run but NOT re-run the engine
    db.add(M.Award(period_id=pid, associate="Rep A", award_amount=5.0)); db.commit()
    assert service.collections_payrun(db) is not r1                 # pay-run recomputed
    assert service.run_period_bonus(db, idx)[0] is eng_before       # engine result reused
    # an engine input change (a collected invoice) DOES bust the engine cache
    db.add(M.CollectedInvoice(sop_number="INV0000")); db.commit()
    assert service.run_period_bonus(db, idx)[0] is not eng_before


def _db_aged():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    db = sessionmaker(bind=create_engine("sqlite:///:memory:"))()
    Base.metadata.create_all(db.get_bind())
    db.add(M.Associate(name="Rep A", batch_initial="RA", role="full time sales", status="Active"))
    for acct, dstr, inv in [("ACCT_NEW", "2025-10-10", "INVN"), ("ACCT_MID", "2025-09-01", "INVM"),
                            ("ACCT_OLD", "2025-06-15", "INVO")]:
        db.add(M.SalesLine(sop_type="Invoice", sop_number=inv, item_number="IT", qty=1.0, unit_price=100.0,
                           extended_price=100.0, unit_cost=60.0, extended_cost=60.0, line_profit=40.0,
                           customer_number=acct, customer_name=acct, document_date=pd.Timestamp(dstr).date(),
                           batch_number="RA", associate="Rep A", imported_at=dt.datetime(2025, 10, 15)))
    db.commit(); service._LINES_CACHE.clear()
    return db


def test_silence_detector_unpaid_and_risky_first():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    db = sessionmaker(bind=create_engine("sqlite:///:memory:"))()
    Base.metadata.create_all(db.get_bind())
    db.add(M.Associate(name="Rep A", batch_initial="RA", role="full time sales", status="Active"))

    def orders(acct, start, n, prefix):
        for i in range(n):
            d = (pd.Timestamp(start) + pd.Timedelta(days=7 * i)).date()
            db.add(M.SalesLine(sop_type="Invoice", sop_number=f"{prefix}{i}", item_number="IT", qty=1.0,
                               unit_price=100.0, extended_price=100.0, unit_cost=60.0, extended_cost=60.0,
                               line_profit=40.0, customer_number=acct, customer_name=acct, document_date=d,
                               batch_number="RA", associate="Rep A", imported_at=dt.datetime(2025, 12, 2)))
    orders("ACTIVE", "2025-10-06", 9, "A")      # weekly through ~Dec 1 -> anchors as_of; NOT silent
    orders("SILOWE", "2025-09-01", 6, "O")      # last order ~Oct 6 -> silent ~56d (> 3x its 7d gap)
    orders("SILPAID", "2025-09-01", 6, "P")
    db.commit()
    service._LINES_CACHE.clear(); service._ENGINE_CACHE.clear()
    for i in range(6):
        db.add(M.CollectedInvoice(sop_number=f"P{i}"))     # SILPAID fully collected -> $0 owed
    for i in range(5):
        db.add(M.CollectedInvoice(sop_number=f"O{i}"))     # SILOWE: O5 left unpaid -> $100 owed
    db.commit()

    sil = service.flag_silent_accounts(db, associate="Rep A")
    accts = [s["account"] for s in sil]
    assert "SILOWE" in accts and "SILPAID" in accts and "ACTIVE" not in accts     # recent account isn't silent
    assert next(s for s in sil if s["account"] == "SILOWE")["unpaid"] == 100
    assert next(s for s in sil if s["account"] == "SILPAID")["unpaid"] == 0
    assert accts.index("SILOWE") < accts.index("SILPAID")                          # risky-first: owing above paid
    assert all(s["rep"] == "Rep A" and "last_order" in s for s in sil)


def test_unpaid_invoice_list_is_chronological_and_sums():
    db = _fresh_db()                          # 10 invoices spread across ACCT0/1/2 on various dates
    _set_collected(db, [])                    # nothing collected -> all unpaid
    rows, _ = service.unpaid_accounts(db, "Rep A")
    a0 = next(r for r in rows if r["account"] == "ACCT0")
    il = a0["invoice_list"]
    assert len(il) == a0["invoices"] >= 2
    assert [iv["date"] for iv in il] == sorted(iv["date"] for iv in il)      # oldest-first
    assert sum(iv["amount"] for iv in il) == a0["outstanding"]               # per-invoice sums to the account total
    # a collected invoice drops out of the account's unpaid list
    _set_collected(db, [il[0]["sop_number"]])
    a0b = next(r for r in service.unpaid_accounts(db, "Rep A")[0] if r["account"] == "ACCT0")
    assert all(iv["sop_number"] != il[0]["sop_number"] for iv in a0b["invoice_list"])


def test_unpaid_aging_buckets_and_writeoff():
    db = _db_aged()                                          # nothing collected -> all 3 outstanding
    rows, total = service.unpaid_accounts(db, "Rep A")
    assert len(rows) == 3 and total == 300
    bucket = {r["account"]: r["bucket"] for r in rows}
    assert bucket["ACCT_NEW"] == "0-30" and bucket["ACCT_OLD"] == "90+"   # aged vs recent
    db.add(M.WrittenOffInvoice(sop_number="INVO")); db.commit()          # write off the dead one
    service._LINES_CACHE.clear()
    rows2, total2 = service.unpaid_accounts(db, "Rep A")
    assert len(rows2) == 2 and total2 == 200 and all(r["account"] != "ACCT_OLD" for r in rows2)
