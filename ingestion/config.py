"""Single source of environment-driven configuration.

SECURITY §1: both ingestion and transformation read the same environment
variables, and config files reference env vars rather than literals. Nothing
here holds a default that is also a secret.

This module deliberately does **not** import from `discovery/`. Decision 9
isolates the two directions: discovery code is throwaway evidence-gathering and
must never become a dependency of the pipeline. The small `.env` parser below
is duplicated from the probe for exactly that reason — the alternative is a
coupling that outlives the code it was convenient for.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = REPO_ROOT / ".env"


class ConfigError(RuntimeError):
    """Configuration is missing or unusable. Raised before any work starts."""


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a `.env` file into a dict.

    Dependency-free on purpose: configuration loading should not be able to
    fail because another package installed badly. Handles comments, blanks and
    quoted values — a password containing `#` must survive, which it will not
    if the comment stripper runs before the quote check.
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()

        if value[:1] in {"'", '"'} and value[-1:] == value[:1] and len(value) > 1:
            value = value[1:-1]  # quoted: verbatim, including any '#'
        else:
            value = value.split(" #", 1)[0].strip()

        values[key] = value
    return values


@dataclass(frozen=True)
class Settings:
    """Everything the pipeline needs to run, validated at load time."""

    # --- source ---
    source_base_url: str
    requests_per_hour: int
    burst_per_second: int

    # --- warehouse ---
    warehouse_host: str
    warehouse_port: str
    warehouse_database: str
    warehouse_user: str
    warehouse_password: str
    schema_raw: str
    schema_staging: str
    schema_marts: str

    # --- lake ---
    lake_bucket: str
    lake_region: str
    lake_prefix: str
    aws_access_key_id: str
    aws_secret_access_key: str

    target_env: str

    @property
    def dsn_description(self) -> str:
        """Safe-to-log connection description. Never includes the password."""
        return (f"{self.warehouse_user}@{self.warehouse_host}:"
                f"{self.warehouse_port}/{self.warehouse_database}")

    @property
    def sustained_delay_seconds(self) -> float:
        """Seconds between calls to stay inside the hourly budget."""
        return 3600 / self.requests_per_hour

    @property
    def burst_delay_seconds(self) -> float:
        """Minimum seconds between consecutive calls."""
        return 1 / self.burst_per_second


# Settings with no safe default: every one is either a credential or an
# environment-specific identifier, and guessing any of them silently points the
# pipeline at the wrong place.
REQUIRED = (
    "WAREHOUSE_HOST", "WAREHOUSE_PORT", "WAREHOUSE_DATABASE",
    "WAREHOUSE_USER", "WAREHOUSE_PASSWORD",
    "LAKE_BUCKET", "LAKE_REGION",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
)

DEFAULTS = {
    "JOLPICA_BASE_URL": "https://api.jolpi.ca/ergast/f1",
    "JOLPICA_REQUESTS_PER_HOUR": "500",
    "JOLPICA_BURST_PER_SECOND": "4",
    "WAREHOUSE_SCHEMA_RAW": "raw",
    "WAREHOUSE_SCHEMA_STAGING": "staging",
    "WAREHOUSE_SCHEMA_MARTS": "marts",
    "LAKE_PREFIX": "raw",
    "TARGET_ENV": "dev",
}


def load(env_file: Path | None = None) -> Settings:
    """Build Settings from the environment, or fail with everything that's wrong.

    Real environment variables win over the file so CI can inject configuration
    without a `.env` existing on disk. Missing keys are reported **together**:
    fixing configuration one error per run is a miserable loop.
    """
    path = env_file or DEFAULT_ENV_FILE
    values = DEFAULTS | read_env_file(path) | {k: v for k, v in os.environ.items() if v}

    missing = [key for key in REQUIRED if not values.get(key)]
    if missing:
        raise ConfigError(
            f"Missing required configuration: {', '.join(missing)}.\n"
            f"Checked {path}. Copy .env.example to .env and fill it in."
        )

    def as_int(key: str) -> int:
        try:
            return int(values[key])
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{key} must be an integer, got {values.get(key)!r}") from exc

    requests_per_hour = as_int("JOLPICA_REQUESTS_PER_HOUR")
    burst_per_second = as_int("JOLPICA_BURST_PER_SECOND")
    if requests_per_hour < 1 or burst_per_second < 1:
        raise ConfigError("Rate limits must be positive; a zero budget cannot make progress.")

    return Settings(
        source_base_url=values["JOLPICA_BASE_URL"].rstrip("/"),
        requests_per_hour=requests_per_hour,
        burst_per_second=burst_per_second,
        warehouse_host=values["WAREHOUSE_HOST"],
        warehouse_port=values["WAREHOUSE_PORT"],
        warehouse_database=values["WAREHOUSE_DATABASE"],
        warehouse_user=values["WAREHOUSE_USER"],
        warehouse_password=values["WAREHOUSE_PASSWORD"],
        schema_raw=values["WAREHOUSE_SCHEMA_RAW"],
        schema_staging=values["WAREHOUSE_SCHEMA_STAGING"],
        schema_marts=values["WAREHOUSE_SCHEMA_MARTS"],
        lake_bucket=values["LAKE_BUCKET"],
        lake_region=values["LAKE_REGION"],
        lake_prefix=values["LAKE_PREFIX"].strip("/"),
        aws_access_key_id=values["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=values["AWS_SECRET_ACCESS_KEY"],
        target_env=values["TARGET_ENV"],
    )
