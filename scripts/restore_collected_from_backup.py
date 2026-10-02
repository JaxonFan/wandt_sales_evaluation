"""One-shot: put back the PAID (collected) invoices that a single-month paid upload wiped on 2026-10-01.

Run this via the `!` prefix (it performs prod-security actions the agent's auto-mode guard blocks):

    ! sales_evaluation/bin/python scripts/restore_collected_from_backup.py

What it does, self-contained and self-cleaning:
  1. restores a TEMPORARY copy of the prod database as it was at RESTORE_TIME (just before the upload),
     from RDS's automated backups — prod itself is never rolled back
  2. TEMPORARILY authorizes ingress on tcp/5432 for THIS IP only (a /32 rule)
  3. reads `collected_invoices` from the temporary copy and ADDS the ones prod is missing
     (additive only: today's September invoices and everything else in prod stay as they are)
  4. REVOKES the temporary SG rule and DELETES the temporary copy (always, even on error)

Safe to re-run: a second run finds nothing missing and adds nothing.
"""
import os, sys, json, subprocess, urllib.request, datetime

REGION = "us-east-1"
SECRET_ID = "wandt/DATABASE_URL"
DB_INSTANCE = "wandt-db"
TEMP_INSTANCE = "wandt-db-restore-tmp"
RESTORE_TIME = "2026-10-01T18:20:00Z"   # the wiping paid upload landed at 18:29 UTC

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)


def aws(*args):
    return subprocess.check_output(["aws", *args, "--region", REGION], text=True, stderr=subprocess.STDOUT)


def public_ip():
    return urllib.request.urlopen("https://checkip.amazonaws.com", timeout=10).read().decode().strip()


def describe(instance):
    try:
        return json.loads(aws("rds", "describe-db-instances", "--db-instance-identifier", instance,
                              "--query", "DBInstances[0]", "--output", "json"))
    except subprocess.CalledProcessError as e:
        if "DBInstanceNotFound" in (e.output or ""):
            return None
        raise


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


def delete_temp():
    try:
        aws("rds", "delete-db-instance", "--db-instance-identifier", TEMP_INSTANCE,
            "--skip-final-snapshot", "--delete-automated-backups")
        print(f"  deleting temporary copy {TEMP_INSTANCE}")
    except subprocess.CalledProcessError as e:
        print(f"  WARNING: could not delete {TEMP_INSTANCE} ({e.output}); delete it manually to stop billing.")


def prod_url():
    raw = aws("secretsmanager", "get-secret-value", "--secret-id", SECRET_ID,
              "--query", "SecretString", "--output", "text").strip()
    try:                                   # secret may be a JSON blob or a plain URL
        parsed = json.loads(raw)
        url = parsed.get("DATABASE_URL", parsed) if isinstance(parsed, dict) else parsed
    except json.JSONDecodeError:
        url = raw
    if "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://").replace(
            "postgres://", "postgresql+psycopg2://")
    return url


def main():
    from sqlalchemy import create_engine, text

    prod = describe(DB_INSTANCE)
    sg = prod["VpcSecurityGroups"][0]["VpcSecurityGroupId"]
    prod_host = prod["Endpoint"]["Address"]
    cidr = f"{public_ip()}/32"
    print(f"RDS SG {sg}; this machine {cidr}")

    try:
        # 1) temporary copy of prod as of RESTORE_TIME (same network settings as prod, SG-gated)
        if describe(TEMP_INSTANCE) is None:
            print(f"restoring {TEMP_INSTANCE} to {RESTORE_TIME} (takes ~10-20 min) ...")
            aws("rds", "restore-db-instance-to-point-in-time",
                "--source-db-instance-identifier", DB_INSTANCE,
                "--target-db-instance-identifier", TEMP_INSTANCE,
                "--restore-time", RESTORE_TIME,
                "--db-instance-class", prod["DBInstanceClass"],
                "--db-subnet-group-name", prod["DBSubnetGroup"]["DBSubnetGroupName"],
                "--vpc-security-group-ids", sg,
                "--publicly-accessible", "--no-multi-az")
        else:
            print(f"{TEMP_INSTANCE} already exists — reusing it")
        aws("rds", "wait", "db-instance-available", "--db-instance-identifier", TEMP_INSTANCE)
        temp_host = describe(TEMP_INSTANCE)["Endpoint"]["Address"]
        print(f"  temporary copy is up at {temp_host}")

        # 2) + 3) read the old collected set, add what prod is missing
        authorize(sg, cidr)
        url = prod_url()
        if prod_host not in url:
            raise SystemExit("DATABASE_URL does not point at the prod instance — stopping, nothing changed.")
        prod_engine = create_engine(url, connect_args={"connect_timeout": 15}, future=True)
        temp_engine = create_engine(url.replace(prod_host, temp_host), connect_args={"connect_timeout": 15},
                                    future=True)
        with temp_engine.connect() as tc:
            before = {r[0] for r in tc.execute(text("select sop_number from collected_invoices"))}
        with prod_engine.begin() as pc:
            current = {r[0] for r in pc.execute(text("select sop_number from collected_invoices"))}
            missing = sorted(before - current)
            print(f"  collected before the upload: {len(before):,}")
            print(f"  collected in prod right now:  {len(current):,}")
            print(f"  to put back:                  {len(missing):,}")
            now = datetime.datetime.utcnow()
            for i in range(0, len(missing), 1000):
                pc.execute(text("insert into collected_invoices (sop_number, reported_at) values (:s, :t)"),
                           [dict(s=sop, t=now) for sop in missing[i:i + 1000]])
            total = pc.execute(text("select count(*) from collected_invoices")).scalar()
        print(f"RESTORE COMPLETE — prod now holds {total:,} collected invoices.")
    finally:
        revoke(sg, cidr)
        delete_temp()


if __name__ == "__main__":
    main()
