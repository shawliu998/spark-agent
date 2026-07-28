from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...models import (
    AgentDecisionRecord,
    AnalysisSpecRecord,
    InteractionRequestRecord,
    JobRecord,
    ModelInvocationRecord,
    PlanRecord,
    ReviewRecord,
    StepObservationRecord,
    TaskRecord,
    WorkflowRecord,
)
from .._service.integrity import MAX_JOB_ATTEMPTS
from .._service.jobs import enqueue_job
from .policy import AgentLoopCounts

_TRANSIENT_CONTROL_FAILURE_CODES = frozenset(
    {
        "job-lease-lost",
        "lease-expired",
        "worker-interrupted",
    }
)


def persisted_loop_counts(session: Session, workflow_id: str) -> AgentLoopCounts:
    observations = (
        session.scalar(
            select(func.count(StepObservationRecord.id)).where(
                StepObservationRecord.workflow_id == workflow_id
            )
        )
        or 0
    )
    agent_plan_revisions = (
        session.scalar(
            select(func.count(AgentDecisionRecord.id)).where(
                AgentDecisionRecord.workflow_id == workflow_id,
                AgentDecisionRecord.action == "revise-analysis-spec",
                AgentDecisionRecord.status == "applied",
            )
        )
        or 0
    )
    latest_spec = (
        session.scalar(
            select(func.max(AnalysisSpecRecord.revision)).where(
                AnalysisSpecRecord.workflow_id == workflow_id
            )
        )
        or 1
    )
    retries = (
        session.scalar(
            select(func.count(AgentDecisionRecord.id)).where(
                AgentDecisionRecord.workflow_id == workflow_id,
                AgentDecisionRecord.action == "retry-step",
                AgentDecisionRecord.status == "applied",
            )
        )
        or 0
    )
    clarifications = (
        session.scalar(
            select(func.count(InteractionRequestRecord.id)).where(
                InteractionRequestRecord.workflow_id == workflow_id,
                InteractionRequestRecord.agent_decision_id.is_not(None),
            )
        )
        or 0
    )
    model_decisions = (
        session.scalar(
            select(func.count(ModelInvocationRecord.id)).where(
                ModelInvocationRecord.workflow_id == workflow_id,
                ModelInvocationRecord.operation_type == "agent-next-action",
            )
        )
        or 0
    )
    invalid_decisions = (
        session.scalar(
            select(func.count(ModelInvocationRecord.id)).where(
                ModelInvocationRecord.workflow_id == workflow_id,
                ModelInvocationRecord.operation_type == "agent-next-action",
                func.json_array_length(ModelInvocationRecord.validation_errors) > 0,
            )
        )
        or 0
    )
    return AgentLoopCounts(
        agent_steps=int(observations),
        # User-driven clarification can legitimately create later plan versions.
        # Only an applied Agent revision consumes the autonomous loop budget.
        plan_revisions=int(agent_plan_revisions),
        analysis_spec_revisions=max(0, int(latest_spec) - 1),
        step_retries=int(retries),
        clarification_rounds=int(clarifications),
        model_decisions=int(model_decisions),
        invalid_model_decisions=int(invalid_decisions),
    )


def recover_agent_loop_jobs(session: Session, workflow: WorkflowRecord) -> int:
    """Recreate only missing deterministic control jobs after a process crash."""

    supported = workflow.workflow_type == "dataset-analysis" or (
        workflow.workflow_type == "literature-synthesis"
        and workflow.generation_mode == "local-deterministic"
    )
    if workflow.creation_mode != "autonomous" or not supported:
        return 0

    workflow_id = workflow.id
    current_plan_id = session.scalar(
        select(PlanRecord.id).where(
            PlanRecord.workflow_id == workflow_id,
            PlanRecord.status == "approved",
        )
    )
    if current_plan_id is None:
        return 0
    recovered = 0
    for source_job, task in _terminal_sources_missing_observation(
        session,
        workflow,
        current_plan_id,
    ):
        recovered += int(
            _enqueue_missing_control_job(
                session,
                workflow,
                kind="observe-step",
                operation_key=f"workflow:{workflow_id}:observe:{source_job.id}",
                task=task,
            )
        )
    observations = list(
        session.scalars(
            select(StepObservationRecord)
            .where(
                StepObservationRecord.workflow_id == workflow_id,
                StepObservationRecord.plan_id == current_plan_id,
            )
            .order_by(StepObservationRecord.created_at, StepObservationRecord.id)
        )
    )
    for observation in observations:
        decision = session.scalar(
            select(AgentDecisionRecord)
            .where(AgentDecisionRecord.observation_id == observation.id)
            .order_by(AgentDecisionRecord.decision_revision.desc())
        )
        if decision is None:
            recovered += int(
                _enqueue_missing_control_job(
                    session,
                    workflow,
                    kind="decide-next-action",
                    operation_key=f"workflow:{workflow_id}:decide:{observation.id}",
                )
            )
            continue
        if (
            decision.status == "proposed"
            and not decision.requires_user_confirmation
            and decision.expected_workflow_revision == workflow.row_version
        ):
            recovered += int(
                _enqueue_missing_control_job(
                    session,
                    workflow,
                    kind="apply-agent-decision",
                    operation_key=(
                        f"workflow:{workflow_id}:apply-decision:{decision.id}"
                    ),
                )
            )
    return recovered


def _terminal_sources_missing_observation(
    session: Session,
    workflow: WorkflowRecord,
    current_plan_id: str,
) -> list[tuple[JobRecord, TaskRecord | None]]:
    candidates = list(
        session.scalars(
            select(JobRecord)
            .where(
                JobRecord.workflow_id == workflow.id,
                JobRecord.kind.in_(["execute-task", "review-workflow"]),
            )
            .order_by(JobRecord.created_at, JobRecord.attempt, JobRecord.id)
        )
    )
    latest_by_subject: dict[tuple[str, str], JobRecord] = {}
    for candidate in candidates:
        subject = (
            ("task", candidate.task_id)
            if candidate.kind == "execute-task" and candidate.task_id is not None
            else ("review", candidate.operation_key)
        )
        latest_by_subject[subject] = candidate
    sources = [
        source
        for source in latest_by_subject.values()
        if source.status in {"succeeded", "failed"}
    ]
    missing: list[tuple[JobRecord, TaskRecord | None]] = []
    for source in sources:
        existing = session.scalar(
            select(StepObservationRecord.id).where(
                StepObservationRecord.source_job_id == source.id
            )
        )
        if existing is not None:
            continue
        task = session.get(TaskRecord, source.task_id) if source.task_id is not None else None
        if source.kind == "execute-task":
            if task is None:
                continue
            if task.workflow_id != workflow.id or task.plan_id != current_plan_id:
                continue
            expected_task_statuses = (
                {"completed"}
                if source.status == "succeeded"
                else {"failed", "blocked"}
            )
            if task.status not in expected_task_statuses:
                continue
        else:
            expected_review_type = (
                "deterministic-analysis-v1"
                if workflow.workflow_type == "dataset-analysis"
                else "deterministic-claims-v2"
            )
            review = session.scalar(
                select(ReviewRecord).where(
                    ReviewRecord.workflow_id == workflow.id,
                    ReviewRecord.plan_id == current_plan_id,
                    ReviewRecord.review_type == expected_review_type,
                    ReviewRecord.input_sha256 == source.input_sha256,
                    ReviewRecord.verdict == "passed",
                )
            )
            if source.status != "succeeded" or review is None:
                continue
        missing.append((source, task))
    return missing


def _enqueue_missing_control_job(
    session: Session,
    workflow: WorkflowRecord,
    *,
    kind: str,
    operation_key: str,
    task: TaskRecord | None = None,
) -> bool:
    attempts = list(
        session.scalars(
            select(JobRecord)
            .where(
                JobRecord.workflow_id == workflow.id,
                JobRecord.operation_key == operation_key,
            )
            .order_by(JobRecord.attempt.desc())
        )
    )
    if any(job.status in {"queued", "leased"} for job in attempts):
        return False
    latest = attempts[0] if attempts else None
    if latest is not None:
        if latest.status != "failed":
            return False
        if (
            latest.attempt >= MAX_JOB_ATTEMPTS
            or latest.error_code not in _TRANSIENT_CONTROL_FAILURE_CODES
        ):
            return False
    enqueue_job(
        session,
        workflow,
        kind=kind,
        task=task,
        operation_key=operation_key,
        attempt=(latest.attempt + 1 if latest is not None else 1),
        previous_job_id=latest.id if latest is not None else None,
    )
    return True


__all__ = ("persisted_loop_counts", "recover_agent_loop_jobs")
