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
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
VENV = REPO_ROOT / ".venv"
IS_WINDOWS = platform.system() == "Windows"
BIN = VENV / ("Scripts" if IS_WINDOWS else "bin")

DEFAULT_SEASON = "2024"


def venv_exe(name: str) -> Path:
    return BIN / (f"{name}.exe" if IS_WINDOWS else name)


def run(command: list[str], *, why: str) -> None:
    """Run a command, echoing it so the task runner never hides what it does."""
    print(f"\n>> {why}\n   {' '.join(str(part) for part in command)}")
    result = subprocess.run(command, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


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

    KNOWN LIMITATION — the rate budget is per-process. Each `ingestion.pipeline`
    invocation below starts a fresh RateLimiter, so a full ingest spends ~85
    calls while no single process sees more than ~72. Within one invocation the
    limiter is shared across entities and correct; across invocations it is
    blind. That is tolerable at this size and *not* tolerable for the historical
    backfill, which spans hours and will be restarted: a resumed run would reset
    its own view of the budget and could exceed 500/hour without noticing.
    Fixing it means persisting call timestamps outside the process — recorded
    here so the backfill does not inherit the assumption silently.
    """
    python = require_venv()
    base = [str(python), "-m", "ingestion.pipeline"]

    if not args.skip_reference:
        run([*base, "--all-reference"], why="reference data (all seasons)")

    run([*base, "--all-season", "--season", args.season],
        why=f"session facts for {args.season}")
    run([*base, "--all-race", "--season", args.season],
        why=f"pit stops and standings for {args.season} (one call per round)")


TASKS = {
    "setup": task_setup,
    "lint": task_lint,
    "test": task_test,
    "check": task_check,
    "migrate": task_migrate,
    "verify": task_verify,
    "ingest": task_ingest,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="task", required=True)

    for name in TASKS:
        task_parser = sub.add_parser(name, help=TASKS[name].__doc__)
        if name == "ingest":
            task_parser.add_argument("--season", default=DEFAULT_SEASON)
            task_parser.add_argument("--skip-reference", action="store_true",
                                     help="reference data is already loaded")
        if name == "migrate":
            task_parser.add_argument("--status", action="store_true",
                                     help="show state without applying anything")

    args = parser.parse_args()
    TASKS[args.task](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
