"""One-shot: mirror the PROD RDS database into the local SQLite DB so local == prod.

Run this via the `!` prefix (it performs prod-security actions the agent's auto-mode guard blocks):

    ! sales_evaluation/bin/python scripts/sync_from_prod.py

What it does, self-contained and self-cleaning:
  1. looks up the RDS instance's security group + this machine's public IP
  2. TEMPORARILY authorizes ingress on tcp/5432 for THIS IP only (a /32 rule)
  3. reads DATABASE_URL from Secrets Manager, connects to prod Postgres
  4. copies every data table into the local SQLite DB (backs local up first;
     PRESERVES the local `users` table so your local logins keep working)
  5. REVOKES the temporary SG rule (always, even on error)

After it finishes, local mirrors prod and no further prod access is needed.
"""
import os, sys, json, shutil, subprocess, urllib.request, datetime

REGION = "us-east-1"
SECRET_ID = "wandt/DATABASE_URL"
DB_INSTANCE = "wandt-db"
SKIP_TABLES = {"users", "audit_log"}   # keep local logins; audit log not needed for the engine

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
    from sqlalchemy import create_engine, select, delete, insert
    from app.db import engine as local_engine, DATABASE_URL as LOCAL_URL
    from app.models import Base

    # 0) back up the local sqlite file
    if LOCAL_URL.startswith("sqlite"):
        path = LOCAL_URL.split("sqlite:///")[-1]
        if os.path.exists(path):
            bak = f"{path}.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}"
            shutil.copy2(path, bak); print(f"backed up local DB -> {bak}")

    sg = rds_sg(); ip = public_ip(); cidr = f"{ip}/32"
    print(f"RDS SG {sg}; this machine {cidr}")
    authorize(sg, cidr)
    try:
        raw = aws("secretsmanager", "get-secret-value", "--secret-id", SECRET_ID,
                  "--query", "SecretString", "--output", "text").strip()
        try:                                   # secret may be a JSON blob or a plain URL
            parsed = json.loads(raw)
            prod_url = parsed.get("DATABASE_URL", parsed) if isinstance(parsed, dict) else parsed
        except json.JSONDecodeError:
            prod_url = raw
        # normalize the driver scheme for SQLAlchemy + psycopg2
        if "+psycopg2" not in prod_url:
            prod_url = prod_url.replace("postgresql://", "postgresql+psycopg2://").replace(
                "postgres://", "postgresql+psycopg2://")
        prod_engine = create_engine(prod_url, connect_args={"connect_timeout": 15}, future=True)

        Base.metadata.create_all(local_engine)   # ensure local schema exists
        tables = list(Base.metadata.sorted_tables)
        with prod_engine.connect() as pc, local_engine.begin() as lc:
            # wipe local (children first) then fill (parents first), skipping preserved tables
            for t in reversed(tables):
                if t.name in SKIP_TABLES:
                    continue
                lc.execute(delete(t))
            for t in tables:
                if t.name in SKIP_TABLES:
                    print(f"  skip {t.name} (preserved)"); continue
                rows = [dict(m) for m in pc.execute(select(t)).mappings().all()]
                if rows:
                    for i in range(0, len(rows), 1000):
                        lc.execute(insert(t), rows[i:i + 1000])
                print(f"  {t.name:20s} {len(rows):>7,} rows")
        print("SYNC COMPLETE — local now mirrors prod.")
    finally:
        revoke(sg, cidr)


if __name__ == "__main__":
    main()
