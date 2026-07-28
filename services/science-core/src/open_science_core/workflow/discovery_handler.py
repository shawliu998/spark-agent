"""Durable materialization and execution of bounded paper discovery tasks."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    CandidateOccurrenceRecord,
    DiscoverySpecRecord,
    JobRecord,
    PlanRecord,
    TaskRecord,
    ToolInvocationRecord,
    WorkflowRecord,
    utc_now,
)
from ._handlers.lifecycle import append_failed_task_event, finish_job
from ._service.events import append_workflow_events, transition_task, transition_workflow
from ._service.integrity import (
    WorkflowConflict,
    assert_plan_approval_integrity,
    content_sha256,
)
from .agent_loop.coordinator import enqueue_agent_observation
from .discovery_adapter import (
    DiscoveryOperationObservation,
    McpToolBroker,
    PaperSearchAdapter,
    discovery_plan_spec,
    discovery_terminal_task_outputs,
    validate_recoverable_discovery_invocation,
    validate_terminal_discovery_invocation,
)
from .discovery_schemas import (
    DiscoveryPolicyStopReason,
    DiscoveryProvider,
    DiscoverySpec,
)
from .schemas import TaskEventData
from .state import WorkflowFailure

McpBrokerFactory = Callable[[], McpToolBroker]


def recover_expired_discovery_job(
    session: Session,
    job_id: str,
    lease_token: str,
) -> bool:
    """Reconcile one expired discovery lease from its durable invocation.

    ``pending`` is the sent-authorized boundary and is therefore fail-closed.
    A ``prepared`` row, in contrast, proves that dispatch never became
    authorized; marking it ``prepared-not-sent`` lets the normal, bounded retry
    policy create exactly one fresh attempt.  This function never constructs a
    broker and cannot perform a remote call during recovery.
    """

    job = session.get(JobRecord, job_id)
    if job is None or job.status != "leased" or job.lease_token != lease_token:
        session.rollback()
        return True
    task = session.get(TaskRecord, job.task_id) if job.task_id is not None else None
    workflow = session.get(WorkflowRecord, job.workflow_id)
    if workflow is None or task is None or task.task_type != "paper-discovery":
        return False
    invocation = session.scalar(
        select(ToolInvocationRecord)
        .where(
            ToolInvocationRecord.job_id == job.id,
            ToolInvocationRecord.attempt == job.attempt,
        )
        .order_by(ToolInvocationRecord.created_at, ToolInvocationRecord.id)
    )
    if invocation is None:
        # The process stopped before persist-before-send completed.  There is no
        # invocation identity to reconcile, so the existing generic lease retry
        # remains the only safe path.
        return False

    if invocation.status == "prepared":
        try:
            validate_recoverable_discovery_invocation(
                session,
                workflow=workflow,
                task=task,
                job=job,
                invocation=invocation,
                lease_token=lease_token,
            )
        except Exception:
            return False
        invocation.status = "failed"
        invocation.error_code = (
            "prepared-not-sent"
            if job.attempt == 1
            else "discovery-retry-exhausted"
        )
        invocation.error_message = "The paper-search request was not authorized for dispatch."
        invocation.returned_count = 0
        invocation.novel_candidate_count = 0
        invocation.duplicate_count = 0
        invocation.candidate_set_sha256 = None
        invocation.finished_at = utc_now()
        session.commit()
        # Only attempt 1 can receive the one fresh send. A second interrupted
        # preparation is terminal and is observed by the Agent, never replayed.
        if job.attempt == 1:
            return False
        _settle_known_discovery_failure(
            session,
            workflow,
            task,
            job,
            "discovery-retry-exhausted",
        )
        return True

    if invocation.status == "pending":
        try:
            validate_recoverable_discovery_invocation(
                session,
                workflow=workflow,
                task=task,
                job=job,
                invocation=invocation,
                lease_token=lease_token,
            )
        except Exception:
            return False
        invocation.status = "outcome-unknown"
        invocation.error_code = "outcome-unknown"
        invocation.error_message = "The paper-search connector outcome is unknown."
        invocation.returned_count = 0
        invocation.novel_candidate_count = 0
        invocation.duplicate_count = 0
        invocation.candidate_set_sha256 = None
        invocation.finished_at = utc_now()
        _settle_discovery_outcome_unknown(session, workflow, task, job)
        session.commit()
        return True

    if invocation.status == "succeeded":
        discovery_spec = session.get(DiscoverySpecRecord, invocation.discovery_spec_id)
        if discovery_spec is None:
            raise WorkflowFailure(
                "discovery-spec-missing",
                "The approved paper-discovery specification is missing.",
            )
        validate_terminal_discovery_invocation(
            session,
            workflow=workflow,
            discovery_spec=discovery_spec,
            invocation=invocation,
            allow_unsettled=True,
        )
        transition_task(session, task, "completed")
        finish_job(session, job, "succeeded")
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "step.completed",
                    TaskEventData(
                        task_id=task.id,
                        step_key=task.step_key or "",
                        order_index=task.order_index or 0,
                        status="completed",
                        output_count=int(invocation.returned_count or 0),
                    ),
                    task.id,
                    job.id,
                )
            ],
        )
        stop_reason = _discovery_stop_reason(session, workflow, discovery_spec)
        if stop_reason is not None:
            _request_source_import(session, workflow, stop_reason)
        session.flush()
        session.refresh(job)
        enqueue_agent_observation(session, workflow, job)
        session.commit()
        return True

    if invocation.status == "outcome-unknown":
        discovery_spec = session.get(DiscoverySpecRecord, invocation.discovery_spec_id)
        if discovery_spec is None:
            return False
        validate_terminal_discovery_invocation(
            session,
            workflow=workflow,
            discovery_spec=discovery_spec,
            invocation=invocation,
            allow_unsettled=True,
        )
        _settle_discovery_outcome_unknown(session, workflow, task, job)
        session.commit()
        return True

    if invocation.status == "cancelled":
        discovery_spec = session.get(DiscoverySpecRecord, invocation.discovery_spec_id)
        if discovery_spec is None:
            return False
        validate_terminal_discovery_invocation(
            session,
            workflow=workflow,
            discovery_spec=discovery_spec,
            invocation=invocation,
            allow_unsettled=True,
        )
        finish_job(session, job, "cancelled")
        if task.status in {"queued", "running", "blocked", "failed"}:
            transition_task(session, task, "cancelled")
        session.commit()
        return True

    discovery_spec = session.get(DiscoverySpecRecord, invocation.discovery_spec_id)
    if discovery_spec is None:
        return False
    validate_terminal_discovery_invocation(
        session,
        workflow=workflow,
        discovery_spec=discovery_spec,
        invocation=invocation,
        allow_unsettled=True,
    )
    _settle_known_discovery_failure(
        session,
        workflow,
        task,
        job,
        invocation.error_code or "discovery-failed",
    )
    return True


def _settle_known_discovery_failure(
    session: Session,
    workflow: WorkflowRecord,
    task: TaskRecord,
    job: JobRecord,
    error_code: str,
) -> None:
    finish_job(session, job, "failed", error_code, None)
    if task.status in {"queued", "running"}:
        transition_task(session, task, "failed")
    append_failed_task_event(session, workflow, task, job, error_code)
    session.flush()
    session.refresh(job)
    enqueue_agent_observation(session, workflow, job)
    session.commit()


def _settle_discovery_outcome_unknown(
    session: Session,
    workflow: WorkflowRecord,
    task: TaskRecord,
    job: JobRecord,
) -> None:
    finish_job(session, job, "failed", "discovery-outcome-unknown", None)
    if task.status in {"queued", "running"}:
        transition_task(session, task, "blocked")
    transition_workflow(
        session,
        workflow,
        "blocked",
        reason_code="discovery-outcome-unknown",
        blocking_message=(
            "A paper-search request may have been sent; review the durable result before retrying."
        ),
    )
    workflow.last_error_code = "discovery-outcome-unknown"
    workflow.last_error_message = (
        "A paper-search request may have been sent; review the durable result before retrying."
    )
    append_failed_task_event(session, workflow, task, job, "discovery-outcome-unknown")
    session.flush()
    session.refresh(job)
    enqueue_agent_observation(session, workflow, job)


def materialize_discovery_plan(
    session: Session,
    workflow: WorkflowRecord,
    discovery_spec: DiscoverySpecRecord,
) -> PlanRecord:
    """Return an already approved discovery plan with intact consent provenance."""

    if (
        workflow.creation_mode != "autonomous"
        or workflow.workflow_type != "literature-synthesis"
        or discovery_spec.workflow_id != workflow.id
        or discovery_spec.status != "approved"
    ):
        raise WorkflowConflict(
            "discovery-plan-not-applicable",
            "An approved discovery plan requires an autonomous literature workflow.",
        )
    spec = DiscoverySpec.model_validate(discovery_spec.spec_json)
    if discovery_spec.spec_sha256 != content_sha256(
        spec.model_dump(mode="json", by_alias=True)
    ):
        raise WorkflowConflict(
            "discovery-spec-integrity-invalid",
            "The approved discovery specification does not match its immutable payload.",
        )
    expected_spec = discovery_plan_spec(discovery_spec, spec)
    approved = session.scalar(
        select(PlanRecord).where(
            PlanRecord.workflow_id == workflow.id,
            PlanRecord.status == "approved",
        )
    )
    if (
        approved is None
        or approved.generator != "paper-discovery-v1"
        or approved.spec_json != expected_spec
        or approved.spec_sha256 != content_sha256(expected_spec)
    ):
        raise WorkflowConflict(
            "discovery-plan-conflict",
            "The workflow has no matching approved public discovery plan.",
        )
    assert_plan_approval_integrity(session, workflow, approved)
    return approved


def execute_leased_discovery_job(
    session: Session,
    job_id: str,
    lease_token: str,
    *,
    broker: McpToolBroker,
) -> DiscoveryOperationObservation:
    """Run one durable operation without letting generic task handling bypass it."""

    job = session.get(JobRecord, job_id)
    if job is None or job.status != "leased" or job.lease_token != lease_token:
        raise WorkflowFailure(
            "job-lease-lost",
            "The discovery job lease is no longer valid.",
            retryable=True,
        )
    workflow = session.get(WorkflowRecord, job.workflow_id)
    task = session.get(TaskRecord, job.task_id) if job.task_id is not None else None
    if workflow is None or task is None or task.task_type != "paper-discovery":
        raise WorkflowFailure(
            "discovery-task-missing",
            "The durable paper-discovery task is missing.",
        )
    spec_id = task.inputs.get("discoverySpecId")
    query_id = task.inputs.get("queryId")
    provider = task.inputs.get("provider")
    if not all(isinstance(value, str) for value in (spec_id, query_id, provider)):
        raise WorkflowFailure(
            "discovery-task-input-invalid",
            "The approved paper-discovery task is malformed.",
        )
    discovery_spec = session.get(DiscoverySpecRecord, cast(str, spec_id))
    if discovery_spec is None:
        raise WorkflowFailure(
            "discovery-spec-missing",
            "The approved paper-discovery specification is missing.",
        )
    observation = PaperSearchAdapter().execute(
        session,
        workflow=workflow,
        discovery_spec=discovery_spec,
        job=job,
        query_id=cast(str, query_id),
        provider=cast(DiscoveryProvider, provider),
        attempt=job.attempt,
        lease_token=lease_token,
        broker=broker,
    )
    session.expire_all()
    job = session.get(JobRecord, job_id)
    workflow = session.get(WorkflowRecord, task.workflow_id or "")
    task = session.get(TaskRecord, task.id)
    if job is None or workflow is None or task is None:
        raise WorkflowFailure("discovery-state-missing", "The discovery state could not be reloaded.")
    if job.status != "leased" or job.lease_token != lease_token:
        raise WorkflowFailure("job-lease-lost", "The discovery job lease is no longer valid.")
    if observation.status in {"succeeded", "existing"}:
        terminal_outputs = discovery_terminal_task_outputs(
            session,
            invocation_id=observation.invocation_id,
        )
        if task.outputs != terminal_outputs:
            raise WorkflowFailure(
                "discovery-terminal-anchor-invalid",
                "The durable paper-discovery result anchor no longer matches its invocation.",
            )
        transition_task(session, task, "completed")
        finish_job(session, job, "succeeded")
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "step.completed",
                    TaskEventData(
                        task_id=task.id,
                        step_key=task.step_key or "",
                        order_index=task.order_index or 0,
                        status="completed",
                        output_count=observation.returned_count,
                    ),
                    task.id,
                    job.id,
                )
            ],
        )
        stop_reason = _discovery_stop_reason(session, workflow, discovery_spec)
        if stop_reason is not None:
            _request_source_import(session, workflow, stop_reason)
    else:
        # A terminal invocation is observed by the Agent loop.  It is never
        # auto-retried by the generic worker: only the adapter's durable result
        # can grant retry authority to the policy layer.
        finish_job(session, job, "failed", observation.error_code, None)
        transition_task(session, task, "blocked" if observation.status == "outcome-unknown" else "failed")
        append_failed_task_event(
            session,
            workflow,
            task,
            job,
            observation.error_code or "discovery-failed",
        )
    enqueue_agent_observation(session, workflow, job)
    session.commit()
    return observation


def _discovery_stop_reason(
    session: Session,
    workflow: WorkflowRecord,
    discovery_spec: DiscoverySpecRecord,
) -> DiscoveryPolicyStopReason | None:
    spec = DiscoverySpec.model_validate(discovery_spec.spec_json)
    operation_count = int(
        session.scalar(
            select(func.count(func.distinct(JobRecord.operation_key)))
            .join(
                TaskRecord,
                TaskRecord.id == JobRecord.task_id,
            )
            .where(
                JobRecord.workflow_id == workflow.id,
                TaskRecord.task_type == "paper-discovery",
                JobRecord.status == "succeeded",
            )
        )
        or 0
    )
    candidates = int(
        session.scalar(
            select(func.count(func.distinct(CandidateOccurrenceRecord.candidate_id)))
            .join(ToolInvocationRecord, ToolInvocationRecord.id == CandidateOccurrenceRecord.invocation_id)
            .where(
                CandidateOccurrenceRecord.project_id == workflow.project_id,
                ToolInvocationRecord.discovery_spec_id == discovery_spec.id,
            )
        )
        or 0
    )
    successes = list(
        session.scalars(
            select(ToolInvocationRecord)
            .where(
                ToolInvocationRecord.workflow_id == workflow.id,
                ToolInvocationRecord.discovery_spec_id == discovery_spec.id,
                ToolInvocationRecord.status == "succeeded",
            )
            .order_by(
                ToolInvocationRecord.finished_at,
                ToolInvocationRecord.id,
            )
        )
    )
    no_novelty = 0
    for item in reversed(successes):
        if int(item.novel_candidate_count or 0) > 0:
            break
        no_novelty += 1
    if candidates >= spec.stop_policy.min_unique_candidates:
        return "discovery-candidate-target-reached"
    if no_novelty >= spec.stop_policy.max_consecutive_no_novelty:
        return "discovery-no-novelty-limit"
    if operation_count >= spec.stop_policy.max_attempts:
        return "discovery-attempt-budget-reached"
    return None


def _request_source_import(
    session: Session,
    workflow: WorkflowRecord,
    stop_reason: DiscoveryPolicyStopReason,
) -> None:
    if workflow.status != "running":
        return
    # The current durable workflow contract reserves waiting-clarification for
    # unresolved intake (workflow_type is NULL).  A typed literature workflow
    # must therefore pause in its existing blocked state until the user imports
    # a real PDF Source; mutating workflow_type would sever plan provenance.
    transition_workflow(
        session,
        workflow,
        "blocked",
        reason_code=stop_reason,
        blocking_message="Import or select PDF sources before evidence extraction can continue.",
    )


__all__ = (
    "McpBrokerFactory",
    "execute_leased_discovery_job",
    "materialize_discovery_plan",
    "recover_expired_discovery_job",
)
