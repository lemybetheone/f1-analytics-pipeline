"""Task runner. The one place the project's common commands are defined.

ARCHITECTURE §11 asks for a Makefile "or equivalent" so the command surface is
small enough to fit in a README and CI has a single source of truth. The logic
lives here rather than in the Makefile for one practical reason: `make` is not
installed on Windows by default, and a task runner the author cannot run is
worse than none. Python is already a hard dependency, so this works everywhere
the project does.

The `Makefile` is a thin wrapper that delegates to these same commands, so
`make ingest` and `python tasks.py ingest` cannot drift apart.

Usage
-----
    python tasks.py setup        # create .venv, install pinned dependencies
    python tasks.py check        # lint + tests, exactly what CI runs
    python tasks.py migrate      # apply pending migrations
    python tasks.py verify       # prove warehouse + lake connectivity
    python tasks.py ingest       # full ingest for one season (default 2024)
    python tasks.py backfill     # the historical seasons, newest first
    python tasks.py transform    # build and test the dbt models
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DBT_DIR = REPO_ROOT / "dbt"
VENV = REPO_ROOT / ".venv"
LOG_DIR = REPO_ROOT / "logs"
IS_WINDOWS = platform.system() == "Windows"
BIN = VENV / ("Scripts" if IS_WINDOWS else "bin")

DEFAULT_SEASON = "2024"

# The first season the source covers. Current seasons are `ingest`'s job.
FIRST_SEASON = 1950
LAST_HISTORICAL_SEASON = 2023

# Three in a row is the network, the credentials or the source — not the data.
MAX_CONSECUTIVE_BACKFILL_FAILURES = 3


def venv_exe(name: str) -> Path:
    return BIN / (f"{name}.exe" if IS_WINDOWS else name)


def run(command: list[str], *, why: str, env: dict[str, str] | None = None,
        cwd: Path | None = None) -> None:
    """Run a command, echoing it so the task runner never hides what it does."""
    print(f"\n>> {why}\n   {' '.join(str(part) for part in command)}")
    result = subprocess.run(command, cwd=cwd or REPO_ROOT, env=env)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def dbt_environment() -> dict[str, str]:
    """Process environment for dbt, with `.env` merged in.

    dbt's `env_var()` reads the **process** environment and knows nothing about
    `.env` files. Without this, `dbt build` fails with "Env var required but
    not provided: 'WAREHOUSE_PASSWORD'" — which reads like a dbt configuration
    problem and is really just a missing bridge.

    Loading it here rather than adding python-dotenv keeps one config path:
    `ingestion/config.py` already parses `.env`, quoted values and all, and the
    pipeline and the transformations now demonstrably read the same file.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from ingestion.config import read_env_file  # noqa: PLC0415

    environment = dict(os.environ)
    environment.update(read_env_file(REPO_ROOT / ".env"))

    # dbt looks in ~/.dbt by default; ours is committed alongside the project.
    environment["DBT_PROFILES_DIR"] = str(DBT_DIR)
    return environment


def require_venv() -> Path:
    python = venv_exe("python")
    if not python.exists():
        raise SystemExit(
            "No virtual environment found.\n"
            "Run:  python tasks.py setup"
        )
    return python


# --------------------------------------------------------------------------
# tasks
# --------------------------------------------------------------------------


def task_setup(_args) -> None:
    """Create the virtual environment and install pinned dependencies."""
    if not VENV.exists():
        # sys.executable, not the venv python — this is what bootstraps it.
        run([sys.executable, "-m", "venv", str(VENV)], why="create .venv")
    else:
        print(">> .venv already exists")

    python = venv_exe("python")
    run([str(python), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
        why="upgrade pip")
    run([str(python), "-m", "pip", "install", "-r", "requirements.txt", "--quiet"],
        why="install pinned dependencies")

    # Hooks are not versioned by git, so pointing at the committed directory is
    # a per-clone step. Doing it here means a fresh clone gets the pre-push
    # checks without having to know they exist.
    run(["git", "config", "core.hooksPath", "hooks"],
        why="install the committed pre-push hook")

    print("\nSetup complete. Next: copy .env.example to .env and fill it in.")


def task_lint(_args) -> None:
    require_venv()
    run([str(venv_exe("ruff")), "check", "."], why="lint")


def task_test(_args) -> None:
    require_venv()
    run([str(venv_exe("pytest")), "-q"], why="unit tests")


def task_check(args) -> None:
    """Exactly what CI runs, in the same order and the same invocation.

    Deliberately the console scripts rather than `python -m`: the two differ in
    sys.path handling, and that discrepancy once let the suite pass locally
    while failing in CI at collection.
    """
    task_lint(args)
    task_test(args)


def task_migrate(args) -> None:
    python = require_venv()
    command = [str(python), "migrations/apply.py"]
    if args.status:
        command.append("--status")
    run(command, why="apply pending migrations")


def task_verify(_args) -> None:
    python = require_venv()
    run([str(python), "discovery/probe_connectivity.py"],
        why="prove warehouse and lake connectivity")


def task_ingest(args) -> None:
    """Ingest one season end to end, in dependency order.

    Reference data first, and not merely by convention: the race-scoped
    entities read their rounds from `raw.races`, so running them against an
    empty reference layer would find no rounds and do nothing.

    Each step below is a separate process, which used to mean a separate rate
    budget: a full ingest spent ~85 calls while no single process saw more than
    ~72. The budget now lives in `raw.api_call_log`, so every process and every
    resumed run counts against the same hourly allowance.
    """
    python = require_venv()
    base = [str(python), "-m", "ingestion.pipeline"]

    if not args.skip_reference:
        run([*base, "--all-reference"], why="reference data (all seasons)")

    # Newest first (ARCHITECTURE decision 21): a dashboard needs one complete
    # recent season, not seventy partial ones, so the most useful data lands in
    # the first minutes of a long run rather than the last.
    for season in sorted(args.season, reverse=True):
        run([*base, "--all-season", "--season", season],
            why=f"session facts for {season}")
        run([*base, "--all-race", "--season", season],
            why=f"pit stops and standings for {season} (one call per round)")


def task_backfill(args) -> None:
    """Load the historical seasons, newest first, resumably.

    Separate from `ingest` rather than a flag on it, because the two have
    different failure models. `ingest` is a handful of calls for one season and
    can afford to die on the first error. A backfill is ~3,800 calls over ~7.6
    hours against a 500/hour budget, so it has to survive interruption, skip
    what it already did, and keep going when one season misbehaves.

    Three things `ingest` does not do, each of which cost something to learn:

    1. **`--resume` on every call.** Without it a restart re-fetches every
       completed scope, spending the budget twice for rows already held.

    2. **A pinned lake partition.** `--ingestion-date` otherwise defaults to
       *today, computed per process*, and this spawns two processes per season
       — so a run crossing midnight UTC scatters seasons across two partitions.
       The sharper edge is on resume: `--resume` skips an extract whose
       checkpoint says complete, then the loader looks for objects under the
       *new* partition prefix, finds none, and loads nothing. A silent no-op
       that reads exactly like success. **Resuming an interrupted backfill
       means passing the original `--partition`**, which is why it is echoed at
       the start and named in the log filename.

    3. **Failure is per-season, not fatal.** One awkward season should not end
       a seven-hour run. Three consecutive failures should, because that is the
       network, the credentials or the source rather than the data.
    """
    python = require_venv()
    seasons = [str(year) for year in range(args.to_season, args.from_season - 1, -1)]

    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"backfill_{args.partition}.log"

    print(f"seasons:   {seasons[0]} down to {seasons[-1]} ({len(seasons)})")
    print(f"partition: {args.partition}")
    print(f"log:       {log_path}")
    print(f"\nResume an interrupted run with:\n"
          f"  python tasks.py backfill --from {args.from_season} "
          f"--to {args.to_season} --partition {args.partition}\n")

    started = time.monotonic()
    failed_seasons: list[str] = []
    consecutive = 0

    with log_path.open("a", encoding="utf-8") as log:
        for index, season in enumerate(seasons, start=1):
            ok = True
            for phase, flag in (("session facts", "--all-season"),
                                ("pit stops and standings", "--all-race")):
                label = f"[{index:>2}/{len(seasons)}] {season}  {phase}"
                print(f"{label} ... ", end="", flush=True)
                log.write(f"\n{'=' * 70}\n{label}\n{'=' * 70}\n")
                log.flush()

                result = subprocess.run(
                    [str(python), "-m", "ingestion.pipeline", flag,
                     "--season", season, "--resume",
                     "--ingestion-date", args.partition],
                    cwd=REPO_ROOT, stdout=log, stderr=subprocess.STDOUT,
                )
                ok &= result.returncode == 0
                print("ok" if result.returncode == 0 else "FAILED")

            if ok:
                consecutive = 0
                continue

            failed_seasons.append(season)
            consecutive += 1
            if consecutive >= MAX_CONSECUTIVE_BACKFILL_FAILURES:
                print(f"\nStopping: {consecutive} consecutive seasons failed. "
                      f"That is systemic, not one bad season.\n"
                      f"See {log_path} and raw.failed_ingestions.")
                break

    hours = (time.monotonic() - started) / 3600
    print(f"\nBackfill finished in {hours:.2f}h.")
    print(f"seasons with failures: {', '.join(failed_seasons) or 'none'}")
    print("\nNext:  python tasks.py transform    # the tests are the real verdict")
    if failed_seasons:
        raise SystemExit(1)


def task_transform(args) -> None:
    """Build and test the dbt models.

    `dbt build` rather than `run` then `test`: build interleaves them, so a
    model whose test fails stops its dependents instead of letting a bad table
    propagate through the graph before anyone checks.
    """
    require_venv()
    dbt = str(venv_exe("dbt"))
    env = dbt_environment()

    run([dbt, "deps"], why="install dbt packages", env=env, cwd=DBT_DIR)

    if args.parse_only:
        # No database needed — validates refs, sources and Jinja only. CI runs
        # the same *check*, but not through here: this function requires a
        # `.venv`, and CI installs into the system Python. It therefore calls
        # `dbt deps && dbt parse` directly, with placeholder warehouse values,
        # since `env_var()` in profiles.yml has no defaults.
        #
        # This claimed "this is what CI runs" from Phase 2 until 2026-09-15,
        # during which CI ran no dbt step at all.
        run([dbt, "parse"], why="validate the project without a database",
            env=env, cwd=DBT_DIR)
        return

    command = [dbt, "build"]
    if args.select:
        command += ["--select", args.select]
    run(command, why="build models and run their tests", env=env, cwd=DBT_DIR)


def task_docs(_args) -> None:
    """Generate the lineage graph and column docs (SECURITY §6: generated, not hand-written)."""
    require_venv()
    dbt = str(venv_exe("dbt"))
    env = dbt_environment()
    run([dbt, "docs", "generate"], why="generate dbt docs", env=env, cwd=DBT_DIR)
    print("\nServe them with:  dbt docs serve --profiles-dir .   (from dbt/)")


TASKS = {
    "setup": task_setup,
    "transform": task_transform,
    "docs": task_docs,
    "lint": task_lint,
    "test": task_test,
    "check": task_check,
    "migrate": task_migrate,
    "verify": task_verify,
    "ingest": task_ingest,
    "backfill": task_backfill,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="task", required=True)

    for name in TASKS:
        task_parser = sub.add_parser(name, help=TASKS[name].__doc__)
        if name == "ingest":
            task_parser.add_argument("--season", nargs="+", default=[DEFAULT_SEASON],
                                     help="one or more seasons; loaded newest first")
            task_parser.add_argument("--skip-reference", action="store_true",
                                     help="reference data is already loaded")
        if name == "backfill":
            task_parser.add_argument("--from", dest="from_season", type=int,
                                     default=FIRST_SEASON,
                                     help=f"oldest season, inclusive "
                                          f"(default {FIRST_SEASON})")
            task_parser.add_argument("--to", dest="to_season", type=int,
                                     default=LAST_HISTORICAL_SEASON,
                                     help=f"newest season, inclusive (default "
                                          f"{LAST_HISTORICAL_SEASON}; current "
                                          f"seasons are `ingest`'s job)")
            # Not merely a default: passing the ORIGINAL date is what makes a
            # resumed run load rather than silently skip. See task_backfill.
            task_parser.add_argument("--partition",
                                     default=datetime.now(UTC).strftime("%Y-%m-%d"),
                                     help="lake partition (default: today UTC). "
                                          "To resume, pass the original date")
        if name == "migrate":
            task_parser.add_argument("--status", action="store_true",
                                     help="show state without applying anything")
        if name == "transform":
            task_parser.add_argument("--select",
                                     help="dbt selector, e.g. stg_drivers or staging")
            task_parser.add_argument("--parse-only", action="store_true",
                                     help="validate refs and syntax without a database")

    args = parser.parse_args()
    TASKS[args.task](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
