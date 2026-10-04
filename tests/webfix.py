"""A hermetic web fixture: an in-memory DB with a tiny real-looking book, wired into the FastAPI app.

Reps use the REAL roster names so config.TEAMS forms (An Cao -> Team 1, Garmi Mei -> Team 2). Two months
of invoices (Aug + Sep 2026), some collected, so the pay ledger has something to say in both chapters."""
import datetime as dt
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.db import Base, get_db
from app import models as M, service
from app.auth import hash_password
from app.growth_main import app

ITEM_RATE = 0.10


def build(growth_live=False, october=False):
    """Returns (client, SessionLocal). growth_live=True pins growth_start before the data so the growth
    columns are active; october=True adds an October invoice so the cycle rolls into a second chapter."""
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    db = Session()
    db.add_all([
        M.Associate(name="An Cao", batch_initial="AN", role="full time sales", status="Active"),
        M.Associate(name="Garmi Mei", batch_initial="GM", role="part time sales", status="Active"),
        M.Associate(name="Cindy Chan", batch_initial="CC", role="manager", status="Active"),
        M.User(username="manager", password_hash=hash_password("pw"), role="manager"),
        M.User(username="an", password_hash=hash_password("pw"), role="rep", associate_name="An Cao"),
    ])
    settings = {"program_start": "2026-08-01", "growth_start": "2025-10-01" if growth_live else "2026-10-01",
                "fiscal_start_month": "10", "item_rate": str(ITEM_RATE)}
    for k, v in settings.items():
        db.add(M.Setting(key=k, value=v))
    n = 0

    def inv(rep, batch, account, day, lines, price=100.0):
        nonlocal n
        n += 1
        sop = f"INV{n:05d}"
        for j in range(lines):
            db.add(M.SalesLine(sop_type="Invoice", sop_number=sop, item_number=f"IT{j}", item_description="x",
                               qty=1.0, unit_price=price, extended_price=price, unit_cost=price * 0.7,
                               extended_cost=price * 0.7, line_profit=price * 0.3, customer_number=account,
                               customer_name=account, document_date=pd.Timestamp(day).date(),
                               batch_number=batch, associate=rep, imported_at=dt.datetime(2026, 10, 1)))
        return sop

    # last year's history for the growth baseline (same accounts, Oct 2025 - Jul 2026; stops before the chapter)
    for mo in range(10, 20):
        y, m = (2025, mo) if mo <= 12 else (2026, mo - 12)
        inv("An Cao", "AN", "ACCT1", f"{y}-{m:02d}-10", 3)
        inv("Garmi Mei", "GM", "ACCT2", f"{y}-{m:02d}-12", 2)
    # the launch chapter: Aug + Sep 2026
    paid = []
    paid.append(inv("An Cao", "AN", "ACCT1", "2026-08-05", 4))      # collected
    inv("An Cao", "AN", "ACCT1", "2026-08-20", 6)                    # missing
    paid.append(inv("An Cao", "AN", "ACCT1", "2026-09-03", 5))      # collected
    inv("An Cao", "AN", "ACCT1", "2026-09-25", 5)                    # missing
    inv("An Cao", "AC0901", "ACCT3", "2026-09-26", 2)                # written under the AC prefix
    paid.append(inv("Garmi Mei", "GM", "ACCT2", "2026-08-11", 10))
    inv("Garmi Mei", "GM", "ACCT2", "2026-09-11", 10)
    if october:
        inv("An Cao", "AN", "ACCT1", "2026-10-06", 3)
    # a DORMANT account: ordered in 2024 only. Ownership must expire, pages must not choke on it.
    inv("An Cao", "AN", "GHOST", "2024-11-05", 2)
    inv("An Cao", "AN", "GHOST", "2024-12-05", 2)
    for sop in paid:
        db.add(M.CollectedInvoice(sop_number=sop, reported_at=dt.datetime(2026, 10, 1)))
    db.commit()
    service._LINES_CACHE.clear(); service._ENGINE_CACHE.clear()

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = override
    client = TestClient(app)
    return client, Session


def login(client, user="manager"):
    r = client.post("/login", data={"username": user, "password": "pw"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    return client


def teardown():
    app.dependency_overrides.clear()
    service._LINES_CACHE.clear(); service._ENGINE_CACHE.clear()
