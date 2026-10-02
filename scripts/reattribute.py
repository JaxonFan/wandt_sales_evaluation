"""Recompute `sales_lines.associate` for rows already in the database.

The importer stamps each line with the salesperson resolved from its Batch Number, so a roster fix (a new
batch prefix, a renamed variant) only reaches invoices imported AFTER the fix. This replays the current
attribution over every existing row.

    sales_evaluation/bin/python scripts/reattribute.py            # local DB, dry run
    sales_evaluation/bin/python scripts/reattribute.py --apply    # local DB, write
    ! sales_evaluation/bin/python scripts/reattribute.py --prod --apply   # PROD (opens the RDS SG for this
                                                                          # IP only, then closes it)

Prints what would change before changing anything; --apply writes. Idempotent.
"""
import os, sys, argparse

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from sync_from_prod import aws, public_ip, rds_sg, authorize, revoke, SECRET_ID   # noqa: E402  (same pattern)


def prod_url():
    import json
    raw = aws("secretsmanager", "get-secret-value", "--secret-id", SECRET_ID,
              "--query", "SecretString", "--output", "text").strip()
    try:
        parsed = json.loads(raw)
        url = parsed.get("DATABASE_URL", parsed) if isinstance(parsed, dict) else parsed
    except json.JSONDecodeError:
        url = raw
    if "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://").replace(
            "postgres://", "postgresql+psycopg2://")
    return url


def reattribute(session, apply_changes):
    from app import models as M
    from app.service import attribution_maps, resolve_associate
    prefix_map, variant_map, _team = attribution_maps(session)
    print(f"prefixes: {sorted(prefix_map)}")
    print(f"variants: {sorted(variant_map)}")
    changes, by_move = 0, {}
    for line in session.query(M.SalesLine).yield_per(5000):
        want = resolve_associate(line.batch_number, prefix_map, variant_map)
        if want != line.associate:
            key = f"{line.associate or '(none)'} -> {want or '(none)'}"
            by_move[key] = by_move.get(key, 0) + 1
            changes += 1
            if apply_changes:
                line.associate = want
    for move, n in sorted(by_move.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>7,} lines   {move}")
    if apply_changes:
        session.commit()
        print(f"APPLIED: {changes:,} lines re-attributed.")
    else:
        print(f"DRY RUN: {changes:,} lines would change (re-run with --apply).")
    return changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod", action="store_true", help="run against the prod RDS instead of the local DB")
    ap.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    args = ap.parse_args()

    if not args.prod:
        from app.db import SessionLocal
        session = SessionLocal()
        try:
            reattribute(session, args.apply)
        finally:
            session.close()
        return

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    sg, cidr = rds_sg(), f"{public_ip()}/32"
    print(f"RDS SG {sg}; this machine {cidr}")
    authorize(sg, cidr)
    try:
        engine = create_engine(prod_url(), connect_args={"connect_timeout": 15}, future=True)
        session = sessionmaker(bind=engine)()
        try:
            reattribute(session, args.apply)
        finally:
            session.close()
    finally:
        revoke(sg, cidr)


if __name__ == "__main__":
    main()
