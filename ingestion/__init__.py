"""Ingestion: extract from the source API, land in the lake, load the warehouse.

Module boundaries (ARCHITECTURE §10):

* `config`          — the single source of environment-driven configuration
* `extract`         — HTTP, retry, pacing, pagination. The only module that
                      talks to the source API.
* `load_lake`       — writes raw payloads to object storage, unmodified
* `load_warehouse`  — reads those objects *back from the lake* and upserts
* `pipeline`        — orchestrates the above; implements none of it

The warehouse loads from the lake rather than from the API response still held
in memory (ARCHITECTURE decision 2). That is what makes the load step
independently replayable, and it is a rule the code has to actually follow —
an in-memory shortcut here would make the architecture diagram a lie.
"""
