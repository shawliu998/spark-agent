from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace
from typing import Any, Literal, cast

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from ...analysis_spec.results import StructuredAnalysisResult
from ...analysis_spec.schemas import (
    AnalysisSpec,
    ClarificationProposal,
    analysis_spec_sha256,
)
from ...model_gateway import OpenAICompatibleModelGateway
from ...models import (
    AgentDecisionRecord,
    AnalysisIntentRecord,
    AnalysisSpecRecord,
    ApprovalRecord,
    ArtifactRecord,
    CandidateOccurrenceRecord,
    DiscoverySpecRecord,
    EventRecord,
    IntentDecisionRecord,
    InteractionRequestRecord,
    JobRecord,
    ModelInvocationRecord,
    PlanRecord,
    ReviewRecord,
    RunRecord,
    StepObservationRecord,
    StructuredAnalysisResultRecord,
    TaskRecord,
    ToolInvocationRecord,
    WorkflowRecord,
    utc_now,
)
from .._handlers.lifecycle import finish_job
from .._handlers.planning import assert_remote_gateway_matches_creation
from .._service.events import append_workflow_events, transition_task, transition_workflow
from .._service.integrity import (
    WorkflowConflict,
    assert_plan_approval_integrity,
    content_sha256,
)
from .._service.jobs import enqueue_job
from .._service.snapshots import (
    assert_result_sources_current,
    result_source_descriptors,
    workflow_snapshot,
)
from ..discovery_adapter import (
    PAPER_SEARCH_CONNECTOR_NAME,
    DiscoveryAdapterError,
    discovery_operation_key,
    discovery_operations,
    discovery_plan_spec,
    discovery_step_key,
    discovery_task_input,
    validate_terminal_discovery_invocation,
)
from ..discovery_schemas import DiscoveryProvider, DiscoverySpec, discovery_sha256
from ..research_memory import (
    create_observation_memory_candidates,
    decision_context_payload,
    get_or_create_context_snapshot,
)
from ..schemas import (
    AUTONOMOUS_REMOTE_DATA_CATEGORIES,
    AgentAnalysisSpecRevisionEventData,
    AgentDecisionEventData,
    AgentObservationCreatedEventData,
    AgentStepRetryRequestedEventData,
    AgentStoppedEventData,
    AnalysisSpecEventData,
    DatasetAnalysisReviewResult,
    DeterministicReviewResult,
    DiscoverySelectionOperationSignal,
    DiscoverySelectionProjection,
)
from ..scientific_interactions import create_scientific_interaction
from ..state import WorkflowFailure
from .decision import (
    AgentDecisionResult,
    next_action_input_sha256,
    recover_unknown_next_action,
    safe_analysis_spec_revision,
    select_next_action,
)
from .observer import (
    ObservationContext,
    VerifiedFailureSummary,
    build_analysis_result_observation,
    build_discovery_observation,
    build_failure_observation,
)
from .policy import (
    AgentLoopContext,
    completion_invariant_satisfied,
    determine_allowed_actions,
    deterministic_action,
)
from .prompts import AGENT_NEXT_ACTION_PROMPT_VERSION
from .recovery import persisted_loop_counts
from .schemas import (
    AgentDecision,
    ObservationFact,
    StepObservation,
    agent_decision_sha256,
    step_observation_sha256,
)

_TRANSIENT_FAILURE_CODES = frozenset(
    {
        "artifact-collection-timeout",
        "lease-expired",
        "runtime-temporarily-unavailable",
        "runtime-timeout",
        "worker-interrupted",
        "connector-unavailable",
        "rate-limited",
    }
)
_METHOD_FAILURE_CODES = frozenset(
    {
        "analysis-method-invalid",
        "analysis-preflight-failed",
        "pearson-assumption-failed",
        "welch-group-variance-zero",
    }
)


class AgentLoopCoordinator:
    def __init__(self, gateway: OpenAICompatibleModelGateway) -> None:
        self._gateway = gateway

    def enqueue_observation(
        self,
        session: Session,
        workflow: WorkflowRecord,
        source_job: JobRecord,
    ) -> JobRecord:
        return enqueue_agent_observation(session, workflow, source_job)

    def observe(
        self,
        session: Session,
        workflow: WorkflowRecord,
        control_job: JobRecord,
    ) -> StepObservationRecord:
        source_job_id = _operation_subject(workflow.id, control_job.operation_key, "observe")
        source_job = session.get(JobRecord, source_job_id)
        if source_job is None or source_job.workflow_id != workflow.id:
            raise WorkflowFailure(
                "agent-observation-source-missing",
                "The durable observation source is missing.",
            )
        task = session.get(TaskRecord, source_job.task_id) if source_job.task_id else None
        observation_type = _observation_type(source_job, task)
        existing = session.scalar(
            select(StepObservationRecord).where(
                StepObservationRecord.source_job_id == source_job.id,
                StepObservationRecord.observation_type == observation_type,
            )
        )
        if existing is not None:
            _observation_from_record(existing)
            create_observation_memory_candidates(session, workflow, existing)
            finish_job(session, control_job, "succeeded")
            self._enqueue_decision(session, workflow, existing)
            return existing
        plan_id = task.plan_id if task is not None else _latest_plan_id(session, workflow.id)
        context = ObservationContext(
            workflow_id=workflow.id,
            plan_id=plan_id,
            task_id=task.id if task is not None else None,
            observation_type=cast(Any, observation_type),
            step_key=(task.step_key if task is not None and task.step_key else "review-workflow"),
            attempt=source_job.attempt,
            source_job_id=source_job.id,
        )
        observation = self._build_observation(session, workflow, source_job, task, context)
        record = _persist_observation(session, observation)
        create_observation_memory_candidates(session, workflow, record)
        finish_job(session, control_job, "succeeded")
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "agent.observation-created",
                    AgentObservationCreatedEventData(
                        observation_id=record.id,
                        task_id=record.task_id,
                        expected_workflow_revision=workflow.row_version,
                        reason_code="verified-step-observation",
                    ),
                    record.task_id,
                    control_job.id,
                )
            ],
        )
        self._enqueue_decision(session, workflow, record)
        return record

    def decide(
        self,
        session: Session,
        workflow: WorkflowRecord,
        control_job: JobRecord,
    ) -> AgentDecisionRecord:
        expected_lease_token = control_job.lease_token
        if expected_lease_token is None:
            raise WorkflowFailure(
                "job-lease-lost",
                "The background job lease is no longer valid.",
                retryable=True,
            )
        observation_id = _operation_subject(workflow.id, control_job.operation_key, "decide")
        observation_record = session.get(StepObservationRecord, observation_id)
        if observation_record is None or observation_record.workflow_id != workflow.id:
            raise WorkflowFailure("agent-observation-missing", "The observation is missing.")
        _assert_current_observation_binding(
            session,
            workflow,
            observation_record,
        )
        existing = session.scalar(
            select(AgentDecisionRecord)
            .where(AgentDecisionRecord.observation_id == observation_id)
            .order_by(AgentDecisionRecord.decision_revision.desc())
        )
        if existing is not None:
            finish_job(session, control_job, "succeeded")
            if existing.status == "proposed" and not existing.requires_user_confirmation:
                self._enqueue_apply(session, workflow, existing)
            return existing
        observation = _observation_from_record(observation_record)
        current_spec_record = _current_analysis_spec(session, workflow.id)
        current_spec = _parse_analysis_spec(current_spec_record)
        context = _loop_context(session, workflow, observation, current_spec)
        plan_summary = _plan_summary(session, workflow.id)
        answered_interactions: tuple[dict[str, object], ...] = ()
        context_snapshot = get_or_create_context_snapshot(
            session,
            workflow,
            plan_id=observation_record.plan_id,
            observation_id=observation_record.id,
        )
        research_context = decision_context_payload(context_snapshot)
        if workflow.generation_mode == "remote-model-assisted":
            assert_remote_gateway_matches_creation(session, workflow, self._gateway)
        selected_model = (
            self._gateway.default_model
            if workflow.generation_mode == "remote-model-assisted"
            else None
        )
        invocation = _decision_invocation_for_operation(
            session,
            workflow,
            control_job.operation_key,
        )
        if invocation is not None:
            result = _recover_decision_invocation(
                workflow=workflow,
                observation=observation,
                context=context,
                current_analysis_spec=current_spec,
                plan_summary=plan_summary,
                answered_interactions=answered_interactions,
                research_context=research_context,
                invocation=invocation,
            )
        else:
            remote_call_expected = bool(
                workflow.generation_mode == "remote-model-assisted"
                and deterministic_action(context, observation) is None
                and self._gateway.configured
                and selected_model is not None
            )
            if remote_call_expected:
                assert selected_model is not None
                input_sha256 = next_action_input_sha256(
                    goal=workflow.goal,
                    observation=observation,
                    context=context,
                    current_analysis_spec=current_spec,
                    plan_summary=plan_summary,
                    answered_interactions=answered_interactions,
                    research_context=research_context,
                    model=selected_model,
                )
                invocation = _begin_decision_invocation(
                    session,
                    workflow,
                    control_job,
                    model=selected_model,
                    endpoint_identity=self._gateway.endpoint_identity,
                    input_sha256=input_sha256,
                )
                session.commit()
                session.expire_all()
                reloaded_workflow = session.get(WorkflowRecord, workflow.id)
                reloaded_job = session.get(JobRecord, control_job.id)
                reloaded_observation = session.get(StepObservationRecord, observation_id)
                reloaded_invocation = session.get(ModelInvocationRecord, invocation.id)
                if (
                    reloaded_workflow is None
                    or reloaded_job is None
                    or reloaded_observation is None
                    or reloaded_invocation is None
                ):
                    raise WorkflowFailure(
                        "agent-next-action-invocation-persistence-failed",
                        "The durable next-action request could not be reloaded.",
                    )
                workflow = reloaded_workflow
                control_job = reloaded_job
                observation_record = reloaded_observation
                invocation = reloaded_invocation
                _assert_decision_job_lease(control_job, expected_lease_token)
                if workflow.cancel_requested_at is not None:
                    _fail_pending_decision_invocation(
                        session,
                        invocation,
                        code="model-request-cancelled-before-send",
                        message=(
                            "The workflow was cancelled before the next-action request began."
                        ),
                    )
                    session.commit()
                    raise WorkflowFailure(
                        "workflow-cancelled-during-job",
                        "The workflow was cancelled before the next-action request began.",
                    )
            result = asyncio.run(
                select_next_action(
                    goal=workflow.goal,
                    observation=observation,
                    context=context,
                    current_analysis_spec=current_spec,
                    plan_summary=plan_summary,
                    answered_interactions=answered_interactions,
                    research_context=research_context,
                    gateway=(
                        self._gateway
                        if workflow.generation_mode == "remote-model-assisted"
                        else None
                    ),
                    model=selected_model,
                )
            )
        if invocation is not None:
            _assert_decision_invocation_binding(
                session,
                workflow,
                control_job,
                invocation,
                result,
                self._gateway,
            )
        invocation = _finalize_decision_invocation(
            session,
            workflow,
            invocation,
            result,
        )
        record = _persist_decision(
            session,
            workflow,
            observation_record,
            result,
            invocation,
        )
        finish_job(session, control_job, "succeeded")
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "agent.decision-proposed",
                    _decision_event(
                        record,
                        observation_record,
                        research_context_snapshot_id=research_context["id"],
                        research_context_snapshot_sha256=research_context["sha256"],
                        discovery_selection=context.discovery_selection,
                    ),
                    observation_record.task_id,
                    control_job.id,
                )
            ],
        )
        if not record.requires_user_confirmation:
            self._enqueue_apply(session, workflow, record)
        return record

    def apply_decision(
        self,
        session: Session,
        workflow: WorkflowRecord,
        control_job: JobRecord,
    ) -> AgentDecisionRecord:
        decision_id = _operation_subject(workflow.id, control_job.operation_key, "apply-decision")
        decision = session.get(AgentDecisionRecord, decision_id)
        if decision is None or decision.workflow_id != workflow.id:
            raise WorkflowFailure("agent-decision-missing", "The agent decision is missing.")
        _decision_from_record(decision)
        if decision.status == "applied":
            finish_job(session, control_job, "succeeded")
            return decision
        if decision.status not in {"proposed", "waiting-user-confirmation"}:
            raise WorkflowFailure(
                "agent-decision-not-applicable",
                "The agent decision is no longer applicable.",
            )
        if workflow.row_version != decision.expected_workflow_revision:
            raise WorkflowFailure(
                "agent-decision-stale",
                "The workflow changed before the agent decision could be applied.",
            )
        if decision.requires_user_confirmation and decision.status != "proposed":
            # Approval changes waiting-user-confirmation back to proposed before
            # queueing this durable apply job.
            raise WorkflowFailure(
                "agent-decision-confirmation-required",
                "The decision still requires user confirmation.",
            )
        observation = session.get(StepObservationRecord, decision.observation_id)
        if observation is None:
            raise WorkflowFailure("agent-observation-missing", "The observation is missing.")
        _observation_from_record(observation)
        _assert_current_observation_binding(session, workflow, observation)
        persisted_discovery_selection = (
            _persisted_discovery_selection(session, workflow, decision)
            if decision.action == "continue"
            else None
        )
        revision_lineage: tuple[str, str] | None = None
        if decision.action == "continue":
            self._apply_continue(
                session,
                workflow,
                decision,
                _observation_from_record(observation),
                persisted_discovery_selection,
            )
        elif decision.action == "retry-step":
            self._apply_retry(session, workflow, decision, observation, control_job)
        elif decision.action == "request-clarification":
            self._apply_clarification(session, workflow, decision)
        elif decision.action == "complete":
            self._apply_complete(session, workflow, observation)
        elif decision.action == "stop":
            self._apply_stop(session, workflow, decision)
        elif decision.action == "revise-analysis-spec":
            revision_lineage = self._apply_spec_revision(
                session,
                workflow,
                decision,
                observation,
                control_job,
            )
        now = utc_now()
        decision.status = "applied"
        decision.applied_at = now
        finish_job(session, control_job, "succeeded")
        decision_event = _decision_event(
            decision,
            observation,
            previous_analysis_spec_id=(
                revision_lineage[0] if revision_lineage is not None else None
            ),
            proposed_analysis_spec_id=(
                revision_lineage[1] if revision_lineage is not None else None
            ),
            discovery_selection=persisted_discovery_selection,
        )
        entries: list[tuple[str, Any, str | None, str | None]] = [
            (
                "agent.decision-applied",
                decision_event,
                observation.task_id,
                control_job.id,
            )
        ]
        if revision_lineage is not None:
            entries.append(
                (
                    "agent.analysis-spec-revision-approved",
                    AgentAnalysisSpecRevisionEventData(
                        **decision_event.model_dump()
                    ),
                    observation.task_id,
                    control_job.id,
                )
            )
        if decision.action == "stop":
            entries.append(
                (
                    "agent.stopped",
                    AgentStoppedEventData(**_decision_event(decision, observation).model_dump()),
                    observation.task_id,
                    control_job.id,
                )
            )
        append_workflow_events(session, workflow, entries)
        return decision

    def _enqueue_decision(
        self,
        session: Session,
        workflow: WorkflowRecord,
        observation: StepObservationRecord,
    ) -> JobRecord:
        return enqueue_job(
            session,
            workflow,
            kind="decide-next-action",
            operation_key=f"workflow:{workflow.id}:decide:{observation.id}",
        )

    def _enqueue_apply(
        self,
        session: Session,
        workflow: WorkflowRecord,
        decision: AgentDecisionRecord,
    ) -> JobRecord:
        return enqueue_job(
            session,
            workflow,
            kind="apply-agent-decision",
            operation_key=f"workflow:{workflow.id}:apply-decision:{decision.id}",
        )

    def _build_observation(
        self,
        session: Session,
        workflow: WorkflowRecord,
        source_job: JobRecord,
        task: TaskRecord | None,
        context: ObservationContext,
    ) -> StepObservation:
        if task is not None and task.task_type == "paper-discovery":
            return _discovery_step_observation(session, workflow, source_job, task, context)
        if source_job.status == "failed" or (task is not None and task.status == "failed"):
            return build_failure_observation(
                context,
                _verified_failure(session, workflow, source_job, task),
            )
        if task is not None and task.step_key == "execute-analysis":
            run_id = task.outputs.get("runId")
            run = session.get(RunRecord, run_id) if isinstance(run_id, str) else None
            result = (
                session.scalar(
                    select(StructuredAnalysisResultRecord).where(
                        StructuredAnalysisResultRecord.run_id == run.id
                    )
                )
                if run is not None
                else None
            )
            intent = (
                session.get(AnalysisIntentRecord, run.analysis_intent_id)
                if run is not None and run.analysis_intent_id is not None
                else None
            )
            if (
                run is not None
                and result is not None
                and intent is not None
                and intent.analysis_spec_id is not None
            ):
                parsed = StructuredAnalysisResult.model_validate_json(
                    json.dumps(result.result_json, allow_nan=False, ensure_ascii=False),
                    strict=True,
                )
                artifacts = list(
                    session.scalars(select(ArtifactRecord).where(ArtifactRecord.run_id == run.id))
                )
                return build_analysis_result_observation(
                    context,
                    analysis_spec_id=intent.analysis_spec_id,
                    structured_result_id=result.id,
                    run_id=run.id,
                    result=parsed,
                    artifact_ids=[item.id for item in artifacts],
                )
        if source_job.kind == "review-workflow":
            return _review_observation(session, workflow, source_job, context)
        if task is None:
            raise WorkflowFailure(
                "agent-observation-task-missing",
                "The successful observation source has no task.",
            )
        artifacts = cast(object, task.outputs.get("artifactIds"))
        artifact_items = cast(list[object], artifacts) if isinstance(artifacts, list) else []
        artifact_ids = [item for item in artifact_items if isinstance(item, str)]
        return StepObservation(
            schema_version="1",
            workflow_id=workflow.id,
            plan_id=task.plan_id,
            task_id=task.id,
            source_job_id=source_job.id,
            observation_type="step-output",
            step_key=task.step_key or "workflow-step",
            attempt=source_job.attempt,
            status="succeeded",
            facts=[
                ObservationFact(
                    code="step-output-verified",
                    statement="The workflow-owned step reached its durable completed state.",
                    value={"outputKeys": sorted(task.outputs)},
                    source_type="workflow",
                    source_id=task.id,
                )
            ],
            artifact_ids=artifact_ids,
            failure_category="none",
            recommended_actions=["continue"],
        )

    def _apply_continue(
        self,
        session: Session,
        workflow: WorkflowRecord,
        decision: AgentDecisionRecord,
        observation: StepObservation,
        persisted_discovery_selection: DiscoverySelectionProjection | None,
    ) -> None:
        target = decision.target_step_key
        current_spec = _parse_analysis_spec(_current_analysis_spec(session, workflow.id))
        current_context = _loop_context(
            session,
            workflow,
            observation,
            current_spec,
        )
        if (
            target != current_context.next_step_key
            or "continue" not in determine_allowed_actions(current_context, observation)
            or current_context.discovery_selection != persisted_discovery_selection
        ):
            raise WorkflowFailure(
                "agent-target-step-stale",
                "The selected next step is no longer the current eligible operation.",
            )
        approved_plan_id = session.scalar(
            select(PlanRecord.id).where(
                PlanRecord.workflow_id == workflow.id,
                PlanRecord.status == "approved",
            )
        )
        if approved_plan_id is None:
            raise WorkflowFailure("agent-plan-missing", "The current approved plan is missing.")
        if target == "review-workflow":
            if workflow.status == "running":
                transition_workflow(session, workflow, "reviewing")
            enqueue_job(
                session,
                workflow,
                kind="review-workflow",
                operation_key=f"workflow:{workflow.id}:review:{approved_plan_id}",
            )
            return
        task = session.scalar(
            select(TaskRecord).where(
                TaskRecord.workflow_id == workflow.id,
                TaskRecord.plan_id == approved_plan_id,
                TaskRecord.step_key == target,
            )
        )
        if task is None or task.status != "pending":
            raise WorkflowFailure(
                "agent-target-step-invalid",
                "The selected next step is no longer pending.",
            )
        transition_task(session, task, "queued")
        enqueue_job(
            session,
            workflow,
            kind="execute-task",
            task=task,
            operation_key=_task_operation_key(workflow, task),
        )

    def _apply_retry(
        self,
        session: Session,
        workflow: WorkflowRecord,
        decision: AgentDecisionRecord,
        observation: StepObservationRecord,
        control_job: JobRecord,
    ) -> None:
        task = session.get(TaskRecord, observation.task_id) if observation.task_id else None
        if task is None or task.step_key != decision.target_step_key or task.status != "failed":
            raise WorkflowFailure("agent-retry-target-invalid", "The retry target is invalid.")
        source_job = session.get(JobRecord, observation.source_job_id)
        if source_job is None or source_job.error_code not in _TRANSIENT_FAILURE_CODES:
            raise WorkflowFailure("agent-retry-not-safe", "This failure is not safely retryable.")
        if task.task_type == "paper-discovery":
            invocation = session.scalar(
                select(ToolInvocationRecord).where(
                    ToolInvocationRecord.workflow_id == workflow.id,
                    ToolInvocationRecord.job_id == source_job.id,
                    ToolInvocationRecord.status == "failed",
                    ToolInvocationRecord.error_code == source_job.error_code,
                )
            )
            if invocation is None or invocation.error_code not in {
                "connector-unavailable",
                "rate-limited",
            }:
                raise WorkflowFailure(
                    "agent-retry-not-safe",
                    "The discovery connector did not grant a safe retry.",
                )
        if workflow.status in {"blocked", "failed"}:
            transition_workflow(session, workflow, "running")
        transition_task(session, task, "queued")
        task.retries += 1
        intent = session.scalar(
            select(AnalysisIntentRecord)
            .where(
                AnalysisIntentRecord.workflow_id == workflow.id,
                AnalysisIntentRecord.task_id == task.id,
            )
            .order_by(AnalysisIntentRecord.created_at.desc())
        )
        operation_key = source_job.operation_key
        if intent is not None and intent.status == "failed" and intent.decision == "approved":
            intent.status = "approved"
            operation_key = f"workflow:{workflow.id}:analysis-intent:{intent.id}"
        attempt = (
            session.scalar(
                select(func.max(JobRecord.attempt)).where(JobRecord.operation_key == operation_key)
            )
            or 0
        ) + 1
        enqueue_job(
            session,
            workflow,
            kind="execute-task",
            task=task,
            operation_key=operation_key,
            attempt=attempt,
            previous_job_id=source_job.id,
        )
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "agent.step-retry-requested",
                    AgentStepRetryRequestedEventData(
                        **_decision_event(decision, observation).model_dump()
                    ),
                    task.id,
                    control_job.id,
                )
            ],
        )

    def _apply_clarification(
        self,
        session: Session,
        workflow: WorkflowRecord,
        decision: AgentDecisionRecord,
    ) -> None:
        parsed = AgentDecision.model_validate_json(
            json.dumps(_decision_payload(decision), allow_nan=False, ensure_ascii=False),
            strict=True,
        )
        proposal = ClarificationProposal(
            reason=parsed.reason, requests=parsed.clarification_requests
        )
        interaction = create_scientific_interaction(
            session,
            workflow,
            proposal,
            selector_input_sha256=decision.input_sha256,
            selector_output_sha256=decision.output_sha256,
        )
        interaction.agent_decision_id = decision.id
        if workflow.status != "waiting-clarification":
            transition_workflow(session, workflow, "waiting-clarification")

    def _apply_spec_revision(
        self,
        session: Session,
        workflow: WorkflowRecord,
        decision: AgentDecisionRecord,
        observation: StepObservationRecord,
        control_job: JobRecord,
    ) -> tuple[str, str]:
        if workflow.workflow_type != "dataset-analysis":
            raise WorkflowFailure(
                "agent-spec-revision-workflow-invalid",
                "Analysis method revisions are only supported for dataset workflows.",
            )
        parsed = AgentDecision.model_validate_json(
            json.dumps(_decision_payload(decision), allow_nan=False, ensure_ascii=False),
            strict=True,
        )
        current_record = session.scalar(
            select(AnalysisSpecRecord)
            .where(
                AnalysisSpecRecord.workflow_id == workflow.id,
                AnalysisSpecRecord.status == "approved",
            )
            .order_by(AnalysisSpecRecord.revision.desc())
        )
        current_spec = _parse_analysis_spec(current_record)
        safe_revision = safe_analysis_spec_revision(current_spec)
        if (
            current_record is None
            or current_spec is None
            or safe_revision is None
            or parsed.proposed_analysis_spec is None
            or parsed.analysis_spec_diff is None
        ):
            raise WorkflowFailure(
                "agent-spec-revision-no-longer-valid",
                "The proposed scientific method revision is no longer valid.",
            )
        safe_spec, safe_diff = safe_revision
        proposed_payload = parsed.proposed_analysis_spec.model_dump(
            mode="json", by_alias=True
        )
        if (
            parsed.proposed_analysis_spec != safe_spec
            or parsed.analysis_spec_diff != safe_diff
            or decision.proposed_analysis_spec_json != proposed_payload
            or decision.proposed_analysis_spec_sha256 != content_sha256(proposed_payload)
            or analysis_spec_sha256(parsed.proposed_analysis_spec)
            != decision.proposed_analysis_spec_sha256
            or parsed.proposed_analysis_spec.dataset_source_id
            != current_record.dataset_source_id
            or parsed.proposed_analysis_spec.dataset_content_hash
            != current_record.dataset_content_hash
            or workflow.dataset_source_id != current_record.dataset_source_id
            or workflow.dataset_content_hash != current_record.dataset_content_hash
        ):
            raise WorkflowFailure(
                "agent-spec-revision-binding-invalid",
                "The proposed scientific method revision does not match the current dataset and approved method.",
            )
        existing = session.scalar(
            select(AnalysisSpecRecord).where(
                AnalysisSpecRecord.workflow_id == workflow.id,
                AnalysisSpecRecord.proposed_by_decision_id == decision.id,
            )
        )
        if existing is not None:
            raise WorkflowFailure(
                "agent-spec-revision-duplicate",
                "The confirmed scientific method revision was already materialized.",
            )
        proposed_record = AnalysisSpecRecord(
            id=str(uuid.uuid4()),
            workflow_id=workflow.id,
            revision=current_record.revision + 1,
            previous_spec_id=current_record.id,
            schema_version=parsed.proposed_analysis_spec.schema_version,
            selector_kind="local-deterministic",
            selector_reason=parsed.analysis_spec_diff.reason,
            prompt_version="agent-analysis-spec-revision-v1",
            model_invocation_id=None,
            proposed_by_decision_id=decision.id,
            revision_reason=decision.reason,
            dataset_source_id=current_record.dataset_source_id,
            dataset_content_hash=current_record.dataset_content_hash,
            dataset_profile_sha256=current_record.dataset_profile_sha256,
            spec_json=proposed_payload,
            spec_sha256=decision.proposed_analysis_spec_sha256,
            status="pending-approval",
        )
        current_record.status = "superseded"
        session.add(proposed_record)
        session.flush()
        current_plan_id = session.scalar(
            select(PlanRecord.id).where(
                PlanRecord.workflow_id == workflow.id,
                PlanRecord.status == "approved",
            )
        )
        if current_plan_id is None:
            raise WorkflowFailure(
                "agent-spec-revision-plan-missing",
                "The approved analysis method has no current research plan.",
            )
        stale_task_ids = list(
            session.scalars(
                select(TaskRecord.id).where(
                    TaskRecord.workflow_id == workflow.id,
                    TaskRecord.plan_id == current_plan_id,
                    TaskRecord.status.in_(
                        [
                            "pending",
                            "queued",
                            "waiting-approval",
                            "failed",
                            "blocked",
                        ]
                    ),
                )
            )
        )
        if stale_task_ids:
            now = utc_now()
            session.execute(
                update(JobRecord)
                .where(
                    JobRecord.workflow_id == workflow.id,
                    JobRecord.task_id.in_(stale_task_ids),
                    JobRecord.status == "queued",
                )
                .values(status="cancelled", finished_at=now)
                .execution_options(synchronize_session=False)
            )
            session.execute(
                update(TaskRecord)
                .where(TaskRecord.id.in_(stale_task_ids))
                .values(status="cancelled", finished_at=now, updated_at=now)
                .execution_options(synchronize_session=False)
            )
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "analysis.spec-superseded",
                    AnalysisSpecEventData(
                        analysis_spec_id=current_record.id,
                        revision=current_record.revision,
                        spec_sha256=current_record.spec_sha256,
                        dataset_profile_sha256=current_record.dataset_profile_sha256,
                        selector_kind=cast(Any, current_record.selector_kind),
                        prompt_version=current_record.prompt_version,
                    ),
                    observation.task_id,
                    control_job.id,
                ),
                (
                    "analysis.spec-created",
                    AnalysisSpecEventData(
                        analysis_spec_id=proposed_record.id,
                        revision=proposed_record.revision,
                        spec_sha256=proposed_record.spec_sha256,
                        dataset_profile_sha256=proposed_record.dataset_profile_sha256,
                        selector_kind=cast(Any, proposed_record.selector_kind),
                        prompt_version=proposed_record.prompt_version,
                    ),
                    observation.task_id,
                    control_job.id,
                ),
            ],
        )
        if workflow.status in {"reviewing", "running"}:
            transition_workflow(
                session,
                workflow,
                "blocked",
                reason_code="agent-analysis-spec-revision-approved",
                blocking_message="The confirmed method revision is ready for replanning.",
            )
        if workflow.status not in {"blocked", "failed"}:
            raise WorkflowFailure(
                "agent-spec-revision-state-invalid",
                "The workflow is no longer in a state that can be replanned.",
            )
        transition_workflow(session, workflow, "planning")
        plan_version = _next_agent_plan_version(session, workflow.id)
        enqueue_job(
            session,
            workflow,
            kind="generate-plan",
            operation_key=f"workflow:{workflow.id}:plan:{plan_version}",
        )
        return current_record.id, proposed_record.id

    def _apply_complete(
        self,
        session: Session,
        workflow: WorkflowRecord,
        observation: StepObservationRecord,
    ) -> None:
        parsed = _observation_from_record(observation)
        current_spec = _parse_analysis_spec(_current_analysis_spec(session, workflow.id))
        context = _loop_context(session, workflow, parsed, current_spec)
        if (
            "complete" not in parsed.recommended_actions
            or parsed.status not in {"succeeded", "needs-review"}
            or not completion_invariant_satisfied(context)
        ):
            raise WorkflowFailure(
                "agent-completion-invariant-failed",
                "The current workflow state no longer satisfies every completion invariant.",
            )
        transition_workflow(session, workflow, "completed")

    def _apply_stop(
        self,
        session: Session,
        workflow: WorkflowRecord,
        decision: AgentDecisionRecord,
    ) -> None:
        if workflow.status in {"running", "reviewing", "planning", "waiting-plan-approval"}:
            transition_workflow(
                session,
                workflow,
                "blocked",
                reason_code=decision.reason_code,
                blocking_message=decision.reason,
            )


def _persist_observation(
    session: Session,
    observation: StepObservation,
) -> StepObservationRecord:
    output_sha256 = step_observation_sha256(observation)
    input_sha256 = content_sha256(
        {
            "attempt": observation.attempt,
            "sourceJobId": observation.source_job_id,
            "workflowId": observation.workflow_id,
        }
    )
    record = StepObservationRecord(
        id=str(uuid.uuid4()),
        workflow_id=observation.workflow_id,
        plan_id=observation.plan_id,
        task_id=observation.task_id,
        source_job_id=observation.source_job_id,
        run_id=observation.run_id,
        review_id=observation.review_id,
        schema_version="1",
        observation_type=observation.observation_type,
        step_key=observation.step_key,
        attempt=observation.attempt,
        status=observation.status,
        facts_json=[item.model_dump(mode="json", by_alias=True) for item in observation.facts],
        warnings_json=[
            item.model_dump(mode="json", by_alias=True) for item in observation.warnings
        ],
        unresolved_questions_json=[
            item.model_dump(mode="json", by_alias=True) for item in observation.unresolved_questions
        ],
        artifact_ids_json=list(observation.artifact_ids),
        failure_category=observation.failure_category,
        recommended_actions_json=list(observation.recommended_actions),
        input_sha256=input_sha256,
        output_sha256=output_sha256,
        generator="deterministic-observer-v1",
        prompt_version=None,
        model=None,
        model_invocation_id=None,
    )
    session.add(record)
    session.flush()
    return record


def _persist_decision(
    session: Session,
    workflow: WorkflowRecord,
    observation: StepObservationRecord,
    result: AgentDecisionResult,
    invocation: ModelInvocationRecord | None,
) -> AgentDecisionRecord:
    value = result.decision
    revision = (
        session.scalar(
            select(func.max(AgentDecisionRecord.decision_revision)).where(
                AgentDecisionRecord.workflow_id == workflow.id
            )
        )
        or 0
    ) + 1
    proposed = value.proposed_analysis_spec
    record = AgentDecisionRecord(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        observation_id=observation.id,
        schema_version="1",
        decision_revision=revision,
        expected_workflow_revision=workflow.row_version,
        action=value.action,
        reason_code=value.reason_code,
        reason=value.reason,
        target_step_key=value.target_step_key,
        proposed_analysis_spec_json=(
            proposed.model_dump(mode="json", by_alias=True) if proposed is not None else None
        ),
        proposed_analysis_spec_sha256=(
            content_sha256(proposed.model_dump(mode="json", by_alias=True))
            if proposed is not None
            else None
        ),
        analysis_spec_diff_json=(
            value.analysis_spec_diff.model_dump(mode="json", by_alias=True)
            if value.analysis_spec_diff is not None
            else None
        ),
        clarification_requests_json=[
            item.model_dump(mode="json", by_alias=True) for item in value.clarification_requests
        ],
        requires_user_confirmation=value.requires_user_confirmation,
        generator=result.generator,
        prompt_version=result.prompt_version,
        model=result.model_used,
        model_invocation_id=invocation.id if invocation is not None else None,
        input_sha256=result.input_sha256,
        output_sha256=result.output_sha256,
        status=("waiting-user-confirmation" if value.requires_user_confirmation else "proposed"),
    )
    session.add(record)
    session.flush()
    return record


def _begin_decision_invocation(
    session: Session,
    workflow: WorkflowRecord,
    job: JobRecord,
    *,
    model: str,
    endpoint_identity: str,
    input_sha256: str,
) -> ModelInvocationRecord:
    if not model.strip() or not endpoint_identity.strip():
        raise WorkflowFailure(
            "agent-next-action-destination-invalid",
            "The approved next-action destination is incomplete.",
        )
    operation_key = f"{job.operation_key}:model"
    record = ModelInvocationRecord(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        schema_version="1",
        operation_type="agent-next-action",
        operation_key=operation_key,
        attempt=job.attempt,
        generator="agent-next-action-v1",
        model=model,
        endpoint_identity=endpoint_identity,
        prompt_version=AGENT_NEXT_ACTION_PROMPT_VERSION,
        input_sha256=input_sha256,
        output_sha256=None,
        token_usage={},
        validation_errors=[],
        request_idempotency_key=_decision_request_idempotency_key(
            workflow.id,
            operation_key,
            input_sha256,
        ),
        request_payload_sha256=input_sha256,
        status="pending",
        error_code=None,
        error_message=None,
        finished_at=None,
    )
    session.add(record)
    session.flush()
    return record


def _decision_invocation_for_operation(
    session: Session,
    workflow: WorkflowRecord,
    decision_operation_key: str,
) -> ModelInvocationRecord | None:
    invocations = list(
        session.scalars(
            select(ModelInvocationRecord).where(
                ModelInvocationRecord.workflow_id == workflow.id,
                ModelInvocationRecord.operation_type == "agent-next-action",
                ModelInvocationRecord.operation_key == f"{decision_operation_key}:model",
            )
        )
    )
    if len(invocations) > 1:
        raise WorkflowFailure(
            "agent-next-action-invocation-identity-conflict",
            "More than one model request exists for this logical next-action decision.",
        )
    return invocations[0] if invocations else None


def _decision_request_idempotency_key(
    workflow_id: str,
    operation_key: str,
    input_sha256: str,
) -> str:
    return "agent-next-action:" + content_sha256(
        {
            "inputSha256": input_sha256,
            "operationKey": operation_key,
            "workflowId": workflow_id,
        }
    )


def _assert_decision_invocation_binding(
    session: Session,
    workflow: WorkflowRecord,
    decision_job: JobRecord,
    invocation: ModelInvocationRecord,
    result: AgentDecisionResult,
    gateway: OpenAICompatibleModelGateway,
) -> None:
    operation_key = f"{decision_job.operation_key}:model"
    request_key = _decision_request_idempotency_key(
        workflow.id,
        operation_key,
        result.input_sha256,
    )
    source_job = session.scalar(
        select(JobRecord).where(
            JobRecord.workflow_id == workflow.id,
            JobRecord.operation_key == decision_job.operation_key,
            JobRecord.attempt == invocation.attempt,
        )
    )
    approval_events = list(
        session.scalars(
            select(EventRecord).where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.event_type == "remote-data.approved",
            )
        )
    )
    approval = approval_events[0].payload if len(approval_events) == 1 else {}
    if (
        invocation.workflow_id != workflow.id
        or invocation.operation_type != "agent-next-action"
        or invocation.operation_key != operation_key
        or invocation.generator != "agent-next-action-v1"
        or invocation.prompt_version != AGENT_NEXT_ACTION_PROMPT_VERSION
        or invocation.model != gateway.default_model
        or invocation.endpoint_identity != gateway.endpoint_identity
        or invocation.input_sha256 != result.input_sha256
        or invocation.request_payload_sha256 != result.input_sha256
        or invocation.request_idempotency_key != request_key
        or source_job is None
        or source_job.kind != "decide-next-action"
        or approval.get("provider") != "openai-compatible"
        or approval.get("endpointIdentity") != invocation.endpoint_identity
        or approval.get("model") != invocation.model
        or approval.get("dataCategories")
        != list(AUTONOMOUS_REMOTE_DATA_CATEGORIES)
    ):
        raise WorkflowFailure(
            "agent-next-action-invocation-approval-mismatch",
            "The stored next-action request does not match its logical operation or "
            "durable remote-data approval.",
        )


def _recover_decision_invocation(
    *,
    workflow: WorkflowRecord,
    observation: StepObservation,
    context: AgentLoopContext,
    current_analysis_spec: AnalysisSpec | None,
    plan_summary: dict[str, object] | None,
    answered_interactions: tuple[dict[str, object], ...],
    research_context: dict[str, object],
    invocation: ModelInvocationRecord,
) -> AgentDecisionResult:
    if invocation.status != "pending":
        raise WorkflowFailure(
            "agent-next-action-invocation-incomplete",
            "A terminal model request exists without a durable decision; it will not be repeated.",
        )
    # The durable request was created after its bounded input was hashed, so the
    # current persisted count includes this pending request while the original
    # input count did not. Reconstruct that exact pre-request context without
    # excluding the pending request from the live loop budget.
    if context.counts.model_decisions < 1:
        raise WorkflowFailure(
            "agent-next-action-invocation-count-invalid",
            "The recovered next-action request is missing from the durable loop budget.",
        )
    request_context = replace(
        context,
        counts=replace(
            context.counts,
            model_decisions=context.counts.model_decisions - 1,
        ),
    )
    result = recover_unknown_next_action(
        goal=workflow.goal,
        observation=observation,
        context=request_context,
        current_analysis_spec=current_analysis_spec,
        plan_summary=plan_summary,
        answered_interactions=answered_interactions,
        research_context=research_context,
        model=invocation.model,
        endpoint_identity=invocation.endpoint_identity,
    )
    if (
        invocation.input_sha256 != result.input_sha256
        or invocation.request_payload_sha256 != result.input_sha256
        or invocation.prompt_version != result.prompt_version
    ):
        raise WorkflowFailure(
            "agent-next-action-invocation-input-changed",
            "The recovered next-action request no longer matches its durable input.",
        )
    return result


def _finalize_decision_invocation(
    session: Session,
    workflow: WorkflowRecord,
    invocation: ModelInvocationRecord | None,
    result: AgentDecisionResult,
) -> ModelInvocationRecord | None:
    if invocation is None:
        if result.used_model:
            raise WorkflowFailure(
                "agent-next-action-provenance-missing",
                "The model-assisted decision has no durable request record.",
            )
        return None
    if (
        not result.used_model
        or result.model_used != invocation.model
        or result.endpoint_identity != invocation.endpoint_identity
        or result.prompt_version != invocation.prompt_version
        or result.input_sha256 != invocation.input_sha256
    ):
        raise WorkflowFailure(
            "agent-next-action-result-mismatch",
            "The model-assisted decision does not match its durable request record.",
        )
    failed = result.parse_result != "valid"
    finalized = session.execute(
        update(ModelInvocationRecord)
        .where(
            ModelInvocationRecord.id == invocation.id,
            ModelInvocationRecord.workflow_id == workflow.id,
            ModelInvocationRecord.status == "pending",
        )
        .values(
            output_sha256=(
                None
                if result.parse_result
                in {"model-request-failed", "model-request-outcome-unknown"}
                else result.output_sha256
            ),
            token_usage=result.token_usage,
            validation_errors=[{"code": item} for item in result.validation_errors],
            status="failed" if failed else "succeeded",
            error_code=result.parse_result if failed else None,
            error_message=(
                "The remote next-action request failed, was invalid, or had an unknown outcome; "
                "a bounded fallback was used."
                if failed
                else None
            ),
            finished_at=utc_now(),
        )
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[object], finalized).rowcount != 1:
        raise WorkflowFailure(
            "agent-next-action-invocation-finalize-conflict",
            "The durable next-action request was finalized concurrently.",
        )
    session.refresh(invocation)
    return invocation


def _fail_pending_decision_invocation(
    session: Session,
    invocation: ModelInvocationRecord,
    *,
    code: str,
    message: str,
) -> None:
    session.execute(
        update(ModelInvocationRecord)
        .where(
            ModelInvocationRecord.id == invocation.id,
            ModelInvocationRecord.status == "pending",
        )
        .values(
            status="failed",
            error_code=code,
            error_message=message[:2_000],
            finished_at=utc_now(),
        )
        .execution_options(synchronize_session=False)
    )


def _assert_decision_job_lease(job: JobRecord, expected_lease_token: str) -> None:
    if job.status != "leased" or job.lease_token != expected_lease_token:
        raise WorkflowFailure(
            "job-lease-lost",
            "The background job lease was lost before the next-action request began.",
            retryable=True,
        )


def _discovery_step_observation(
    session: Session,
    workflow: WorkflowRecord,
    source_job: JobRecord,
    task: TaskRecord,
    context: ObservationContext,
) -> StepObservation:
    spec_id = task.inputs.get("discoverySpecId")
    if not isinstance(spec_id, str):
        raise WorkflowFailure(
            "discovery-task-input-invalid",
            "The discovery task is missing its approved specification binding.",
        )
    spec = session.get(DiscoverySpecRecord, spec_id)
    invocation = session.scalar(
        select(ToolInvocationRecord)
        .where(ToolInvocationRecord.workflow_id == workflow.id, ToolInvocationRecord.job_id == source_job.id)
        .order_by(ToolInvocationRecord.created_at.desc())
    )
    if spec is None or spec.workflow_id != workflow.id or invocation is None:
        return build_failure_observation(
            context,
            VerifiedFailureSummary(
                error_code="discovery-observation-missing",
                user_message="The durable discovery invocation is missing.",
                failure_stage=task.step_key or "paper-discovery",
                failure_category="unknown",
                external_side_effects=False,
                safe_to_retry=False,
                requires_spec_revision=False,
                requires_user_input=False,
            ),
        )
    from ..discovery_schemas import DiscoverySpec

    parsed = DiscoverySpec.model_validate(spec.spec_json)
    operations = [
        (query.id, provider)
        for query in parsed.queries
        for provider in query.providers
    ]
    prior = list(
        session.scalars(
            select(ToolInvocationRecord)
            .where(
                ToolInvocationRecord.workflow_id == workflow.id,
                ToolInvocationRecord.discovery_spec_id == spec.id,
                ToolInvocationRecord.connector_name == PAPER_SEARCH_CONNECTOR_NAME,
                ToolInvocationRecord.status == "succeeded",
            )
            .order_by(ToolInvocationRecord.finished_at, ToolInvocationRecord.created_at, ToolInvocationRecord.id)
        )
    )
    consecutive_no_novelty = 0
    for item in reversed(prior):
        if int(item.novel_candidate_count or 0) > 0:
            break
        consecutive_no_novelty += 1
    attempted_operations = {
        item.operation_key
        for item in session.scalars(
            select(ToolInvocationRecord).where(
                ToolInvocationRecord.workflow_id == workflow.id,
                ToolInvocationRecord.discovery_spec_id == spec.id,
                ToolInvocationRecord.connector_name == PAPER_SEARCH_CONNECTOR_NAME,
                ToolInvocationRecord.status.in_(["succeeded", "failed", "outcome-unknown", "cancelled"]),
            )
        )
    }
    candidate_count = int(
        session.scalar(
            select(func.count(func.distinct(CandidateOccurrenceRecord.candidate_id)))
            .join(ToolInvocationRecord, ToolInvocationRecord.id == CandidateOccurrenceRecord.invocation_id)
            .where(
                CandidateOccurrenceRecord.project_id == workflow.project_id,
                ToolInvocationRecord.discovery_spec_id == spec.id,
                ToolInvocationRecord.connector_name == PAPER_SEARCH_CONNECTOR_NAME,
            )
        )
        or 0
    )
    stop_reached = (
        candidate_count >= parsed.stop_policy.min_unique_candidates
        or len(attempted_operations) >= parsed.stop_policy.max_attempts
        or consecutive_no_novelty >= parsed.stop_policy.max_consecutive_no_novelty
    )
    remaining = 0
    if not stop_reached:
        remaining = sum(
            1
            for query_id, provider in operations
            if f"discovery:{spec.id}:{query_id}:{provider}" not in attempted_operations
        )
    error_code = invocation.error_code
    return build_discovery_observation(
        context,
        invocation_id=invocation.id,
        query_id=invocation.query_id,
        provider=invocation.provider,
        returned_count=int(invocation.returned_count or 0),
        novel_candidate_count=int(invocation.novel_candidate_count or 0),
        duplicate_count=int(invocation.duplicate_count or 0),
        candidate_set_sha256=invocation.candidate_set_sha256,
        remaining_approved_operations=remaining,
        consecutive_no_novelty=consecutive_no_novelty,
        error_code=error_code,
        retry_safe=error_code in {"connector-unavailable", "rate-limited"},
        outcome_unknown=invocation.status == "outcome-unknown",
        stop_reached=stop_reached,
    )


def _verified_failure(
    session: Session,
    workflow: WorkflowRecord,
    source_job: JobRecord,
    task: TaskRecord | None,
) -> VerifiedFailureSummary:
    intent = (
        session.scalar(
            select(AnalysisIntentRecord)
            .where(
                AnalysisIntentRecord.workflow_id == workflow.id,
                AnalysisIntentRecord.task_id == task.id,
            )
            .order_by(AnalysisIntentRecord.created_at.desc())
        )
        if task is not None
        else None
    )
    summary = intent.error_summary if intent is not None else None
    summary_code = summary.get("code") if isinstance(summary, dict) else None
    code = (
        summary_code
        if isinstance(summary_code, str)
        else source_job.error_code or workflow.last_error_code or "unknown-failure"
    )
    summary_message = summary.get("userMessage") if isinstance(summary, dict) else None
    message = (
        summary_message
        if isinstance(summary_message, str)
        else source_job.error_message
        or workflow.last_error_message
        or "The workflow step failed without a safe structured explanation."
    )
    retryable = (
        bool(summary.get("retryable") if isinstance(summary, dict) else False)
        and code in _TRANSIENT_FAILURE_CODES
    )
    run = (
        session.scalar(
            select(RunRecord)
            .where(RunRecord.analysis_intent_id == intent.id)
            .order_by(RunRecord.attempt.desc())
        )
        if intent is not None
        else None
    )
    artifacts = (
        list(session.scalars(select(ArtifactRecord).where(ArtifactRecord.run_id == run.id)))
        if run is not None
        else []
    )
    category = (
        "method"
        if code in _METHOD_FAILURE_CODES
        else "runtime"
        if code in _TRANSIENT_FAILURE_CODES or code.startswith("runtime-")
        else "artifact"
        if code.startswith("artifact-")
        else "input"
        if code.startswith(("dataset-", "column-"))
        else "unknown"
    )
    return VerifiedFailureSummary(
        run_id=run.id if run is not None else None,
        error_code=code,
        user_message=message[:2_000],
        failure_stage=task.step_key if task is not None and task.step_key else source_job.kind,
        failure_category=cast(Any, category),
        artifact_ids=[artifact.id for artifact in artifacts],
        external_side_effects=False,
        safe_to_retry=retryable,
        requires_spec_revision=category == "method",
        requires_user_input=code in {"column-ambiguous", "method-confirmation-required"},
    )


def _assert_current_observation_binding(
    session: Session,
    workflow: WorkflowRecord,
    observation: StepObservationRecord,
) -> None:
    current_plan = session.scalar(
        select(PlanRecord).where(
            PlanRecord.workflow_id == workflow.id,
            PlanRecord.status == "approved",
        )
    )
    source_job = session.get(JobRecord, observation.source_job_id)
    if (
        current_plan is None
        or observation.plan_id != current_plan.id
        or source_job is None
        or source_job.workflow_id != workflow.id
        or source_job.status not in {"succeeded", "failed"}
    ):
        raise WorkflowFailure(
            "agent-observation-plan-stale",
            "The observation does not belong to the current approved plan.",
        )
    if source_job.kind == "execute-task":
        task = (
            session.get(TaskRecord, source_job.task_id)
            if source_job.task_id is not None
            else None
        )
        if (
            task is None
            or task.workflow_id != workflow.id
            or task.plan_id != current_plan.id
            or observation.task_id != task.id
            or observation.step_key != task.step_key
            or observation.observation_type not in {"step-output", "analysis-execution"}
        ):
            raise WorkflowFailure(
                "agent-observation-plan-stale",
                "The task observation does not belong to the current approved plan.",
            )
        return
    if source_job.kind == "review-workflow":
        review = (
            session.get(ReviewRecord, observation.review_id)
            if observation.review_id is not None
            else None
        )
        expected_review_type = _expected_review_type(workflow)
        review_task = (
            session.get(TaskRecord, review.task_id)
            if review is not None and review.task_id is not None
            else None
        )
        literature_task_invalid = bool(
            workflow.workflow_type == "literature-synthesis"
            and (
                review_task is None
                or review_task.workflow_id != workflow.id
                or review_task.plan_id != current_plan.id
                or review_task.step_key != "synthesize-extractive-claims"
            )
        )
        if (
            observation.observation_type != "review"
            or observation.task_id is not None
            or observation.step_key != "review-workflow"
            or review is None
            or review.workflow_id != workflow.id
            or review.plan_id != current_plan.id
            or review.review_type != expected_review_type
            or review.input_sha256 != source_job.input_sha256
            or literature_task_invalid
        ):
            raise WorkflowFailure(
                "agent-observation-plan-stale",
                "The review observation does not belong to the current approved plan.",
            )
        return
    raise WorkflowFailure(
        "agent-observation-source-invalid",
        "The observation source is not an Agent-controlled workflow step.",
    )


def _review_observation(
    session: Session,
    workflow: WorkflowRecord,
    source_job: JobRecord,
    context: ObservationContext,
) -> StepObservation:
    expected_review_type = _expected_review_type(workflow)
    review = session.scalar(
        select(ReviewRecord)
        .where(
            ReviewRecord.workflow_id == workflow.id,
            ReviewRecord.plan_id == context.plan_id,
            ReviewRecord.review_type == expected_review_type,
            ReviewRecord.input_sha256 == source_job.input_sha256,
        )
        .order_by(ReviewRecord.created_at.desc(), ReviewRecord.id.desc())
    )
    if review is None:
        raise WorkflowFailure("agent-review-missing", "The reviewer result is missing.")
    if workflow.workflow_type == "literature-synthesis":
        if review.verdict == "passed":
            status, failure, actions = "succeeded", "none", ["complete"]
        elif review.verdict == "revision-required":
            status, failure, actions = "blocked", "review", ["stop"]
        else:
            status, failure, actions = "failed", "review", ["stop"]
        return StepObservation(
            schema_version="1",
            workflow_id=workflow.id,
            plan_id=context.plan_id,
            task_id=None,
            source_job_id=source_job.id,
            run_id=None,
            review_id=review.id,
            observation_type="review",
            step_key="review-workflow",
            attempt=source_job.attempt,
            status=cast(Any, status),
            facts=[
                ObservationFact(
                    code="reviewer-verdict",
                    statement="The deterministic Reviewer recorded its terminal verdict.",
                    value=review.verdict,
                    source_type="review",
                    source_id=review.id,
                )
            ],
            warnings=[],
            artifact_ids=[],
            failure_category=cast(Any, failure),
            recommended_actions=cast(Any, actions),
        )
    payload = cast(dict[str, object], review.result_json)
    review_task = session.get(TaskRecord, review.task_id) if review.task_id is not None else None
    review_outputs = (
        cast(dict[str, object], review_task.outputs) if review_task is not None else {}
    )
    run_id_value = review_outputs.get("runId")
    run_id = run_id_value if isinstance(run_id_value, str) else None
    artifact_value = review_outputs.get("artifactIds")
    artifact_items = (
        cast(list[object], artifact_value) if isinstance(artifact_value, list) else []
    )
    artifact_ids = [item for item in artifact_items if isinstance(item, str)]
    warnings: list[dict[str, object]] = []
    for key in ("artifactIssues", "numericIssues", "methodWarnings"):
        value = payload.get(key)
        if isinstance(value, list):
            warning_items = cast(list[object], value)
            warnings.extend(
                cast(dict[str, object], item)
                for item in warning_items
                if isinstance(item, dict)
            )
    verdict = review.verdict
    if verdict == "passed":
        status, failure, actions = "succeeded", "none", ["complete"]
    elif verdict == "passed-with-warnings":
        status, failure, actions = "needs-review", "none", ["complete", "request-clarification"]
    elif verdict == "revision-required":
        status, failure, actions = (
            "blocked",
            "review",
            ["revise-analysis-spec", "request-clarification", "stop"],
        )
    elif verdict == "blocked":
        status, failure, actions = "blocked", "review", ["request-clarification", "stop"]
    else:
        status, failure, actions = "failed", "review", ["stop"]
    warning_artifact_ids: list[str] = []
    for item in warnings:
        artifact_id = item.get("artifactId")
        if isinstance(artifact_id, str):
            warning_artifact_ids.append(artifact_id)
    artifact_ids = list(dict.fromkeys([*artifact_ids, *warning_artifact_ids]))
    return StepObservation(
        schema_version="1",
        workflow_id=workflow.id,
        plan_id=context.plan_id,
        task_id=context.task_id,
        source_job_id=source_job.id,
        run_id=run_id,
        review_id=review.id,
        observation_type="review",
        step_key="review-workflow",
        attempt=source_job.attempt,
        status=cast(Any, status),
        facts=[
            ObservationFact(
                code="reviewer-verdict",
                statement="The deterministic Reviewer recorded its terminal verdict.",
                value=verdict,
                source_type="review",
                source_id=review.id,
            )
        ],
        warnings=[],
        artifact_ids=artifact_ids,
        failure_category=cast(Any, failure),
        recommended_actions=cast(Any, actions),
    )


def _loop_context(
    session: Session,
    workflow: WorkflowRecord,
    observation: StepObservation,
    current_spec: AnalysisSpec | None,
) -> AgentLoopContext:
    plan = session.scalar(
        select(PlanRecord).where(
            PlanRecord.workflow_id == workflow.id,
            PlanRecord.status == "approved",
        )
    )
    plan_id = plan.id if plan is not None else None
    tasks = list(
        session.scalars(
            select(TaskRecord)
            .where(TaskRecord.workflow_id == workflow.id, TaskRecord.plan_id == plan_id)
            .order_by(TaskRecord.order_index)
        )
    )
    failed_task = next(
        (
            task
            for task in tasks
            if task.id == observation.task_id and task.status == "failed"
        ),
        None,
    )
    if observation.status == "failed" and failed_task is not None:
        next_step = failed_task.step_key
        discovery_selection = None
    else:
        if plan is not None and plan.generator == "paper-discovery-v1":
            pending, discovery_selection = _select_discovery_next_task(
                session,
                workflow,
                plan,
                tasks,
            )
        else:
            pending = next((task for task in tasks if task.status == "pending"), None)
            discovery_selection = None
        next_step = pending.step_key if pending is not None else None
    review = (
        session.get(ReviewRecord, observation.review_id)
        if observation.review_id is not None
        else session.scalar(
            select(ReviewRecord)
            .where(
                ReviewRecord.workflow_id == workflow.id,
                ReviewRecord.plan_id == plan_id,
            )
            .order_by(ReviewRecord.created_at.desc(), ReviewRecord.id.desc())
        )
    )
    if review is not None and (
        review.workflow_id != workflow.id or review.plan_id != plan_id
    ):
        review = None
    if (
        next_step is None
        and tasks
        and all(task.status == "completed" for task in tasks)
        and review is None
    ):
        next_step = "review-workflow"
    run = session.get(RunRecord, observation.run_id) if observation.run_id is not None else None
    structured = (
        session.scalar(
            select(StructuredAnalysisResultRecord).where(
                StructuredAnalysisResultRecord.run_id == run.id
            )
        )
        if run is not None
        else None
    )
    intent = (
        session.get(AnalysisIntentRecord, run.analysis_intent_id)
        if run is not None and run.analysis_intent_id is not None
        else None
    )
    pending_approval = (
        session.scalar(
            select(ApprovalRecord.id).where(
                ApprovalRecord.workflow_id == workflow.id,
                ApprovalRecord.plan_id == plan_id,
                ApprovalRecord.user_decision.is_(None),
            )
        )
        is not None
    )
    failure_code = next(
        (
            str(fact.value)
            for fact in observation.facts
            if fact.code == "failure-code" and isinstance(fact.value, str)
        ),
        None,
    )
    spec_revision_valid = bool(
        current_spec is not None
        and (
            (
                current_spec.operation.type == "two-group-comparison"
                and current_spec.operation.method == "welch-t-test"
            )
            or (
                current_spec.operation.type == "correlation"
                and current_spec.operation.method == "pearson"
            )
        )
    )
    artifact_ids = list(dict.fromkeys(observation.artifact_ids))
    artifacts = (
        list(
            session.scalars(
                select(ArtifactRecord).where(ArtifactRecord.id.in_(artifact_ids))
            )
        )
        if artifact_ids
        else []
    )
    required_artifacts_exist = bool(
        run is not None
        and artifact_ids
        and len(artifacts) == len(artifact_ids)
        and all(artifact.run_id == run.id for artifact in artifacts)
    )
    unresolved_required_interaction = (
        session.scalar(
            select(InteractionRequestRecord.id).where(
                InteractionRequestRecord.workflow_id == workflow.id,
                InteractionRequestRecord.status == "pending",
            )
        )
        is not None
    )
    warning_acceptance_events = (
        list(
            session.scalars(
                select(EventRecord).where(
                    EventRecord.workflow_id == workflow.id,
                    EventRecord.event_type == "analysis.review-warnings-accepted",
                )
            )
        )
        if review is not None and review.verdict == "passed-with-warnings"
        else []
    )
    review_warnings_accepted = bool(
        review is not None
        and len(warning_acceptance_events) == 1
        and warning_acceptance_events[0].payload.get("reviewId") == review.id
        and warning_acceptance_events[0].payload.get("reviewInputSha256")
        == review.input_sha256
    )
    completion_bundle_current = bool(
        "complete" in observation.recommended_actions
        and _completion_bundle_matches(
            session,
            workflow,
            observation,
            plan_id=plan_id,
        )
    )
    return AgentLoopContext(
        counts=persisted_loop_counts(session, workflow.id),
        next_step_key=next_step,
        discovery_selection=discovery_selection,
        run_completed=completion_bundle_current and run is not None and run.status == "completed",
        structured_result_exists=completion_bundle_current and structured is not None,
        required_artifacts_exist=completion_bundle_current and required_artifacts_exist,
        analysis_spec_current=bool(
            completion_bundle_current
            and current_spec is not None
            and intent is not None
            and intent.analysis_spec_id == _current_analysis_spec_id(session, workflow.id)
        ),
        analysis_intent_approved_and_current=bool(
            completion_bundle_current
            and intent is not None
            and intent.decision == "approved"
            and intent.status == "completed"
        ),
        literature_result_verified=bool(
            workflow.workflow_type == "literature-synthesis"
            and completion_bundle_current
        ),
        reviewer_verdict=cast(
            Any,
            review.verdict
            if completion_bundle_current and review is not None
            else None,
        ),
        review_warnings_accepted=review_warnings_accepted,
        unresolved_required_interaction=unresolved_required_interaction,
        pending_approval=pending_approval,
        required_revision=review is not None and bool(review.result_json.get("requiredRevisions")),
        failure_code=failure_code,
        failure_is_transient=failure_code in _TRANSIENT_FAILURE_CODES,
        terminal_result_exists=run is not None and run.status == "completed",
        spec_revision_is_valid=spec_revision_valid,
        clarification_is_available=bool(observation.unresolved_questions),
        capability_unsupported=observation.failure_category == "unsupported",
        input_is_irrecoverable=observation.failure_category == "input",
    )


def _select_discovery_next_task(
    session: Session,
    workflow: WorkflowRecord,
    plan: PlanRecord,
    tasks: list[TaskRecord],
) -> tuple[TaskRecord | None, DiscoverySelectionProjection | None]:
    """Select one exact approved Discovery operation from durable result signals.

    The immutable plan remains the complete authority boundary. This selector
    only reorders still-pending members of that set; it cannot add a query,
    provider, permission, retry, or budget. A stable hash is used solely as the
    final tie-breaker so equivalent durable snapshots recover the same choice.
    """

    try:
        assert_plan_approval_integrity(session, workflow, plan)
        spec_id = plan.spec_json.get("discoverySpecId")
        if not isinstance(spec_id, str) or not spec_id:
            raise DiscoveryAdapterError("approved discovery plan has no specification")
        record = session.get(DiscoverySpecRecord, spec_id)
        if (
            record is None
            or record.workflow_id != workflow.id
            or record.status != "approved"
        ):
            raise DiscoveryAdapterError("approved discovery specification is not current")
        spec = DiscoverySpec.model_validate(record.spec_json)
        if (
            discovery_sha256(spec) != record.spec_sha256
            or plan.spec_json != discovery_plan_spec(record, spec)
            or plan.spec_sha256 != content_sha256(plan.spec_json)
        ):
            raise DiscoveryAdapterError(
                "approved discovery plan no longer matches its immutable specification"
            )
        operations = discovery_operations(record, spec)
    except (DiscoveryAdapterError, ValidationError, WorkflowConflict) as error:
        raise WorkflowFailure(
            "discovery-selection-authority-invalid",
            "The current Discovery operation set no longer matches its approval.",
        ) from error

    if len(tasks) != len(operations):
        raise WorkflowFailure(
            "discovery-selection-task-set-invalid",
            "The current Discovery task set does not match the approved operations.",
        )

    expected_by_key: dict[str, tuple[str, str]] = {}
    task_by_operation: dict[str, TaskRecord] = {}
    for order_index, (task, operation) in enumerate(
        zip(tasks, operations, strict=True),
        start=1,
    ):
        query_id = operation.query.id
        provider = operation.provider
        step_key = discovery_step_key(query_id, provider)
        operation_key = discovery_operation_key(record.id, query_id, provider)
        expected_input = discovery_task_input(record, query_id, provider)
        expected_objective = f"Search {provider} for approved query {query_id}."
        expected_hash = content_sha256(
            {
                "inputs": expected_input,
                "objective": expected_objective,
                "stepKey": step_key,
                "stepType": "paper-discovery",
            }
        )
        if (
            task.project_id != workflow.project_id
            or task.workflow_id != workflow.id
            or task.plan_id != plan.id
            or task.step_key != step_key
            or task.order_index != order_index
            or task.objective != expected_objective
            or task.task_type != "paper-discovery"
            or task.inputs != expected_input
            or task.input_sha256 != expected_hash
            or task.expected_outputs != ["discovery-observation"]
            or task.acceptance_criteria
            != ["persist-structured-discovery-observation"]
            or task.permissions != ["remote-paper-search"]
            or task.risk_level != "medium"
            or task.timeout_seconds != 120
        ):
            raise WorkflowFailure(
                "discovery-selection-task-set-invalid",
                "A Discovery task no longer matches its exact approved operation.",
            )
        expected_by_key[operation_key] = (query_id, provider)
        task_by_operation[operation_key] = task

    invocations = list(
        session.scalars(
            select(ToolInvocationRecord)
            .where(
                ToolInvocationRecord.workflow_id == workflow.id,
                ToolInvocationRecord.discovery_spec_id == record.id,
            )
            .order_by(
                ToolInvocationRecord.finished_at,
                ToolInvocationRecord.created_at,
                ToolInvocationRecord.id,
            )
        )
    )
    for invocation in invocations:
        expected = expected_by_key.get(invocation.operation_key)
        expected_task = task_by_operation.get(invocation.operation_key)
        if (
            expected is None
            or expected_task is None
            or invocation.project_id != workflow.project_id
            or (invocation.query_id, invocation.provider) != expected
        ):
            raise WorkflowFailure(
                "discovery-selection-operation-invalid",
                "A Discovery invocation is outside the exact approved operation set.",
            )
        try:
            validate_terminal_discovery_invocation(
                session,
                workflow=workflow,
                discovery_spec=record,
                invocation=invocation,
                expected_plan=plan,
                expected_task=expected_task,
            )
        except DiscoveryAdapterError as error:
            raise WorkflowFailure(
                "discovery-selection-invocation-invalid",
                "A Discovery invocation failed terminal integrity validation.",
            ) from error

    invocation_history: dict[str, list[ToolInvocationRecord]] = {}
    for invocation in invocations:
        invocation_history.setdefault(invocation.operation_key, []).append(invocation)
    for invocation_attempts in invocation_history.values():
        invocation_attempts.sort(
            key=lambda item: (item.attempt, item.created_at, item.id)
        )
        if [item.attempt for item in invocation_attempts] != list(
            range(1, len(invocation_attempts) + 1)
        ):
            raise WorkflowFailure(
                "discovery-selection-history-invalid",
                "Discovery retry attempts are not contiguous.",
            )
        # No second operation may be selected while a remote outcome is in
        # flight, unknown, cancelled, or awaiting the existing retry decision.
        if any(
            item.status in {"prepared", "pending", "outcome-unknown", "cancelled"}
            for item in invocation_attempts
        ):
            return None, None
        if invocation_attempts[-1].status == "failed":
            return None, None
        if any(
            item.status == "failed"
            and item.error_code not in {"connector-unavailable", "rate-limited"}
            for item in invocation_attempts[:-1]
        ):
            raise WorkflowFailure(
                "discovery-selection-history-invalid",
                "A Discovery success follows a failure that was not safe to retry.",
            )
    if any(task.status in {"queued", "running", "failed", "blocked"} for task in tasks):
        return None, None

    succeeded = [item for item in invocations if item.status == "succeeded"]
    successful_keys = {item.operation_key for item in succeeded}
    if len(successful_keys) != len(succeeded):
        raise WorkflowFailure(
            "discovery-selection-history-invalid",
            "A Discovery operation has more than one successful terminal outcome.",
        )
    for operation_key, task in task_by_operation.items():
        if task.status == "completed" and operation_key not in successful_keys:
            raise WorkflowFailure(
                "discovery-selection-history-invalid",
                "A completed Discovery task has no matching durable success.",
            )
        if task.status == "pending" and operation_key in successful_keys:
            raise WorkflowFailure(
                "discovery-selection-history-invalid",
                "A pending Discovery task already has a terminal result.",
            )

    consecutive_no_novelty = 0
    for item in reversed(succeeded):
        if int(item.novel_candidate_count or 0) > 0:
            break
        consecutive_no_novelty += 1
    candidate_count = int(
        session.scalar(
            select(func.count(func.distinct(CandidateOccurrenceRecord.candidate_id)))
            .join(
                ToolInvocationRecord,
                ToolInvocationRecord.id == CandidateOccurrenceRecord.invocation_id,
            )
            .where(
                CandidateOccurrenceRecord.project_id == workflow.project_id,
                ToolInvocationRecord.discovery_spec_id == record.id,
                ToolInvocationRecord.connector_name == PAPER_SEARCH_CONNECTOR_NAME,
            )
        )
        or 0
    )
    if (
        candidate_count >= spec.stop_policy.min_unique_candidates
        or len(successful_keys) >= spec.stop_policy.max_attempts
        or consecutive_no_novelty
        >= spec.stop_policy.max_consecutive_no_novelty
    ):
        return None, None

    eligible = [
        (operation_key, task)
        for operation_key, task in task_by_operation.items()
        if task.status == "pending" and operation_key not in successful_keys
    ]
    if not eligible:
        return None, None

    query_stats: dict[str, dict[str, int]] = {}
    provider_stats: dict[str, int] = {}
    selection_history: list[dict[str, object]] = []
    for item in succeeded:
        stats = query_stats.setdefault(
            item.query_id,
            {"attempts": 0, "noNovelty": 0, "novel": 0, "duplicates": 0},
        )
        novel = int(item.novel_candidate_count or 0)
        duplicates = int(item.duplicate_count or 0)
        stats["attempts"] += 1
        stats["noNovelty"] += int(novel == 0)
        stats["novel"] += novel
        stats["duplicates"] += duplicates
        provider_stats[item.provider] = provider_stats.get(item.provider, 0) + 1
        selection_history.append(
            {
                "attempt": item.attempt,
                "duplicateCount": duplicates,
                "novelCandidateCount": novel,
                "operationKey": item.operation_key,
                "outputSha256": item.output_sha256,
                "returnedCount": int(item.returned_count or 0),
            }
        )
    selection_snapshot_sha256 = content_sha256(
        {
            "candidateCount": candidate_count,
            "consecutiveNoNovelty": consecutive_no_novelty,
            "discoverySpecSha256": record.spec_sha256,
            "eligibleOperationKeys": sorted(key for key, _task in eligible),
            "history": selection_history,
            "planSha256": plan.spec_sha256,
            "workflowId": workflow.id,
        }
    )

    def rank(item: tuple[str, TaskRecord]) -> tuple[int, int, int, int, int, str]:
        operation_key, _task = item
        query_id, provider = expected_by_key[operation_key]
        stats = query_stats.get(
            query_id,
            {"attempts": 0, "noNovelty": 0, "novel": 0, "duplicates": 0},
        )
        return (
            stats["attempts"],
            provider_stats.get(provider, 0),
            stats["noNovelty"],
            -stats["novel"],
            stats["duplicates"],
            content_sha256(
                {
                    "operationKey": operation_key,
                    "selectionSnapshotSha256": selection_snapshot_sha256,
                }
            ),
        )

    ranked = sorted(eligible, key=rank)
    operation_signals: list[DiscoverySelectionOperationSignal] = []
    for position, (operation_key, task) in enumerate(ranked, start=1):
        query_id, provider = expected_by_key[operation_key]
        stats = query_stats.get(
            query_id,
            {"attempts": 0, "noNovelty": 0, "novel": 0, "duplicates": 0},
        )
        operation_signals.append(
            DiscoverySelectionOperationSignal(
                operation_key=operation_key,
                step_key=cast(str, task.step_key),
                query_id=query_id,
                provider=cast(DiscoveryProvider, provider),
                query_attempt_count=stats["attempts"],
                provider_attempt_count=provider_stats.get(provider, 0),
                query_no_novelty_count=stats["noNovelty"],
                query_novel_candidate_count=stats["novel"],
                query_duplicate_count=stats["duplicates"],
                tie_break_sha256=rank((operation_key, task))[-1],
                rank=position,
            )
        )
    reason_code = _discovery_selection_reason(operation_signals)
    selected_operation_key, selected_task = ranked[0]
    projection = DiscoverySelectionProjection(
        schema_version="1",
        policy_version="discovery-next-operation-v1",
        workflow_id=workflow.id,
        plan_id=plan.id,
        plan_sha256=plan.spec_sha256,
        discovery_spec_id=record.id,
        discovery_spec_revision=record.revision,
        discovery_spec_sha256=record.spec_sha256,
        eligible_operations=operation_signals,
        selected_operation_key=selected_operation_key,
        selected_step_key=cast(str, selected_task.step_key),
        selection_snapshot_sha256=selection_snapshot_sha256,
        reason_code=reason_code,
        postcondition="queue-selected-pending-approved-operation-only",
    )
    return selected_task, projection


def _discovery_selection_reason(
    operations: list[DiscoverySelectionOperationSignal],
) -> Literal[
    "only-eligible-operation",
    "query-coverage-gap",
    "provider-coverage-gap",
    "lower-query-no-novelty",
    "higher-observed-novelty",
    "lower-duplicate-burden",
    "stable-tie-break",
]:
    if len(operations) == 1:
        return "only-eligible-operation"
    dimensions: tuple[tuple[str, list[int]], ...] = (
        (
            "query-coverage-gap",
            [item.query_attempt_count for item in operations],
        ),
        (
            "provider-coverage-gap",
            [item.provider_attempt_count for item in operations],
        ),
        (
            "lower-query-no-novelty",
            [item.query_no_novelty_count for item in operations],
        ),
        (
            "higher-observed-novelty",
            [-item.query_novel_candidate_count for item in operations],
        ),
        (
            "lower-duplicate-burden",
            [item.query_duplicate_count for item in operations],
        ),
    )
    for reason, values in dimensions:
        if len(set(values)) > 1:
            return reason
    return "stable-tie-break"


def _completion_bundle_matches(
    session: Session,
    workflow: WorkflowRecord,
    observation: StepObservation,
    *,
    plan_id: str | None,
) -> bool:
    if workflow.workflow_type == "literature-synthesis":
        return _literature_completion_bundle_matches(
            session,
            workflow,
            observation,
            plan_id=plan_id,
        )
    if (
        plan_id is None
        or observation.plan_id != plan_id
        or observation.review_id is None
        or observation.run_id is None
    ):
        return False
    try:
        snapshot = workflow_snapshot(session, workflow)
    except (ValidationError, WorkflowConflict, ValueError):
        return False
    plan = snapshot.plan
    intent = snapshot.analysis_intent
    run = snapshot.analysis_run
    spec = snapshot.analysis_spec
    structured = snapshot.structured_result
    review = snapshot.latest_review
    if (
        plan is None
        or plan.id != plan_id
        or plan.status != "approved"
        or intent is None
        or intent.status != "completed"
        or intent.decision != "approved"
        or run is None
        or run.id != observation.run_id
        or run.status != "completed"
        or spec is None
        or spec.status != "approved"
        or structured is None
        or review is None
        or review.id != observation.review_id
        or not isinstance(review.result, DatasetAnalysisReviewResult)
        or review.result.run_id != run.id
        or review.result.analysis_intent_id != intent.id
        or review.result.analysis_spec_id != spec.id
        or review.result.structured_result_sha256 != structured.result_sha256
        or review.result.input_dataset_content_hash != workflow.dataset_content_hash
        or structured.run_id != run.id
        or structured.analysis_intent_id != intent.id
        or structured.analysis_spec_id != spec.id
        or intent.analysis_spec_id != spec.id
        or intent.spec_sha256 != spec.spec_sha256
        or snapshot.pending_approvals
    ):
        return False
    source_job = session.get(JobRecord, observation.source_job_id)
    review_record = session.get(ReviewRecord, review.id)
    exact_artifact_ids = {artifact.id for artifact in run.artifacts}
    if (
        source_job is None
        or source_job.kind != "review-workflow"
        or source_job.workflow_id != workflow.id
        or source_job.status != "succeeded"
        or review_record is None
        or review_record.plan_id != plan_id
        or review_record.input_sha256 != source_job.input_sha256
        or set(observation.artifact_ids) != exact_artifact_ids
        or len(observation.artifact_ids) != len(exact_artifact_ids)
    ):
        return False
    approvals = list(
        session.scalars(
            select(ApprovalRecord).where(
                ApprovalRecord.workflow_id == workflow.id,
                ApprovalRecord.plan_id == plan_id,
                ApprovalRecord.task_id == intent.task_id,
                ApprovalRecord.subject_type == "analysis-intent",
                ApprovalRecord.subject_id == intent.id,
                ApprovalRecord.requested_action == "execute-python-data-analysis",
                ApprovalRecord.intent_hash == intent.payload_sha256,
                ApprovalRecord.user_decision == "approved",
            )
        )
    )
    waiting_confirmation = session.scalar(
        select(AgentDecisionRecord.id).where(
            AgentDecisionRecord.workflow_id == workflow.id,
            AgentDecisionRecord.status == "waiting-user-confirmation",
        )
    )
    return bool(
        len(approvals) == 1
        and approvals[0].payload_schema_version == "analysis-intent-v4"
        and approvals[0].decided_at is not None
        and waiting_confirmation is None
    )


def _literature_completion_bundle_matches(
    session: Session,
    workflow: WorkflowRecord,
    observation: StepObservation,
    *,
    plan_id: str | None,
) -> bool:
    if (
        workflow.creation_mode != "autonomous"
        or workflow.generation_mode != "local-deterministic"
        or workflow.status != "reviewing"
        or plan_id is None
        or observation.plan_id != plan_id
        or observation.review_id is None
        or observation.run_id is not None
        or observation.artifact_ids
    ):
        return False
    try:
        snapshot = workflow_snapshot(session, workflow)
    except (ValidationError, WorkflowConflict, ValueError):
        return False
    plan = snapshot.plan
    review = snapshot.latest_review
    result = snapshot.result
    if (
        plan is None
        or plan.id != plan_id
        or plan.status != "approved"
        or review is None
        or review.id != observation.review_id
        or review.review_type != "deterministic-claims-v2"
        or review.verdict != "passed"
        or not isinstance(review.result, DeterministicReviewResult)
        or review.result.schema_version != "2"
        or review.result.verdict != "passed"
        or result is None
        or result.integrity_status != "verified-frozen-v2"
        or not result.claims
        or any(claim.support_status != "supported" for claim in result.claims)
        or snapshot.pending_approvals
    ):
        return False
    source_job = session.get(JobRecord, observation.source_job_id)
    review_record = session.get(ReviewRecord, review.id)
    intent_decision = (
        session.get(IntentDecisionRecord, workflow.current_intent_decision_id)
        if workflow.current_intent_decision_id is not None
        else None
    )
    inspect_task = session.scalar(
        select(TaskRecord).where(
            TaskRecord.workflow_id == workflow.id,
            TaskRecord.plan_id == plan_id,
            TaskRecord.step_key == "inspect-sources",
        )
    )
    raw_inspected_source_ids = cast(
        object,
        inspect_task.outputs.get("sourceIds") if inspect_task is not None else None,
    )
    inspected_source_ids = (
        cast(list[str], raw_inspected_source_ids)
        if isinstance(raw_inspected_source_ids, list)
        and all(
            isinstance(source_id, str)
            for source_id in cast(list[object], raw_inspected_source_ids)
        )
        else None
    )
    descriptors = result_source_descriptors(session, workflow)
    descriptor_source_ids = [descriptor.source_id for descriptor in descriptors]
    if (
        inspected_source_ids is None
        or not descriptors
        or descriptor_source_ids != inspected_source_ids
        or len(descriptor_source_ids) != len(set(descriptor_source_ids))
    ):
        return False
    try:
        assert_result_sources_current(session, workflow, descriptors)
    except WorkflowConflict:
        return False
    waiting_confirmation = session.scalar(
        select(AgentDecisionRecord.id).where(
            AgentDecisionRecord.workflow_id == workflow.id,
            AgentDecisionRecord.status == "waiting-user-confirmation",
        )
    )
    pending_interaction = session.scalar(
        select(InteractionRequestRecord.id).where(
            InteractionRequestRecord.workflow_id == workflow.id,
            InteractionRequestRecord.status == "pending",
        )
    )
    return bool(
        source_job is not None
        and source_job.kind == "review-workflow"
        and source_job.workflow_id == workflow.id
        and source_job.status == "succeeded"
        and review_record is not None
        and review_record.plan_id == plan_id
        and review_record.review_type == "deterministic-claims-v2"
        and review_record.verdict == "passed"
        and review_record.input_sha256 == source_job.input_sha256
        and intent_decision is not None
        and intent_decision.workflow_id == workflow.id
        and intent_decision.intent == "literature-synthesis"
        and inspected_source_ids == intent_decision.selected_source_ids
        and len(inspected_source_ids) == len(set(inspected_source_ids))
        and set(inspected_source_ids).issubset(workflow.selected_source_ids)
        and waiting_confirmation is None
        and pending_interaction is None
    )


def _observation_from_record(record: StepObservationRecord) -> StepObservation:
    observation = StepObservation.model_validate(
        {
            "schemaVersion": record.schema_version,
            "workflowId": record.workflow_id,
            "planId": record.plan_id,
            "taskId": record.task_id,
            "sourceJobId": record.source_job_id,
            "runId": record.run_id,
            "reviewId": record.review_id,
            "observationType": record.observation_type,
            "stepKey": record.step_key,
            "attempt": record.attempt,
            "status": record.status,
            "facts": record.facts_json,
            "warnings": record.warnings_json,
            "unresolvedQuestions": record.unresolved_questions_json,
            "artifactIds": record.artifact_ids_json,
            "failureCategory": record.failure_category,
            "recommendedActions": record.recommended_actions_json,
        },
        strict=True,
    )
    if step_observation_sha256(observation) != record.output_sha256:
        raise WorkflowFailure(
            "agent-observation-integrity-failed",
            "The persisted Agent observation no longer matches its immutable hash.",
        )
    return observation


def _decision_from_record(record: AgentDecisionRecord) -> AgentDecision:
    decision = AgentDecision.model_validate_json(
        json.dumps(_decision_payload(record), allow_nan=False, ensure_ascii=False),
        strict=True,
    )
    if agent_decision_sha256(decision) != record.output_sha256:
        raise WorkflowFailure(
            "agent-decision-integrity-failed",
            "The persisted Agent decision no longer matches its immutable hash.",
        )
    return decision


def _decision_payload(record: AgentDecisionRecord) -> dict[str, Any]:
    return {
        "schemaVersion": record.schema_version,
        "action": record.action,
        "reasonCode": record.reason_code,
        "reason": record.reason,
        "targetStepKey": record.target_step_key,
        "clarificationRequests": record.clarification_requests_json,
        "proposedAnalysisSpec": record.proposed_analysis_spec_json,
        "analysisSpecDiff": record.analysis_spec_diff_json,
        "requiresUserConfirmation": record.requires_user_confirmation,
    }


def _decision_event(
    decision: AgentDecisionRecord,
    observation: StepObservationRecord,
    *,
    previous_analysis_spec_id: str | None = None,
    proposed_analysis_spec_id: str | None = None,
    research_context_snapshot_id: object | None = None,
    research_context_snapshot_sha256: object | None = None,
    discovery_selection: DiscoverySelectionProjection | None = None,
) -> AgentDecisionEventData:
    return AgentDecisionEventData(
        observation_id=observation.id,
        decision_id=decision.id,
        action=cast(Any, decision.action),
        task_id=observation.task_id,
        target_step_key=decision.target_step_key,
        previous_analysis_spec_id=(
            previous_analysis_spec_id
            or _fact_source_id(observation, "analysis-spec-used")
        ),
        proposed_analysis_spec_id=proposed_analysis_spec_id,
        expected_workflow_revision=decision.expected_workflow_revision,
        reason_code=decision.reason_code,
        research_context_snapshot_id=cast(str | None, research_context_snapshot_id),
        research_context_snapshot_sha256=cast(Any, research_context_snapshot_sha256),
        discovery_selection=discovery_selection,
        discovery_selection_sha256=(
            content_sha256(
                discovery_selection.model_dump(mode="json", by_alias=True)
            )
            if discovery_selection is not None
            else None
        ),
    )


def _persisted_discovery_selection(
    session: Session,
    workflow: WorkflowRecord,
    decision: AgentDecisionRecord,
) -> DiscoverySelectionProjection | None:
    matching_events = [
        event
        for event in session.scalars(
            select(EventRecord).where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.event_type == "agent.decision-proposed",
            )
        )
        if event.payload.get("decisionId") == decision.id
    ]
    if len(matching_events) != 1:
        raise WorkflowFailure(
            "agent-decision-provenance-invalid",
            "The Agent decision has no unique proposed-event provenance.",
        )
    try:
        event_data = AgentDecisionEventData.model_validate(
            matching_events[0].payload,
            strict=True,
        )
    except ValidationError as error:
        raise WorkflowFailure(
            "agent-decision-provenance-invalid",
            "The Agent decision proposed-event payload is invalid.",
        ) from error
    projection = event_data.discovery_selection
    projection_hash = event_data.discovery_selection_sha256
    if projection is None:
        if decision.action == "continue" and (
            decision.target_step_key or ""
        ).startswith("paper-discovery-"):
            raise WorkflowFailure(
                "discovery-selection-provenance-missing",
                "The Discovery continuation has no persisted selection projection.",
            )
        return None
    if (
        projection_hash
        != content_sha256(projection.model_dump(mode="json", by_alias=True))
        or projection.workflow_id != workflow.id
        or projection.selected_step_key != decision.target_step_key
    ):
        raise WorkflowFailure(
            "discovery-selection-provenance-invalid",
            "The persisted Discovery selection projection failed integrity checks.",
        )
    return projection


def _fact_source_id(record: StepObservationRecord, code: str) -> str | None:
    return next(
        (
            item.get("sourceId")
            for item in record.facts_json
            if item.get("code") == code and isinstance(item.get("sourceId"), str)
        ),
        None,
    )


def _task_operation_key(workflow: WorkflowRecord, task: TaskRecord) -> str:
    if task.task_type != "paper-discovery":
        return f"workflow:{workflow.id}:task:{task.id}"
    spec_id = task.inputs.get("discoverySpecId")
    query_id = task.inputs.get("queryId")
    provider = task.inputs.get("provider")
    if (
        not isinstance(spec_id, str)
        or not spec_id
        or not isinstance(query_id, str)
        or not query_id
        or provider not in {"arxiv", "crossref", "openalex", "pubmed"}
    ):
        raise WorkflowFailure(
            "discovery-task-input-invalid",
            "The approved paper-discovery task is malformed.",
        )
    return discovery_operation_key(
        spec_id,
        query_id,
        cast(DiscoveryProvider, provider),
    )


def _operation_subject(workflow_id: str, operation_key: str, operation: str) -> str:
    prefix = f"workflow:{workflow_id}:{operation}:"
    if not operation_key.startswith(prefix):
        raise WorkflowFailure("agent-operation-key-invalid", "The job identity is invalid.")
    value = operation_key.removeprefix(prefix)
    if not value or ":" in value:
        raise WorkflowFailure("agent-operation-key-invalid", "The job subject is invalid.")
    return value


def _observation_type(source_job: JobRecord, task: TaskRecord | None) -> str:
    if source_job.kind == "review-workflow":
        return "review"
    if task is None:
        return "pre-plan"
    return "analysis-execution" if task.step_key == "execute-analysis" else "step-output"


def _expected_review_type(workflow: WorkflowRecord) -> str:
    if workflow.workflow_type == "dataset-analysis":
        return "deterministic-analysis-v1"
    if workflow.workflow_type == "literature-synthesis":
        return "deterministic-claims-v2"
    raise WorkflowFailure(
        "agent-review-workflow-unsupported",
        "The workflow has no supported deterministic Agent review contract.",
    )


def _latest_plan_id(session: Session, workflow_id: str) -> str | None:
    return session.scalar(
        select(PlanRecord.id)
        .where(PlanRecord.workflow_id == workflow_id)
        .order_by(PlanRecord.version.desc())
    )


def _next_agent_plan_version(session: Session, workflow_id: str) -> int:
    latest = session.scalar(
        select(func.max(PlanRecord.version)).where(PlanRecord.workflow_id == workflow_id)
    )
    prefix = f"workflow:{workflow_id}:plan:"
    reserved = [
        int(suffix)
        for operation_key in session.scalars(
            select(JobRecord.operation_key).where(
                JobRecord.workflow_id == workflow_id,
                JobRecord.kind == "generate-plan",
            )
        )
        if operation_key.startswith(prefix)
        and (suffix := operation_key.removeprefix(prefix)).isdigit()
    ]
    return max([int(latest or 0), *reserved]) + 1


def _current_analysis_spec(session: Session, workflow_id: str) -> AnalysisSpecRecord | None:
    return session.scalar(
        select(AnalysisSpecRecord)
        .where(
            AnalysisSpecRecord.workflow_id == workflow_id,
            AnalysisSpecRecord.status.in_(["approved", "pending-approval"]),
        )
        .order_by(AnalysisSpecRecord.revision.desc())
    )


def _current_analysis_spec_id(session: Session, workflow_id: str) -> str | None:
    record = _current_analysis_spec(session, workflow_id)
    return record.id if record is not None else None


def _parse_analysis_spec(record: AnalysisSpecRecord | None) -> AnalysisSpec | None:
    if record is None:
        return None
    try:
        return AnalysisSpec.model_validate_json(
            json.dumps(record.spec_json, allow_nan=False, ensure_ascii=False),
            strict=True,
        )
    except ValidationError as error:
        raise WorkflowFailure(
            "analysis-spec-binding-invalid",
            "The stored AnalysisSpec does not satisfy its strict schema.",
        ) from error


def _latest_plan(session: Session, workflow_id: str) -> PlanRecord | None:
    return session.scalar(
        select(PlanRecord)
        .where(PlanRecord.workflow_id == workflow_id)
        .order_by(PlanRecord.version.desc())
    )


def _plan_summary(session: Session, workflow_id: str) -> dict[str, object] | None:
    plan = _latest_plan(session, workflow_id)
    if plan is None:
        return None
    steps = cast(object, plan.spec_json.get("steps"))
    return {
        "id": plan.id,
        "version": plan.version,
        "status": plan.status,
        "stepKeys": _plan_step_keys(steps),
    }


def _plan_step_keys(steps: object) -> list[str]:
    if not isinstance(steps, list):
        return []
    keys: list[str] = []
    for item in cast(list[object], steps):
        if not isinstance(item, dict):
            continue
        key = cast(dict[str, object], item).get("key")
        if isinstance(key, str):
            keys.append(key)
    return keys


def enqueue_agent_observation(
    session: Session,
    workflow: WorkflowRecord,
    source_job: JobRecord,
) -> JobRecord:
    supported = workflow.workflow_type == "dataset-analysis" or (
        workflow.workflow_type == "literature-synthesis"
        and workflow.generation_mode == "local-deterministic"
    )
    if workflow.creation_mode != "autonomous" or not supported:
        raise WorkflowConflict(
            "agent-loop-not-applicable",
            "The observation loop applies only to supported autonomous workflows.",
        )
    session.refresh(source_job)
    if source_job.status not in {"succeeded", "failed"}:
        raise WorkflowConflict(
            "agent-observation-source-incomplete",
            "The observation source must have a durable terminal job status.",
        )
    task = session.get(TaskRecord, source_job.task_id) if source_job.task_id else None
    return enqueue_job(
        session,
        workflow,
        kind="observe-step",
        task=task,
        operation_key=f"workflow:{workflow.id}:observe:{source_job.id}",
    )


__all__ = ("AgentLoopCoordinator", "enqueue_agent_observation")
