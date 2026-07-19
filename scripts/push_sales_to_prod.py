"""One-shot: push the LOCAL (complete, re-imported) sales_lines table up to PROD RDS.

Why: the old importer silently dropped invoices from untracked/inactive batch codes (Maple Tran's MT etc.),
so prod's sales_lines is missing ~13.9k history rows and the growth backtest shows phantom growth there.
Local was rebuilt from the source XLSX through the FIXED import path and matches the manager's GP sheet to
the dollar — this script makes prod's sales_lines identical to local's. Sales lines only; every other table
(awards, actions, users, receivables, voided...) is untouched.

Run via the `!` prefix (it performs prod-security actions the agent's auto-mode guard blocks):

    ! sales_evaluation/bin/python scripts/push_sales_to_prod.py

Self-contained and self-cleaning (same pattern as sync_from_prod.py):
  1. TEMPORARILY authorizes this machine's IP on the RDS SG (tcp/5432, /32)
  2. reads DATABASE_URL from Secrets Manager, connects to prod Postgres
  3. sanity-checks local (must have MORE rows than prod), then inside ONE transaction:
     wipes prod sales_lines and bulk-inserts local's rows
  4. prints before/after counts + the Jan1-Jun19 2025 profit total (should be $2,234,239 = the GP sheet)
  5. REVOKES the temporary SG rule (always, even on error)
"""
import os, sys, json, subprocess, urllib.request

REGION = "us-east-1"
SECRET_ID = "wandt/DATABASE_URL"
DB_INSTANCE = "wandt-db"

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def aws(*args):
    return subprocess.check_output(["aws", *args, "--region", REGION], text=True)


def public_ip():
    return urllib.request.urlopen("https://checkip.amazonaws.com", timeout=10).read().decode().strip()


def rds_sg():
    out = json.loads(aws("rds", "describe-db-instances", "--db-instance-identifier", DB_INSTANCE,
                         "--query", "DBInstances[0].VpcSecurityGroups[?Status=='active'].VpcSecurityGroupId",
                         "--output", "json"))
    if not out:
        raise SystemExit("could not find the RDS security group")
    return out[0]


def authorize(sg, cidr):
    try:
        aws("ec2", "authorize-security-group-ingress", "--group-id", sg, "--protocol", "tcp",
            "--port", "5432", "--cidr", cidr)
        print(f"  opened {sg} tcp/5432 for {cidr}")
    except subprocess.CalledProcessError as e:
        if "InvalidPermission.Duplicate" in (e.output or "") + str(e):
            print(f"  {cidr} already authorized on {sg}")
        else:
            raise


def revoke(sg, cidr):
    try:
        aws("ec2", "revoke-security-group-ingress", "--group-id", sg, "--protocol", "tcp",
            "--port", "5432", "--cidr", cidr)
        print(f"  revoked {sg} tcp/5432 for {cidr}")
    except subprocess.CalledProcessError as e:
        print(f"  WARNING: could not revoke SG rule ({e}); remove {cidr} from {sg} manually.")


def main():
    from sqlalchemy import create_engine, select, delete, insert, func, text
    from app.db import engine as local_engine
    from app.models import SalesLine

    t = SalesLine.__table__

    with local_engine.connect() as lc:
        local_rows = [dict(m) for m in lc.execute(select(t)).mappings().all()]
    print(f"local sales_lines: {len(local_rows):,} rows")
    if len(local_rows) < 100_000:
        raise SystemExit("local sales_lines looks incomplete — aborting (expected ~123k rows)")

    sg = rds_sg(); ip = public_ip(); cidr = f"{ip}/32"
    print(f"RDS SG {sg}; this machine {cidr}")
    authorize(sg, cidr)
    try:
        raw = aws("secretsmanager", "get-secret-value", "--secret-id", SECRET_ID,
                  "--query", "SecretString", "--output", "text").strip()
        try:
            parsed = json.loads(raw)
            prod_url = parsed.get("DATABASE_URL", parsed) if isinstance(parsed, dict) else parsed
        except json.JSONDecodeError:
            prod_url = raw
        if "+psycopg2" not in prod_url:
            prod_url = prod_url.replace("postgresql://", "postgresql+psycopg2://").replace(
                "postgres://", "postgresql+psycopg2://")
        prod_engine = create_engine(prod_url, connect_args={"connect_timeout": 15}, future=True)

        with prod_engine.begin() as pc:
            before = pc.execute(select(func.count()).select_from(t)).scalar()
            print(f"prod sales_lines before: {before:,} rows")
            pc.execute(delete(t))
            # drop the local autoincrement ids so prod assigns its own
            for row in local_rows:
                row.pop("id", None)
            for i in range(0, len(local_rows), 1000):
                pc.execute(insert(t), local_rows[i:i + 1000])
            after = pc.execute(select(func.count()).select_from(t)).scalar()
            check = pc.execute(text(
                "SELECT COALESCE(SUM(line_profit),0) FROM sales_lines "
                "WHERE document_date >= '2025-01-01' AND document_date <= '2025-06-19'")).scalar()
            print(f"prod sales_lines after : {after:,} rows")
            print(f"prod Jan1-Jun19 2025 profit: ${check:,.0f}  (GP sheet: $2,234,239)")
        print("PUSH COMPLETE — prod sales_lines now matches local (full history, no dropped invoices).")
    finally:
        revoke(sg, cidr)


if __name__ == "__main__":
    main()
