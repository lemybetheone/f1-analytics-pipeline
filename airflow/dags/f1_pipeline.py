"""Ingest the current F1 season and rebuild the models.

Orchestration lives here rather than in `tasks.py ingest`, which runs the same
three steps in a Python loop. Airflow gets to own the ordering instead: each
step becomes a task with its own retries, its own log, and a visible place in
the graph. A single opaque call would waste most of that.
"""

from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import dag

PROJECT = "/opt/project"
PY = "/home/airflow/project-venv/bin/python"
DBT = "/home/airflow/project-venv/bin/dbt"

# Becomes dynamic when we add a schedule in the next step.
SEASON = "2026"


@dag(
    dag_id="f1_pipeline",
    start_date=datetime(2026, 9, 1),
    schedule=None,
    catchup=False,
    tags=["f1"],
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=5),
    },
)
def f1_pipeline():

    ingest_reference = BashOperator(
        task_id="ingest_reference",
        bash_command=f"cd {PROJECT} && {PY} -m ingestion.pipeline --all-reference",
    )

    ingest_season_entities = BashOperator(
        task_id="ingest_season_entities",
        bash_command=(
            f"cd {PROJECT} && {PY} -m ingestion.pipeline "
            f"--all-season --season {SEASON}"
        ),
    )

    ingest_race_entities = BashOperator(
        task_id="ingest_race_entities",
        bash_command=(
            f"cd {PROJECT} && {PY} -m ingestion.pipeline "
            f"--all-race --season {SEASON}"
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