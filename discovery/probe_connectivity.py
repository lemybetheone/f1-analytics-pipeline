"""Phase 0 / Gate 3 — prove connectivity to the warehouse and the lake.

Why this exists
---------------
SECURITY §9's pre-flight checklist requires a connectivity smoke test to pass
*before* pipeline code is written, using environment variables only. Proving it
now means a Phase 1 failure is a bug in the pipeline rather than an unresolved
question about whether the connection was ever possible.

Two things it deliberately does NOT do:

* It never prints a credential. Failures report the *kind* of failure and the
  host being dialled, never the password or the full DSN.
* It makes no changes. This run is read-only; creating the project schemas is
  a separate, explicit step, so a connection problem and a permission problem
  cannot be confused with each other.

Usage
-----
    python discovery/probe_connectivity.py            # warehouse + lake
    python discovery/probe_connectivity.py --warehouse-only
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"


def load_env(path: Path) -> dict[str, str]:
    """Minimal .env reader.

    Deliberately dependency-free: proving connectivity should not depend on
    another package being installed correctly. Handles `KEY=value`, comments,
    blank lines, and quoted values (a password containing `#` must survive).
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        key, raw = key.strip(), raw.strip()

        if raw[:1] in {"'", '"'} and raw[-1:] == raw[:1] and len(raw) > 1:
            raw = raw[1:-1]  # quoted: take verbatim, including any '#'
        else:
            raw = raw.split(" #", 1)[0].strip()  # unquoted: strip trailing comment

        values[key] = raw
    return values


def require(env: dict[str, str], *keys: str) -> tuple[dict[str, str], list[str]]:
    """Split requested settings into those present and those missing/empty."""
    present = {k: env[k] for k in keys if env.get(k)}
    missing = [k for k in keys if not env.get(k)]
    return present, missing


def check_warehouse(env: dict[str, str]) -> bool:
    print("Warehouse (Postgres)")

    settings, missing = require(
        env, "WAREHOUSE_HOST", "WAREHOUSE_PORT", "WAREHOUSE_DATABASE",
        "WAREHOUSE_USER", "WAREHOUSE_PASSWORD",
    )
    if missing:
        print(f"  FAIL  not set in .env: {', '.join(missing)}")
        return False

    try:
        import psycopg
    except ImportError:
        print("  FAIL  psycopg not installed — run: pip install -r requirements.txt")
        return False

    host, user = settings["WAREHOUSE_HOST"], settings["WAREHOUSE_USER"]
    print(f"  host    {host}:{settings['WAREHOUSE_PORT']}")
    print(f"  user    {user}")

    # Pooler endpoints expect the project ref appended to the role name. Getting
    # this wrong produces a "Tenant or user not found" that reads like a bad
    # password, so name it before it wastes an hour.
    if "pooler.supabase.com" in host and "." not in user:
        print("  WARN    pooler host with a bare username — Supabase expects "
              "'<role>.<project-ref>'")

    try:
        with psycopg.connect(
            host=host,
            port=settings["WAREHOUSE_PORT"],
            dbname=settings["WAREHOUSE_DATABASE"],
            user=user,
            password=settings["WAREHOUSE_PASSWORD"],
            connect_timeout=15,
        ) as conn, conn.cursor() as cur:
            cur.execute("select version(), current_database(), current_user, "
                        "current_setting('server_version_num')")
            version, database, whoami, version_num = cur.fetchone()

            cur.execute("select schema_name from information_schema.schemata "
                        "order by schema_name")
            schemas = [row[0] for row in cur.fetchall()]

        print(f"  OK      connected as {whoami} to {database}")
        print(f"  server  {version.split(' on ')[0]}  (version_num {version_num})")

        wanted = [env.get(k) for k in
                  ("WAREHOUSE_SCHEMA_RAW", "WAREHOUSE_SCHEMA_STAGING", "WAREHOUSE_SCHEMA_MARTS")]
        existing = [s for s in wanted if s and s in schemas]
        absent = [s for s in wanted if s and s not in schemas]
        print(f"  schemas present: {', '.join(existing) or 'none of the project schemas'}")
        if absent:
            print(f"           to create: {', '.join(absent)}  "
                  f"(run with --create-schemas)")
        return True

    except Exception as exc:  # noqa: BLE001 — report the class, never the secret
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        print(f"  FAIL    {type(exc).__name__}: {detail}")
        print("          Common causes: wrong pooler vs direct host, IPv6-only "
              "direct endpoint, password not yet propagated after a reset.")
        return False


def create_schemas(env: dict[str, str]) -> bool:
    """Create the three project schemas. Separate from the connectivity check."""
    import psycopg

    names = [env.get(k) for k in
             ("WAREHOUSE_SCHEMA_RAW", "WAREHOUSE_SCHEMA_STAGING", "WAREHOUSE_SCHEMA_MARTS")]
    names = [n for n in names if n]

    print("\nCreating schemas")
    try:
        with psycopg.connect(
            host=env["WAREHOUSE_HOST"], port=env["WAREHOUSE_PORT"],
            dbname=env["WAREHOUSE_DATABASE"], user=env["WAREHOUSE_USER"],
            password=env["WAREHOUSE_PASSWORD"], connect_timeout=15,
        ) as conn:
            with conn.cursor() as cur:
                for name in names:
                    # Identifiers come from .env, not user input, but quote them
                    # properly rather than interpolating raw text into DDL.
                    cur.execute(
                        psycopg.sql.SQL("create schema if not exists {}").format(
                            psycopg.sql.Identifier(name)
                        )
                    )
                    print(f"  OK      {name}")
            conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL    {type(exc).__name__}: {str(exc).splitlines()[0]}")
        return False


def check_lake(env: dict[str, str]) -> bool:
    print("\nLake (S3)")

    _, missing = require(env, "LAKE_BUCKET", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    if missing:
        print(f"  SKIP    not configured yet: {', '.join(missing)}")
        return False

    try:
        import boto3
    except ImportError:
        print("  FAIL    boto3 not installed — run: pip install -r requirements.txt")
        return False

    bucket = env["LAKE_BUCKET"]
    print(f"  bucket  {bucket} ({env.get('LAKE_REGION', 'unset region')})")

    # Which identity is this key? A root access key cannot be scoped and cannot
    # be revoked without disrupting the whole account, so SECURITY §2 rules it
    # out. Assert it here rather than trusting that setup was done correctly.
    identity_ok = True
    try:
        sts = boto3.client(
            "sts",
            region_name=env.get("LAKE_REGION"),
            aws_access_key_id=env["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=env["AWS_SECRET_ACCESS_KEY"],
        )
        arn = sts.get_caller_identity()["Arn"]
        if arn.endswith(":root"):
            print("  FAIL    this is a ROOT access key — unscoped and unrevokable.")
            print("          Create an IAM user with the lake policy, then delete "
                  "this key (SECURITY §2).")
            identity_ok = False
        else:
            print(f"  identity {arn.split(':')[-1]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN    could not resolve identity: {type(exc).__name__}")

    try:
        client = boto3.client(
            "s3",
            region_name=env.get("LAKE_REGION"),
            aws_access_key_id=env["AWS_ACCESS_KEY_ID"],
            aws_secret_access_key=env["AWS_SECRET_ACCESS_KEY"],
        )
        client.head_bucket(Bucket=bucket)
        listing = client.list_objects_v2(Bucket=bucket, MaxKeys=1)
        print(f"  OK      reachable, {listing.get('KeyCount', 0)} object(s) at root")
        return identity_ok
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL    {type(exc).__name__}: {str(exc).splitlines()[0]}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(ENV_FILE))
    parser.add_argument("--warehouse-only", action="store_true")
    parser.add_argument("--create-schemas", action="store_true",
                        help="create the three project schemas (the only write this makes)")
    args = parser.parse_args()

    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"No .env at {env_path} — copy .env.example and fill it in.")
        return 1

    # Real environment variables win over the file, so CI can inject secrets
    # without a .env ever existing on disk.
    env = load_env(env_path) | {k: v for k, v in os.environ.items() if v}

    print(f"Reading configuration from {env_path.name}\n")
    warehouse_ok = check_warehouse(env)

    if warehouse_ok and args.create_schemas:
        warehouse_ok = create_schemas(env)

    lake_ok = False if args.warehouse_only else check_lake(env)

    print("\n" + "-" * 60)
    print(f"  warehouse : {'PASS' if warehouse_ok else 'FAIL'}")
    print(f"  lake      : {'PASS' if lake_ok else 'not configured' if args.warehouse_only or not env.get('LAKE_BUCKET') else 'FAIL'}")
    print("-" * 60)

    return 0 if warehouse_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
