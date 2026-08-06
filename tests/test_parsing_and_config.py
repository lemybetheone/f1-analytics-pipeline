"""Unit tests for record parsing, lake keys, and configuration loading.

ARCHITECTURE §12: parsing and configuration are worth testing because a wrong
answer is silent. A dead-lettered record you never see and a `.env` value
parsed halfway both look like success.
"""

from __future__ import annotations

import pytest

from ingestion.config import ConfigError, load, read_env_file
from ingestion.load_lake import Lake
from ingestion.load_warehouse import parse_results
from tests.test_extract import make_settings


def race(season="2024", rnd="1", results=None):
    return {"season": season, "round": rnd, "Results": results or []}


def result(driver_id="max_verstappen", **extra):
    return {"Driver": {"driverId": driver_id}, "position": "1", **extra}


def payload(races):
    return {"MRData": {"RaceTable": {"Races": races}}}


# --- parsing ---------------------------------------------------------------

def test_parses_grain_keys_from_two_levels():
    """season and round live on the race; driverId on the nested result."""
    records, failures = parse_results(payload([
        race("2024", "1", [result("verstappen"), result("norris")]),
        race("2024", "2", [result("verstappen")]),
    ]))

    assert not failures
    assert [(r.season, r.round, r.driver_id) for r in records] == [
        ("2024", "1", "verstappen"),
        ("2024", "1", "norris"),
        ("2024", "2", "verstappen"),
    ]


def test_missing_key_field_is_dead_lettered_not_raised():
    """One bad record must never kill a run, and must never vanish silently."""
    records, failures = parse_results(payload([
        race("2024", "1", [result("verstappen"), {"position": "2"}]),
    ]))

    assert len(records) == 1, "the good record still loads"
    assert len(failures) == 1
    fragment, reason = failures[0]
    assert "driverId" in reason
    assert fragment == {"position": "2"}, "the failing record is preserved for retry"


def test_missing_race_context_dead_letters_every_child():
    records, failures = parse_results(payload([
        {"round": "1", "Results": [result("a"), result("b")]},  # no season
    ]))

    assert not records
    assert len(failures) == 2
    assert all("season" in reason for _, reason in failures)


def test_payload_is_preserved_untouched():
    """Raw stores what the source returned; casting belongs to staging."""
    original = result("verstappen", points="26", grid="1", status="Finished")
    records, _ = parse_results(payload([race(results=[original])]))

    assert records[0].payload == original
    assert records[0].payload["points"] == "26", "still a string, uncast"


def test_empty_and_malformed_payloads_do_not_raise():
    for candidate in ({}, {"MRData": {}}, {"MRData": {"RaceTable": {}}}, payload([])):
        records, failures = parse_results(candidate)
        assert records == [] and failures == []


# --- lake keys -------------------------------------------------------------

def test_lake_key_is_deterministic_and_sorts_correctly():
    lake = Lake(make_settings(), client=object())

    first = lake.key_for("results", "season=2024", 0, ingestion_date="2026-08-06")
    again = lake.key_for("results", "season=2024", 0, ingestion_date="2026-08-06")
    tenth = lake.key_for("results", "season=2024", 1000, ingestion_date="2026-08-06")

    assert first == again, "same page must overwrite, not accumulate duplicates"
    assert first == "raw/results/2026-08-06/results_season=2024_offset=00000.json"
    assert first < tenth, "zero padding keeps lexical order equal to fetch order"


def test_lake_key_partitions_by_ingestion_date():
    lake = Lake(make_settings(), client=object())
    monday = lake.key_for("results", "season=2024", 0, ingestion_date="2026-08-03")
    tuesday = lake.key_for("results", "season=2024", 0, ingestion_date="2026-08-04")

    assert monday != tuesday, "re-ingesting later must not overwrite yesterday's evidence"


# --- configuration ---------------------------------------------------------

def test_env_parser_keeps_hash_inside_quotes(tmp_path):
    """A '#' in a quoted password must survive; unquoted it starts a comment."""
    env = tmp_path / ".env"
    env.write_text(
        "QUOTED='pa#ss word'\n"
        "PLAIN=simple  # trailing comment\n"
        "\n"
        "# a comment line\n",
        encoding="utf-8",
    )
    values = read_env_file(env)

    assert values["QUOTED"] == "pa#ss word"
    assert values["PLAIN"] == "simple"


def test_load_reports_every_missing_setting_at_once(tmp_path, monkeypatch):
    for key in ("WAREHOUSE_HOST", "LAKE_BUCKET", "AWS_ACCESS_KEY_ID"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("WAREHOUSE_HOST=h\n", encoding="utf-8")

    with pytest.raises(ConfigError) as exc:
        load(env)

    message = str(exc.value)
    assert "LAKE_BUCKET" in message and "AWS_ACCESS_KEY_ID" in message, (
        "fixing configuration one error per run is a miserable loop"
    )


def test_rate_limits_must_be_positive(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "WAREHOUSE_HOST=h\nWAREHOUSE_PORT=5432\nWAREHOUSE_DATABASE=d\n"
        "WAREHOUSE_USER=u\nWAREHOUSE_PASSWORD=p\nLAKE_BUCKET=b\nLAKE_REGION=r\n"
        "AWS_ACCESS_KEY_ID=k\nAWS_SECRET_ACCESS_KEY=s\n"
        "JOLPICA_REQUESTS_PER_HOUR=0\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="positive"):
        load(env)


def test_dsn_description_never_contains_the_password():
    settings = make_settings(warehouse_password="hunter2")
    assert "hunter2" not in settings.dsn_description
