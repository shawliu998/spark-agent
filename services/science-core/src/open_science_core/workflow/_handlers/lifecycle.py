from __future__ import annotations

from typing import Protocol, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from ...models import JobRecord, PlanRecord, TaskRecord, WorkflowRecord, utc_now
from ..schemas import TaskEventData
from ..service import (
    MAX_JOB_ATTEMPTS,
    WorkflowConflict,
    append_workflow_events,
    assert_plan_approval_integrity,
    assert_task_matches_approved_plan,
    cancel_pending_interactions,
    enqueue_job,
    job_input_compatibility,
    retry_delay_seconds,
    transition_task,
    transition_workflow,
)
from ..state import WorkflowBlockedError, WorkflowFailure


class _PlanHandler(Protocol):
    def __call__(
        self,
        session: Session,
        workflow: WorkflowRecord,
        job: JobRecord,
        *,
        legacy_handler: bool,
    ) -> None: ...


class _RouteHandler(Protocol):
    def __call__(
        self,
        session: Session,
        workflow: WorkflowRecord,
        job: JobRecord,
    ) -> None: ...


class _TaskHandler(Protocol):
    def __call__(
        self,
        session: Session,
        workflow: WorkflowRecord,
        task: TaskRecord,
        job: JobRecord,
        *,
        legacy_handler: bool,
    ) -> None: ...


class _ReviewHandler(Protocol):
    def __call__(
        self,
        session: Session,
        workflow: WorkflowRecord,
        job: JobRecord,
        *,
        legacy_handler: bool,
    ) -> None: ...


class _AgentLoopHandler(Protocol):
    def __call__(
        self,
        session: Session,
        workflow: WorkflowRecord,
        job: JobRecord,
        /,
    ) -> object: ...


def mark_leased_job_started(session: Session, job_id: str, lease_token: str) -> None:
    job = session.get(JobRecord, job_id)
    if job is None or job.status != "leased" or job.lease_token != lease_token:
        raise WorkflowFailure(
            "job-lease-lost",
            "The background job lease is no longer valid.",
            retryable=True,
        )
    workflow = session.get(WorkflowRecord, job.workflow_id)
    if workflow is None:
        raise WorkflowFailure("workflow-missing", "The workflow record is missing.")
    if workflow.cancel_requested_at is not None:
        acknowledge_cancellation(session, job, workflow)
        session.commit()
        return
    if job.task_id is None:
        return
    task = session.get(TaskRecord, job.task_id)
    if task is None:
        raise WorkflowFailure("task-missing", "The workflow step record is missing.")
    if task.status == "queued":
        transition_task(session, task, "running")
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "step.started",
                    TaskEventData(
                        task_id=task.id,
                        step_key=task.step_key or "",
                        order_index=task.order_index or 0,
                        status="running",
                    ),
                    task.id,
                    job.id,
                )
            ],
        )
        session.commit()


def execute_leased_job(
    session: Session,
    job_id: str,
    lease_token: str,
    *,
    handle_route_intent: _RouteHandler,
    handle_generate_plan: _PlanHandler,
    handle_task: _TaskHandler,
    handle_review: _ReviewHandler,
    handle_observe_step: _AgentLoopHandler,
    handle_decide_next_action: _AgentLoopHandler,
    handle_apply_agent_decision: _AgentLoopHandler,
) -> None:
    job = session.get(JobRecord, job_id)
    if job is None or job.status != "leased" or job.lease_token != lease_token:
        raise WorkflowFailure(
            "job-lease-lost",
            "The background job lease is no longer valid.",
            retryable=True,
        )
    workflow = session.get(WorkflowRecord, job.workflow_id)
    if workflow is None:
        raise WorkflowFailure("workflow-missing", "The workflow record is missing.")
    task = session.get(TaskRecord, job.task_id) if job.task_id is not None else None
    if workflow.cancel_requested_at is not None:
        acknowledge_cancellation(session, job, workflow, task)
        session.commit()
        return
    compatibility = job_input_compatibility(session, workflow, job, task)
    if compatibility is None:
        raise WorkflowFailure(
            "job-input-changed",
            "The workflow input changed after this job was queued.",
            outcome_unknown=False,
        )

    if job.kind == "route-intent":
        handle_route_intent(session, workflow, job)
    elif job.kind == "generate-plan":
        handle_generate_plan(
            session,
            workflow,
            job,
            legacy_handler=compatibility == "legacy",
        )
    elif job.kind == "execute-task" and task is not None:
        handle_task(
            session,
            workflow,
            task,
            job,
            legacy_handler=compatibility == "legacy",
        )
    elif job.kind == "review-workflow":
        handle_review(
            session,
            workflow,
            job,
            legacy_handler=compatibility == "legacy",
        )
    elif job.kind == "observe-step":
        handle_observe_step(session, workflow, job)
    elif job.kind == "decide-next-action":
        handle_decide_next_action(session, workflow, job)
    elif job.kind == "apply-agent-decision":
        handle_apply_agent_decision(session, workflow, job)
    else:
        raise WorkflowFailure("unsupported-job-kind", "The workflow job type is unsupported.")
    # A cancellation may commit while a deterministic handler is doing a long
    # read-only calculation. Guard the final transaction with both the aggregate
    # revision and cancellation flag; on failure the whole handler transaction
    # rolls back and the worker's settlement path acknowledges cancellation.
    cancellation_guard = session.execute(
        update(WorkflowRecord)
        .where(
            WorkflowRecord.id == workflow.id,
            WorkflowRecord.row_version == workflow.row_version,
            WorkflowRecord.cancel_requested_at.is_(None),
        )
        .values(updated_at=WorkflowRecord.updated_at)
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[object], cancellation_guard).rowcount != 1:
        raise WorkflowFailure(
            "workflow-cancelled-during-job",
            "The workflow was cancelled before this job could publish its result.",
        )
    session.commit()


def advance_after_task(
    session: Session,
    workflow: WorkflowRecord,
    completed_task: TaskRecord,
    *,
    preserve_legacy_review: bool,
) -> None:
    tasks = list(
        session.scalars(
            select(TaskRecord)
            .where(TaskRecord.plan_id == completed_task.plan_id)
            .order_by(TaskRecord.order_index)
        )
    )
    completed_index = next(
        (index for index, task in enumerate(tasks) if task.id == completed_task.id),
        None,
    )
    if (
        completed_index is None
        or any(task.status != "completed" for task in tasks[: completed_index + 1])
        or any(task.status != "pending" for task in tasks[completed_index + 1 :])
    ):
        raise WorkflowFailure(
            "workflow-step-order-invalid",
            "The sequential workflow steps are not in their required order.",
        )
    next_task = next((task for task in tasks if task.status == "pending"), None)
    if next_task is not None:
        transition_task(session, next_task, "queued")
        next_job = enqueue_job(
            session,
            workflow,
            kind="execute-task",
            task=next_task,
            operation_key=f"workflow:{workflow.id}:task:{next_task.id}",
        )
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "step.queued",
                    TaskEventData(
                        task_id=next_task.id,
                        step_key=next_task.step_key or "",
                        order_index=next_task.order_index or 0,
                        status="queued",
                    ),
                    next_task.id,
                    next_job.id,
                )
            ],
        )
        return
    if not tasks or any(task.status != "completed" for task in tasks):
        raise WorkflowFailure(
            "workflow-step-gap",
            "The sequential workflow cannot identify its next step.",
        )
    transition_workflow(session, workflow, "reviewing")
    enqueue_job(
        session,
        workflow,
        kind="review-workflow",
        operation_key=f"workflow:{workflow.id}:review:{completed_task.plan_id}",
        handler_version=(
            "deterministic-claims-v1" if preserve_legacy_review else None
        ),
    )


def settle_leased_job_error(
    session: Session,
    job_id: str,
    lease_token: str,
    error: WorkflowBlockedError | WorkflowFailure | Exception,
) -> None:
    job = session.get(JobRecord, job_id)
    if job is None or job.status != "leased" or job.lease_token != lease_token:
        session.rollback()
        return
    workflow = session.get(WorkflowRecord, job.workflow_id)
    task = session.get(TaskRecord, job.task_id) if job.task_id is not None else None
    if workflow is None:
        finish_job(session, job, "failed", "workflow-missing", str(error))
        session.commit()
        return
    if workflow.cancel_requested_at is not None:
        acknowledge_cancellation(session, job, workflow, task)
        session.commit()
        return
    if isinstance(error, WorkflowBlockedError):
        finish_job(session, job, "failed", error.code, error.user_message)
        if task is not None and task.status in {"queued", "running"}:
            transition_task(session, task, "blocked")
        transition_workflow(
            session,
            workflow,
            "blocked",
            reason_code=error.code,
            blocking_message=error.user_message,
            retryable=error.code == "no-ready-pdf",
        )
        append_failed_task_event(session, workflow, task, job, error.code)
        session.commit()
        return
    failure = error if isinstance(error, WorkflowFailure) else WorkflowFailure(
        "workflow-handler-error",
        "The local workflow handler failed unexpectedly. Retry after checking the project inputs.",
        retryable=False,
    )
    if (
        failure.code == "lease-expired"
        and job_input_compatibility(session, workflow, job, task) is None
    ):
        failure = WorkflowFailure(
            "job-input-changed",
            "The expired workflow job no longer matches its verified inputs.",
        )
    finish_job(session, job, "failed", failure.code, failure.user_message)
    retryable = (
        failure.retryable
        and not failure.outcome_unknown
        and job.attempt < MAX_JOB_ATTEMPTS
    )
    if retryable:
        if task is not None and task.status == "running":
            # The handler transaction rolled back; in case start was committed,
            # return the deterministic step to queued for the next attempt.
            task.status = "failed"
            task.finished_at = utc_now()
            task.retries += 1
            session.flush()
            transition_task(session, task, "queued")
        enqueue_job(
            session,
            workflow,
            kind=job.kind,
            task=task,
            operation_key=job.operation_key,
            attempt=job.attempt + 1,
            previous_job_id=job.id,
            delay_seconds=retry_delay_seconds(job.attempt + 1),
            handler_version=job.handler_version,
        )
        session.commit()
        return
    if task is not None and task.status in {"queued", "running"}:
        transition_task(session, task, "failed")
    workflow.last_error_code = failure.code
    workflow.last_error_message = failure.user_message[:2_000]
    if failure.outcome_unknown:
        transition_workflow(
            session,
            workflow,
            "blocked",
            reason_code="execution-outcome-unknown",
            blocking_message="Execution outcome is unknown; reconcile the existing run before retrying.",
        )
    else:
        transition_workflow(session, workflow, "failed", reason_code=failure.code)
    append_failed_task_event(session, workflow, task, job, failure.code)
    session.commit()


def acknowledge_cancellation(
    session: Session,
    job: JobRecord,
    workflow: WorkflowRecord,
    task: TaskRecord | None = None,
) -> None:
    finish_job(session, job, "cancelled")
    now = utc_now()
    session.execute(
        update(JobRecord)
        .where(
            JobRecord.workflow_id == workflow.id,
            JobRecord.status == "queued",
        )
        .values(status="cancelled", finished_at=now, updated_at=now)
    )
    session.execute(
        update(TaskRecord)
        .where(
            TaskRecord.workflow_id == workflow.id,
            TaskRecord.status.in_(
                [
                    "pending",
                    "queued",
                    "running",
                    "waiting-approval",
                    "blocked",
                    "failed",
                ]
            ),
        )
        .values(status="cancelled", finished_at=now, updated_at=now)
    )
    cancel_pending_interactions(session, workflow.id)
    if workflow.status != "cancelled":
        transition_workflow(session, workflow, "cancelled")


def finish_job(
    session: Session,
    job: JobRecord,
    status: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    now = utc_now()
    result = session.execute(
        update(JobRecord)
        .where(
            JobRecord.id == job.id,
            JobRecord.status == "leased",
            JobRecord.lease_token == job.lease_token,
        )
        .values(
            status=status,
            error_code=error_code,
            error_message=error_message[:2_000] if error_message else None,
            finished_at=now,
            updated_at=now,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[object], result).rowcount != 1:
        raise WorkflowFailure(
            "job-lease-lost",
            "The job lease expired before its result could be saved.",
            retryable=True,
        )
    session.flush()


def append_failed_task_event(
    session: Session,
    workflow: WorkflowRecord,
    task: TaskRecord | None,
    job: JobRecord,
    error_code: str,
) -> None:
    if task is None:
        return
    append_workflow_events(
        session,
        workflow,
        [
            (
                "step.failed",
                TaskEventData(
                    task_id=task.id,
                    step_key=task.step_key or "",
                    order_index=task.order_index or 0,
                    status="failed" if task.status == "failed" else "blocked",
                    error_code=error_code,
                ),
                task.id,
                job.id,
            )
        ],
    )


def previous_task(session: Session, task: TaskRecord) -> TaskRecord:
    previous = session.scalar(
        select(TaskRecord).where(
            TaskRecord.plan_id == task.plan_id,
            TaskRecord.order_index == (task.order_index or 0) - 1,
        )
    )
    if previous is None or previous.status != "completed":
        raise WorkflowFailure(
            "previous-step-incomplete",
            "The preceding sequential workflow step is not complete.",
        )
    return previous


def workflow_failure_from_conflict(error: WorkflowConflict) -> WorkflowFailure:
    return WorkflowFailure(
        error.code,
        error.user_message,
        retryable=error.retryable,
    )


def assert_current_task_contract(
    session: Session,
    workflow: WorkflowRecord,
    task: TaskRecord,
) -> PlanRecord:
    plan = session.get(PlanRecord, task.plan_id) if task.plan_id is not None else None
    if plan is None:
        raise WorkflowFailure(
            "approved-plan-missing",
            "The approved workflow plan for this step is missing.",
        )
    try:
        assert_task_matches_approved_plan(workflow, plan, task)
        assert_plan_approval_integrity(session, workflow, plan)
    except WorkflowConflict as error:
        raise workflow_failure_from_conflict(error) from None
    return plan
