"""Ingest the current F1 season and rebuild the models.

Orchestration lives here rather than in `tasks.py ingest`, which runs the same
three steps in a Python loop. Airflow gets to own the ordering instead: each
step becomes a task with its own retries, its own log, and a visible place in
the graph. A single opaque call would waste most of that.

**The season comes from the clock, not from `logical_date`.** This job refreshes
*current state*: the source has no time-window query, so asking for 2026 returns
every 2026 result, always. There is no "the 13th of September slice" to fetch,
so a run does not represent a period and keying the season off the run's date
would borrow a semantic the job does not have. `$(date -u +%Y)` says what is
meant — the season happening now.

Two safety rails follow from the same fact:

* `catchup=False` — with it on, unpausing after a fortnight would fire fourteen
  runs that each fetch *exactly the same thing*, spending the API budget
  fourteen times for one result.
* `max_active_runs=1` — an overrunning run must not be joined by the next. Two
  at once would compete for the same 500/hour budget and interleave writes to
  the same lake partition.

Daily rather than weekly because results are **adjudicated**: stewards' decisions
can change a classification days after a race, and a weekly run would miss
amendments. ~80 calls a day against a 500/hour budget is cheap insurance.

**No `--resume` on any ingest task, and that is load-bearing.** With the flag,
`extract_and_land` reads the checkpoint and skips any scope already marked
`complete` (`ingestion/pipeline.py`). Season-scoped entities — results,
qualifying, sprint — hold *one* checkpoint per season, and it flips to
`complete` the moment the paginator passes the reported row count. So a single
full sweep of 2026 marks the season done forever, and a row that landed wrong
could never be corrected: the pipeline only ever pages forward.

That is not hypothetical. Round 15 of 2026 (Azerbaijan, raced 26 September) was
published with `grid` as JSON `null` for all 22 drivers, because the source
attached results before the grid. `not_null_stg_results_grid_position` caught it
and held the race out of the marts, which is the system working. Recovering it
meant re-fetching the season from offset 0 — possible *only* because this flag
is absent. Adding `--resume` here to save a handful of calls would silently make
every future partial-publish permanent.

`--resume` exists for the backfill, where one scope is thousands of calls and
losing progress is expensive. The daily season sweep is about ten. Re-paging is
far cheaper than the class of bug it prevents.
"""

from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import dag
from callbacks import report_failure

PROJECT = "/opt/project"
PY = "/home/airflow/project-venv/bin/python"
DBT = "/home/airflow/project-venv/bin/dbt"

@dag(
    dag_id="f1_pipeline",
    start_date=datetime(2026, 9, 1),
    schedule="@daily",
    catchup=False,
    max_active_runs=1,
    tags=["f1"],
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
        "on_failure_callback": report_failure,
    },
)
def f1_pipeline():

    ingest_reference = BashOperator(
        task_id="ingest_reference",
        bash_command=f"cd {PROJECT} && {PY} -m ingestion.pipeline --all-reference",
    )

    # results, qualifying, sprint. One paginated sweep per season, not per round
    # — so **this** is the task to clear when a race's results need re-fetching.
    # No --resume: see the module docstring.
    ingest_season_entities = BashOperator(
        task_id="ingest_season_entities",
        bash_command=(
            f"cd {PROJECT} && {PY} -m ingestion.pipeline "
            f"--all-season --season $(date -u +%Y)"
        ),
    )

    # pitstops, driverstandings, constructorstandings. One call per round, so
    # clearing this task re-fetches standings but *not* results.
    ingest_race_entities = BashOperator(
        task_id="ingest_race_entities",
        bash_command=(
            f"cd {PROJECT} && {PY} -m ingestion.pipeline "
            f"--all-race --season $(date -u +%Y)"
        ),
    )

    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=f"cd {PROJECT}/dbt && {DBT} deps && {DBT} build",
        env={"DBT_PROFILES_DIR": f"{PROJECT}/dbt"},
        append_env=True,
    )

    ingest_reference >> ingest_season_entities >> ingest_race_entities >> dbt_build


f1_pipeline()