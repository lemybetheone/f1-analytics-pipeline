# Conventional entry point. Every target delegates to tasks.py, which holds the
# actual command definitions — `make` is not installed on Windows by default,
# and the author develops there, so a Makefile carrying the logic would be a
# task runner half the project could not run.
#
# Delegating rather than duplicating means `make ingest` and
# `python tasks.py ingest` cannot drift apart.
#
# Windows: use `python tasks.py <target>` directly, or install make
# (`scoop install make`).

PYTHON ?= python
SEASON ?= 2024

.PHONY: help setup check lint test migrate verify ingest backfill transform docs

help:
	@echo "setup      create .venv, install pinned dependencies, install git hooks"
	@echo "check      lint + unit tests (exactly what CI runs)"
	@echo "migrate    apply pending database migrations"
	@echo "verify     prove warehouse and lake connectivity"
	@echo "ingest     full ingest for one season   (make ingest SEASON=2023)"
	@echo "backfill   the historical seasons, newest first (~7.6h, resumable)"
	@echo "transform  build and test the dbt models"
	@echo "docs       generate the lineage graph and column docs"

setup:
	$(PYTHON) tasks.py setup

check:
	$(PYTHON) tasks.py check

lint:
	$(PYTHON) tasks.py lint

test:
	$(PYTHON) tasks.py test

migrate:
	$(PYTHON) tasks.py migrate

verify:
	$(PYTHON) tasks.py verify

ingest:
	$(PYTHON) tasks.py ingest --season $(SEASON)

backfill:
	$(PYTHON) tasks.py backfill

transform:
	$(PYTHON) tasks.py transform

docs:
	$(PYTHON) tasks.py docs
