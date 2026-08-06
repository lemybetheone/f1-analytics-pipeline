"""Apply committed migrations in order, exactly once, as the pipeline role.

Why this exists
---------------
SECURITY §7 requires schema changes to be applied through a repeatable process
rather than pasted into a console. Two concrete problems it solves:

* **Ownership.** The Supabase SQL editor connects as `postgres`, so any table
  created there is owned by `postgres` — not by `f1_pipeline`. That is the
  ownership split migration 002 exists to avoid. This runner connects with the
  pipeline credentials, so objects are owned correctly from creation.

* **"Did that already run?"** `create table if not exists` silently skips
  existing objects, so a re-run looks successful whether or not it changed
  anything. A recorded ledger of applied versions is the difference between
  knowing and assuming.

Each migration runs in **one transaction together with the ledger insert**, so
a failure leaves neither the change nor a record claiming it was applied.

Usage
-----
    python migrations/apply.py --status     # what is applied, what is pending
    python migrations/apply.py              # apply everything pending
    python migrations/apply.py --dry-run    # list what would run
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"

# Configuration comes from ingestion, not discovery. Decision 9 isolates the
# two directions and discovery/ is throwaway; the migration runner is part of
# the pipeline, so it depends on the pipeline's own config module.
sys.path.insert(0, str(REPO_ROOT))

from ingestion.config import read_env_file  # noqa: E402

# Migrations are `NNN_description.sql`. The numeric prefix is the version and
# defines the order; anything else in the directory is documentation.
MIGRATION_PATTERN = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")

# The ledger lives in `raw`, not `public`. The pipeline role owns raw/staging/
# marts and nothing else, so `public` is not writable by it — least privilege
# working as intended. A dedicated `meta` schema would read better, but
# creating one needs CREATE on the database, which the role deliberately lacks,
# and the runner cannot bootstrap a schema it has no rights to make.
LEDGER_DDL = """
create table if not exists raw.schema_migrations (
    version     text        primary key,
    name        text        not null,
    checksum    text        not null,
    applied_at  timestamptz not null default now()
)
"""


def discover() -> list[tuple[str, str, Path]]:
    """Return (version, name, path) for every migration, in version order."""
    found = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        match = MIGRATION_PATTERN.match(path.name)
        if not match:
            print(f"  ! ignoring {path.name} — expected NNN_description.sql")
            continue
        found.append((match.group(1), match.group(2), path))
    return found


def checksum(path: Path) -> str:
    """Fingerprint the file so an edit to an applied migration is detectable.

    Editing an applied migration means two databases claim the same version
    with different contents — the failure mode that makes migrations
    untrustworthy. Detecting it is the whole reason to store a checksum.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def connect(env: dict[str, str]):
    import psycopg

    missing = [k for k in ("WAREHOUSE_HOST", "WAREHOUSE_PORT", "WAREHOUSE_DATABASE",
                           "WAREHOUSE_USER", "WAREHOUSE_PASSWORD") if not env.get(k)]
    if missing:
        raise SystemExit(f"Missing configuration in .env: {', '.join(missing)}")

    return psycopg.connect(
        host=env["WAREHOUSE_HOST"],
        port=env["WAREHOUSE_PORT"],
        dbname=env["WAREHOUSE_DATABASE"],
        user=env["WAREHOUSE_USER"],
        password=env["WAREHOUSE_PASSWORD"],
        connect_timeout=15,
    )


def applied_versions(conn) -> dict[str, tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute(LEDGER_DDL)
        conn.commit()
        cur.execute("select version, checksum, applied_at::text "
                    "from raw.schema_migrations order by version")
        return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="show state and exit")
    parser.add_argument("--dry-run", action="store_true", help="list pending, apply nothing")
    parser.add_argument(
        "--mark-applied", nargs="+", metavar="VERSION",
        help="record these versions as applied WITHOUT running them. For adopting "
             "a database whose early migrations were applied by hand — re-running "
             "them would fail on objects that already exist.",
    )
    args = parser.parse_args()

    env = read_env_file(REPO_ROOT / ".env")
    migrations = discover()
    if not migrations:
        print("No migrations found.")
        return 1

    with connect(env) as conn:
        with conn.cursor() as cur:
            cur.execute("select current_user")
            whoami = cur.fetchone()[0]
        print(f"Connected as {whoami}\n")

        applied = applied_versions(conn)

        if args.mark_applied:
            by_version = {v: (n, p) for v, n, p in migrations}
            for version in args.mark_applied:
                if version not in by_version:
                    print(f"  ! no migration {version} on disk")
                    return 1
                if version in applied:
                    print(f"  {version} already recorded")
                    continue
                name, path = by_version[version]
                with conn.cursor() as cur:
                    cur.execute(
                        "insert into raw.schema_migrations (version, name, checksum) "
                        "values (%s, %s, %s)",
                        (version, name, checksum(path)),
                    )
                conn.commit()
                print(f"  marked {version} {name} as applied (not executed)")
            return 0

        pending = []

        for version, name, path in migrations:
            digest = checksum(path)
            if version in applied:
                recorded, when = applied[version]
                if recorded == digest:
                    print(f"  {version}  {name:32s} applied {when}")
                else:
                    # Loud, not fatal: 001 and 002 were applied by hand before
                    # this runner existed, so their recorded checksum is
                    # backfilled and a mismatch there is expected once.
                    print(f"  {version}  {name:32s} APPLIED, BUT FILE HAS CHANGED "
                          f"since (recorded {recorded}, now {digest})")
            else:
                print(f"  {version}  {name:32s} PENDING")
                pending.append((version, name, path, digest))

        if args.status:
            return 0
        if not pending:
            print("\nNothing to apply.")
            return 0
        if args.dry_run:
            print(f"\nWould apply {len(pending)}: "
                  f"{', '.join(v for v, _, _, _ in pending)}")
            return 0

        print()
        for version, name, path, digest in pending:
            sql = path.read_text(encoding="utf-8")
            try:
                # The migration and its ledger entry share one transaction, so
                # a failure can never leave a record claiming success.
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute(
                        "insert into raw.schema_migrations (version, name, checksum) "
                        "values (%s, %s, %s)",
                        (version, name, digest),
                    )
                conn.commit()
                print(f"  applied {version} {name}")
            except Exception as exc:  # noqa: BLE001 — report and stop the run
                conn.rollback()
                print(f"  FAILED  {version} {name}")
                print(f"          {type(exc).__name__}: {str(exc).splitlines()[0]}")
                print("          Nothing was recorded; fix and re-run.")
                return 1

    print("\nAll migrations applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
