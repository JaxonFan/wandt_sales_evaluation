"""Seed the DB from the two item-level XLSX exports + the roster. Idempotent (re-runnable).
Run:  DATABASE_URL=... python -m app.load_history
"""
import warnings; warnings.filterwarnings("ignore")
import glob
import pandas as pd
from .auth import hash_password
from .db import engine, SessionLocal, Base
from . import models as M
from .config import DEFAULTS, SALES_ROLES
from .service import attribution_maps, resolve_associate

ROOT = "."
ROSTER_PATH = f"{ROOT}/w&t_sales_associate_roster.xlsx"
SALES_GLOB = f"{ROOT}/sales_data/*.XLSX"


def seed_associates(db):
    roster = pd.read_excel(ROSTER_PATH)
    roster["Batch Initial"] = roster["Batch Initial"].astype(str).str.strip().str.upper()
    roster["Other Names"] = roster["Other Names"].astype(str).str.strip().str.upper()
    roster["Sales Person Name"] = roster["Sales Person Name"].astype(str).str.strip()
    roster["Status"] = roster["Status"].astype(str).str.strip().str.title()
    roster["Role"] = roster["Role"].astype(str).str.strip().str.lower()
    roster = roster[roster["Status"].isin(["Active", "Inactive"])]   # drop trailing junk row
    for _, r in roster.iterrows():
        db.add(M.Associate(
            name=(r["Sales Person Name"] if r["Sales Person Name"] not in ("", "nan") else None),
            batch_initial=(r["Batch Initial"] if r["Batch Initial"] not in ("", "NAN") else None),
            other_names=(r["Other Names"] if r["Other Names"] not in ("", "NAN") else None),
            role=r["Role"], status=r["Status"]))
    db.commit()


def read_sales(db):
    prefix_map, variant_map, sales_team = attribution_maps(db)
    frames = []
    for path in sorted(glob.glob(SALES_GLOB)):
        frames.append(pd.read_excel(path))
    raw = pd.concat(frames, ignore_index=True)
    sales = raw[raw["SOP Type"] == "Invoice"].copy()
    for col in ["Extended Price", "Extended Cost", "QTY", "Unit Price", "Unit Cost"]:
        sales[col] = pd.to_numeric(sales[col], errors="coerce")
    sales["Document Date"] = pd.to_datetime(sales["Document Date"])
    sales["associate"] = sales["Batch Number"].apply(lambda b: resolve_associate(b, prefix_map, variant_map))
    sales = sales[sales["associate"].isin(sales_team)].copy()    # rep transactions only
    return sales


def main():
    Base.metadata.create_all(engine)
    db = SessionLocal()
    for tbl in (M.SalesLine, M.Associate):
        db.query(tbl).delete()
    db.commit()

    seed_associates(db)
    sales = read_sales(db)

    def num(value):
        return float(value) if pd.notna(value) else None

    objs = []
    for r in sales.to_dict("records"):
        ext_price, ext_cost = num(r["Extended Price"]), num(r["Extended Cost"])
        objs.append(M.SalesLine(
            sop_type=str(r["SOP Type"]), sop_number=str(r["SOP Number"]).strip(),
            item_number=str(r["Item Number"]).strip(), item_description=str(r["Item Description"]),
            qty=num(r["QTY"]), unit_price=num(r["Unit Price"]), extended_price=ext_price,
            unit_cost=num(r["Unit Cost"]), extended_cost=ext_cost,
            line_profit=(ext_price - ext_cost) if (ext_price is not None and ext_cost is not None) else None,
            customer_number=str(r["Customer Number"]).strip(), customer_name=str(r["Customer Name"]).strip(),
            document_date=r["Document Date"].date(), batch_number=str(r["Batch Number"]).strip().upper(),
            associate=r["associate"]))
    db.bulk_save_objects(objs)
    db.commit()

    if db.query(M.User).count() == 0:
        db.add(M.User(username="manager", password_hash=hash_password("demo123"), role="manager"))
        db.add(M.User(username="admin", password_hash=hash_password("demo123"), role="admin"))
        # one read-only rep login per sales associate (username = first name, lowercased)
        from .service import attribution_maps
        _, _, sales_team = attribution_maps(db)
        for name in sales_team:
            username = name.split()[0].lower()
            db.add(M.User(username=username, password_hash=hash_password("demo123"),
                          role="rep", associate_name=name))
    if db.query(M.Setting).filter(M.Setting.key != "period_anchor").count() == 0:
        for k, v in DEFAULTS.items():
            db.add(M.Setting(key=k, value=str(v)))
    db.commit()

    print(f"seeded: {db.query(M.Associate).count()} associates, {db.query(M.SalesLine).count():,} rep sales lines")
    print(f"date range: {sales['Document Date'].min().date()} -> {sales['Document Date'].max().date()}")
    print("users: manager/demo123, admin/demo123")
    db.close()


if __name__ == "__main__":
    main()
