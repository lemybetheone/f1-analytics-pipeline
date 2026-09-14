"""Failure reporting for the pipeline DAGs.

`on_failure_callback` fires **once, after the final attempt fails** — not on
each retry. That is deliberate: a task in `up_for_retry` has not failed yet, and
alerting on every attempt is how an alert channel becomes noise people ignore.
Verified rather than assumed: a task with `retries=2` produces this record in
attempt 3's log and in neither of the first two.

The transport is a log record because this Airflow runs on one machine and the
UI is the alerting channel. The value here is the *structure*: swapping in
email, Slack or PagerDuty replaces the body of `report_failure` and nothing
else. Deciding what a failure message must contain is the part that does not
change with the transport.

**The callback context is not the template context.** Airflow's `Context` type
lists `exception`, `reason`, `try_number`, `logical_date` and the `ds`/`ts`
macros, and none of them are present when a task callback runs in Airflow
3.0.2 — reading them yields `None`, which produces an alert that says a failure
happened without saying anything about it. Measured by dumping the live context
from inside a failing task; what is actually passed is `dag`, `dag_run`,
`run_id`, `task`, `task_instance`/`ti`, `params`, `var`, `conn`, `macros` and
the asset accessors.

So everything below comes off the task instance, and **the exception is not
available here at all**. It does not need to be: Airflow logs the traceback to
the same file immediately above this record, which is why the log path is the
most useful thing an alert can carry.
"""

import logging

log = logging.getLogger(__name__)


def report_failure(context) -> None:
    """Emit one structured, greppable record for a finally-failed task."""
    ti = context["task_instance"]
    run_id = context["run_id"]

    log.error(
        "PIPELINE FAILURE\n"
        "  dag     : %s\n"
        "  task    : %s\n"
        "  run     : %s\n"
        "  attempt : %s of %s (no retries left)\n"
        "  state   : %s\n"
        "  host    : %s\n"
        "  log     : logs/dag_id=%s/run_id=%s/task_id=%s/attempt=%s.log\n"
        "            (the traceback is immediately above this record)",
        ti.dag_id,
        ti.task_id,
        run_id,
        ti.try_number,
        ti.max_tries + 1,
        # `.value`, because the SDK's state enum
        # (airflow.sdk.api.datamodels._generated.TaskInstanceState) is a plain
        # Enum with no __str__ override, so %s renders it as
        # 'TaskInstanceState.FAILED'. The similarly named enum in
        # airflow.utils.state does override it — easy to test the wrong one.
        getattr(ti.state, "value", ti.state),
        ti.hostname,
        ti.dag_id,
        run_id,
        ti.task_id,
        ti.try_number,
    )
