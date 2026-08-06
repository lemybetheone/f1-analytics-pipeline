"""Guard the promises the repository makes about secrets and line endings.

These are not unit tests of pipeline logic — that arrives with the ingestion
code. They exist because the failure they prevent is unrecoverable: a committed
credential is public the moment it is pushed, and rotating it afterwards does
not un-publish it. SECURITY §1 states the rules; this is the enforcement, since
a rule that is only written down is a rule that eventually gets missed at 1am.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Settings in .env.example that must never carry a real value. Non-secret
# settings (base URLs, region, schema names) are deliberately excluded — they
# are useful defaults, not credentials.
SECRET_KEYS = (
    "WAREHOUSE_PASSWORD",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "LAKE_BUCKET",
)

# Shapes that should never appear in a tracked file.
#
# Matched on the *shape of a real credential*, not on the variable name. An
# earlier version flagged any `AWS_SECRET_ACCESS_KEY=<anything>`, which fired on
# a test fixture using a one-character dummy. A guard that cries wolf gets
# disabled — the same alert-fatigue argument SECURITY §4 makes about
# over-testing. So: AWS secrets are exactly 40 base64-ish characters, and access
# key ids are AKIA plus 16 uppercase alphanumerics.
CREDENTIAL_PATTERNS = (
    ("AWS access key id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("AWS secret access key", re.compile(r"AWS_SECRET_ACCESS_KEY\s*=\s*['\"]?[A-Za-z0-9/+=]{30,}")),
    ("Postgres URI with inline password", re.compile(r"postgres(?:ql)?://[^:\s]+:[^@\s]+@")),
)


def env_example_lines() -> dict[str, str]:
    path = REPO_ROOT / ".env.example"
    assert path.exists(), ".env.example must exist — it documents required configuration"

    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


@pytest.mark.parametrize("key", SECRET_KEYS)
def test_env_example_has_no_real_secret(key: str) -> None:
    """.env.example is tracked, so anything in it is destined for the remote."""
    values = env_example_lines()
    assert key in values, f"{key} should be documented in .env.example"
    assert values[key] == "", (
        f"{key} has a value in .env.example. It is a tracked file — placeholders only."
    )


def test_env_example_documents_every_key_the_probe_requires() -> None:
    """A setting the code requires but the template omits is a setup trap."""
    values = env_example_lines()
    required = {
        "WAREHOUSE_HOST", "WAREHOUSE_PORT", "WAREHOUSE_DATABASE",
        "WAREHOUSE_USER", "WAREHOUSE_PASSWORD",
        "LAKE_BUCKET", "LAKE_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    }
    missing = required - values.keys()
    assert not missing, f".env.example is missing required settings: {sorted(missing)}"


def test_gitignore_excludes_env_but_keeps_the_template() -> None:
    rules = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in rules, ".gitignore must exclude .env"
    assert "!.env.example" in rules, ".env.example must stay tracked"
    assert "*.private.md" in rules, "local-only notes must never be committed"


def test_no_credential_shaped_strings_in_tracked_files() -> None:
    """Scan tracked text files for credential shapes.

    Deliberately scans the working tree rather than the diff: a secret
    introduced three commits ago is still a secret, and a test that only looks
    at the latest change would have stopped catching it.
    """
    skip_dirs = {".git", ".venv", "__pycache__", ".ruff_cache", ".pytest_cache"}
    findings: list[str] = []

    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or any(part in skip_dirs for part in path.parts):
            continue
        if path.name == ".env" or path.suffix not in {".md", ".py", ".sql", ".yml",
                                                      ".yaml", ".toml", ".txt",
                                                      ".example", ".json"}:
            continue
        if path.name == ".env.example":
            continue  # covered precisely by the tests above

        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        for label, pattern in CREDENTIAL_PATTERNS:
            if pattern.search(content):
                findings.append(f"{path.relative_to(REPO_ROOT)}: {label}")

    assert not findings, "credential-shaped strings found in tracked files:\n" + "\n".join(findings)
