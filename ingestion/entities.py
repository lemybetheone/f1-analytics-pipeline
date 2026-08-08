"""What to fetch, where the records live in the payload, and what the grain is.

One place to declare an endpoint rather than one module per endpoint. The
Phase 0 probe already measured every value here — record paths, grain keys and
which endpoints reject season-wide calls — so this file is a transcription of
evidence, not a set of guesses. See SCHEMA §2 and `discovery/findings/`.

Adding an endpoint should be a row here plus a table in a migration. If it ever
needs a bespoke module, that is a signal the source is less uniform than Phase 0
found, and worth re-measuring rather than special-casing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# How a request is scoped. Measured, not assumed:
#   global — one call set covers all of history (`/drivers.json`)
#   season — needs a season (`/2024/results.json`)
#   race   — needs season *and* round. `pitstops` returns HTTP 400 without them,
#            and `driverstandings` silently returns only the final round.
#            Not implemented yet; see the note at the bottom of this module.
SCOPE_GLOBAL = "global"
SCOPE_SEASON = "season"
SCOPE_RACE = "race"


@dataclass(frozen=True)
class EntitySpec:
    """Everything needed to fetch, parse and store one endpoint."""

    name: str
    table: str
    path_template: str
    scope: str

    # Path to the list holding records — or holding their *parents* when
    # `child_key` is set. Results nest one level down: a race object carries
    # the Results array, and season/round live on the race, not the result.
    container: tuple[str, ...]
    child_key: str | None = None
    parent_fields: tuple[str, ...] = ()

    # (column, dotted path). Resolved against the record first, then the parent
    # context. Every one of these was measured at 100% presence in Phase 0, so
    # a missing value is genuinely exceptional and gets dead-lettered.
    keys: tuple[tuple[str, str], ...] = ()

    def path_for(self, season: str | None = None, round_: str | None = None) -> str:
        if self.scope == SCOPE_RACE:
            if not season or not round_:
                raise ValueError(f"{self.name} requires a season and a round")
            return self.path_template.format(season=season, round=round_)
        if self.scope == SCOPE_SEASON:
            if not season:
                raise ValueError(f"{self.name} requires a season")
            return self.path_template.format(season=season)
        return self.path_template

    def scope_label(self, season: str | None = None, round_: str | None = None) -> str:
        """Identifies the unit of work in checkpoints and lake keys.

        Race-scoped entities get one label — and therefore one checkpoint —
        per round. That is deliberate: each round is an independent unit of
        work, so an interrupted season resumes at the round it stopped on
        rather than restarting the season. Rounds are zero-padded so lake keys
        sort in race order; unpadded, round 10 sorts before round 2.
        """
        if self.scope == SCOPE_RACE:
            return f"season={season}_round={int(round_):02d}"
        if self.scope == SCOPE_SEASON:
            return f"season={season}"
        return "all"

    @property
    def key_columns(self) -> tuple[str, ...]:
        return tuple(column for column, _ in self.keys)


ENTITIES: dict[str, EntitySpec] = {
    # --- reference data: small, complete, fetched for all of history --------
    "seasons": EntitySpec(
        name="seasons", table="seasons", path_template="seasons", scope=SCOPE_GLOBAL,
        container=("MRData", "SeasonTable", "Seasons"),
        keys=(("season", "season"),),
    ),
    "circuits": EntitySpec(
        name="circuits", table="circuits", path_template="circuits", scope=SCOPE_GLOBAL,
        container=("MRData", "CircuitTable", "Circuits"),
        keys=(("circuit_id", "circuitId"),),
    ),
    "drivers": EntitySpec(
        name="drivers", table="drivers", path_template="drivers", scope=SCOPE_GLOBAL,
        container=("MRData", "DriverTable", "Drivers"),
        keys=(("driver_id", "driverId"),),
    ),
    "constructors": EntitySpec(
        name="constructors", table="constructors", path_template="constructors",
        scope=SCOPE_GLOBAL,
        container=("MRData", "ConstructorTable", "Constructors"),
        keys=(("constructor_id", "constructorId"),),
    ),
    "status": EntitySpec(
        name="status", table="status", path_template="status", scope=SCOPE_GLOBAL,
        container=("MRData", "StatusTable", "Status"),
        keys=(("status_id", "statusId"),),
    ),
    "races": EntitySpec(
        name="races", table="races", path_template="races", scope=SCOPE_GLOBAL,
        container=("MRData", "RaceTable", "Races"),
        keys=(("season", "season"), ("round", "round")),
    ),

    # --- session facts: nested under a race, fetched per season -------------
    "results": EntitySpec(
        name="results", table="results", path_template="{season}/results",
        scope=SCOPE_SEASON,
        container=("MRData", "RaceTable", "Races"),
        child_key="Results", parent_fields=("season", "round"),
        keys=(("season", "season"), ("round", "round"), ("driver_id", "Driver.driverId")),
    ),
    "qualifying": EntitySpec(
        name="qualifying", table="qualifying", path_template="{season}/qualifying",
        scope=SCOPE_SEASON,
        container=("MRData", "RaceTable", "Races"),
        child_key="QualifyingResults", parent_fields=("season", "round"),
        keys=(("season", "season"), ("round", "round"), ("driver_id", "Driver.driverId")),
    ),
    "sprint": EntitySpec(
        name="sprint", table="sprint", path_template="{season}/sprint",
        scope=SCOPE_SEASON,
        container=("MRData", "RaceTable", "Races"),
        child_key="SprintResults", parent_fields=("season", "round"),
        keys=(("season", "season"), ("round", "round"), ("driver_id", "Driver.driverId")),
    ),
    # --- race-scoped: one request per round --------------------------------
    # These three cannot be fetched season-wide. Pit stops return HTTP 400
    # without a round. Season-scoped standings return only the *final* round
    # while reporting a total that looks like every round — the more dangerous
    # failure, because it succeeds and silently gives you one row set instead
    # of twenty-four.
    #
    # Rounds come from `raw.races`, which the reference load already populated,
    # so iterating them costs no extra API calls.
    "pitstops": EntitySpec(
        name="pitstops", table="pitstops", path_template="{season}/{round}/pitstops",
        scope=SCOPE_RACE,
        container=("MRData", "RaceTable", "Races"),
        child_key="PitStops", parent_fields=("season", "round"),
        # `stop` is part of the grain: a driver pits more than once per race, so
        # (season, round, driver) would collide. Note driverId is flat here, not
        # nested under a Driver object as it is on results.
        keys=(("season", "season"), ("round", "round"),
              ("driver_id", "driverId"), ("stop", "stop")),
    ),
    "driverstandings": EntitySpec(
        name="driverstandings", table="driver_standings",
        path_template="{season}/{round}/driverstandings", scope=SCOPE_RACE,
        container=("MRData", "StandingsTable", "StandingsLists"),
        child_key="DriverStandings", parent_fields=("season", "round"),
        keys=(("season", "season"), ("round", "round"), ("driver_id", "Driver.driverId")),
    ),
    "constructorstandings": EntitySpec(
        name="constructorstandings", table="constructor_standings",
        path_template="{season}/{round}/constructorstandings", scope=SCOPE_RACE,
        container=("MRData", "StandingsTable", "StandingsLists"),
        child_key="ConstructorStandings", parent_fields=("season", "round"),
        keys=(("season", "season"), ("round", "round"),
              ("constructor_id", "Constructor.constructorId")),
    ),
}


@dataclass(frozen=True)
class Record:
    """One row destined for a raw table: its grain keys and untouched payload."""

    keys: dict[str, str]
    payload: dict


def _resolve(path: str, record: dict, context: dict) -> Any:
    """Look a dotted path up in the record, falling back to the parent context.

    `Driver.driverId` lives on the record; `season` lives on the race that
    contains it. Trying the record first means a record-level field always wins
    over an inherited one of the same name.
    """
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            current = None
            break
        current = current[part]

    if current in (None, ""):
        return context.get(path)
    return current


def _walk(payload: dict, container: tuple[str, ...]) -> list:
    current: Any = payload
    for part in container:
        if not isinstance(current, dict):
            return []
        current = current.get(part)
    return current if isinstance(current, list) else []


def parse_records(spec: EntitySpec, payload: dict) -> tuple[list[Record], list[tuple[dict, str]]]:
    """Extract records for one entity from one page.

    Returns (records, failures). A record missing a grain key is returned as a
    failure rather than raised: ARCHITECTURE §5 requires that one bad record
    never kills a run and never vanishes silently.
    """
    records: list[Record] = []
    failures: list[tuple[dict, str]] = []

    for parent in _walk(payload, spec.container):
        if not isinstance(parent, dict):
            continue

        if spec.child_key:
            context = {field: parent.get(field) for field in spec.parent_fields}
            children = parent.get(spec.child_key) or []
        else:
            context = {}
            children = [parent]

        for child in children:
            if not isinstance(child, dict):
                continue

            keys: dict[str, str] = {}
            missing: list[str] = []
            for column, path in spec.keys:
                value = _resolve(path, child, context)
                if value in (None, ""):
                    missing.append(path)
                else:
                    keys[column] = str(value)

            if missing:
                failures.append((child, f"missing key field(s): {', '.join(missing)}"))
                continue

            records.append(Record(keys=keys, payload=child))

    return records, failures
