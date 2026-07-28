from __future__ import annotations

import asyncio
import hashlib
import json
import math
import uuid
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..model_gateway import OpenAICompatibleModelGateway
from ..models import (
    AgentContextSnapshotRecord,
    AgentDecisionRecord,
    AnalysisSpecRecord,
    ApprovalRecord,
    EventRecord,
    IntentDecisionRecord,
    InteractionRequestRecord,
    JobRecord,
    ModelInvocationRecord,
    PlanRecord,
    ProjectRecord,
    SourceRecord,
    StepObservationRecord,
    TaskRecord,
    UserResponseRecord,
    WorkflowRecord,
    utc_now,
)
from ._handlers.lifecycle import finish_job
from ._handlers.planning import assert_remote_gateway_matches_creation
from ._service.events import append_workflow_events, transition_workflow
from ._service.integrity import WorkflowConflict, canonical_json_bytes, content_sha256
from ._service.jobs import enqueue_job
from ._service.lifecycle import verified_dataset_for_workflow
from ._service.snapshots import workflow_snapshot
from .agent_loop.policy import (
    MAX_AGENT_STEPS,
    MAX_ANALYSIS_SPEC_REVISIONS,
    MAX_CLARIFICATION_ROUNDS,
    MAX_INVALID_MODEL_DECISIONS,
    MAX_MODEL_DECISIONS,
    MAX_PLAN_REVISIONS,
    MAX_STEP_RETRIES,
)
from .agent_loop.schemas import (
    AgentDecision,
    StepObservation,
    agent_decision_sha256,
    step_observation_sha256,
)
from .agent_schemas import (
    AgentAllowedAction,
    AgentDecisionOut,
    AgentDecisionResolveIn,
    AgentDecisionSummaryOut,
    AgentLoopLimitStateOut,
    AgentLoopLimitUsageOut,
    AgentRunCreateIn,
    AgentRunSnapshot,
    AgentStatusReasonOut,
    AgentWorkflowStateOut,
    IntentDecisionOut,
    InteractionRequestOut,
    InteractionRespondIn,
    ResolvedAgentWorkflowType,
    StepObservationOut,
    UserResponseOut,
)
from .research_memory import commit_user_response_memory
from .schemas import (
    AUTONOMOUS_REMOTE_DATA_CATEGORIES,
    AgentDecisionEventData,
    AgentRunCreatedEventData,
    AnalysisSpecEventData,
    CreatedEventData,
    GenerationMode,
    IntentDecisionEventData,
    InteractionAnsweredEventData,
    InteractionRequestedEventData,
    RemoteDataApprovalEventData,
    StatusChangedEventData,
    WorkflowStatus,
)
from .scientific_interactions import is_scientific_interaction
from .state import WorkflowFailure, workflow_transition_allowed


def agent_run_create_hash(payload: AgentRunCreateIn) -> str:
    return content_sha256(
        payload.model_dump(mode="json", by_alias=True, exclude_none=False)
    )


def start_agent_run(
    session: Session,
    project: ProjectRecord,
    payload: AgentRunCreateIn,
    idempotency_key: str,
    *,
    gateway: OpenAICompatibleModelGateway,
) -> WorkflowRecord:
    payload_hash = agent_run_create_hash(payload)
    existing = _agent_create_replay(
        session,
        project_id=project.id,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
    )
    if existing is not None:
        return existing
    _validated_sources(
        session,
        project_id=project.id,
        source_ids=payload.source_ids,
    )
    remote_router_approved = payload.remote_data_approved and gateway.configured
    generation_mode = (
        "remote-model-assisted" if remote_router_approved else "local-deterministic"
    )
    workflow = WorkflowRecord(
        id=str(uuid.uuid4()),
        project_id=project.id,
        create_idempotency_key=idempotency_key,
        create_payload_sha256=payload_hash,
        creation_mode="autonomous",
        selected_source_ids=list(payload.source_ids),
        current_intent_decision_id=None,
        workflow_type=None,
        dataset_source_id=None,
        dataset_content_hash=None,
        goal=payload.goal,
        generation_mode=generation_mode,
        status="routing",
        row_version=1,
        event_sequence=0,
    )
    session.add(workflow)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        replay = _agent_create_replay(
            session,
            project_id=project.id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
        if replay is not None:
            return replay
        raise
    route_job = enqueue_job(
        session,
        workflow,
        kind="route-intent",
        operation_key=f"workflow:{workflow.id}:intent-route:1",
    )
    events: list[tuple[str, Any, str | None, str | None]] = [
        (
            "agent-run.created",
            AgentRunCreatedEventData(
                goal_sha256=hashlib.sha256(workflow.goal.encode("utf-8")).hexdigest(),
                source_ids=list(workflow.selected_source_ids),
                mode="autonomous",
                generation_mode=generation_mode,
            ),
            None,
            route_job.id,
        )
    ]
    if remote_router_approved:
        events.append(
            (
                "remote-data.approved",
                RemoteDataApprovalEventData(
                    provider="openai-compatible",
                    endpoint_host=gateway.endpoint_host,
                    endpoint_identity=gateway.endpoint_identity,
                    model=gateway.default_model,
                    data_categories=list(AUTONOMOUS_REMOTE_DATA_CATEGORIES),
                ),
                None,
                None,
            )
        )
    append_workflow_events(session, workflow, events)
    session.commit()
    session.refresh(workflow)
    return workflow


def _agent_create_replay(
    session: Session,
    *,
    project_id: str,
    idempotency_key: str,
    payload_hash: str,
) -> WorkflowRecord | None:
    existing = session.scalar(
        select(WorkflowRecord).where(
            WorkflowRecord.project_id == project_id,
            WorkflowRecord.create_idempotency_key == idempotency_key,
        )
    )
    if existing is None:
        return None
    if existing.create_payload_sha256 != payload_hash:
        raise WorkflowConflict(
            "idempotency-key-reused",
            "This Idempotency-Key was already used with a different agent run request.",
        )
    if existing.creation_mode != "autonomous":
        raise WorkflowConflict(
            "idempotency-key-reused",
            "This Idempotency-Key belongs to a fixed workflow request.",
        )
    return existing


def _validated_sources(
    session: Session,
    *,
    project_id: str,
    source_ids: list[str],
) -> list[SourceRecord]:
    if not source_ids:
        return []
    records = list(
        session.scalars(select(SourceRecord).where(SourceRecord.id.in_(source_ids)))
    )
    by_id = {record.id: record for record in records}
    if set(by_id) != set(source_ids) or any(
        record.project_id != project_id for record in records
    ):
        raise WorkflowConflict(
            "source-not-found",
            "One or more selected sources do not exist in this project.",
        )
    if any(
        record.source_kind not in {"pdf", "dataset"}
        or record.ingestion_status != "ready"
        or len(record.content_hash) != 64
        for record in records
    ):
        raise WorkflowConflict(
            "source-not-ready",
            "Every selected source must be a ready, content-addressed PDF or CSV dataset.",
            retryable=True,
        )
    return [by_id[source_id] for source_id in source_ids]


def handle_route_intent(
    session: Session,
    workflow: WorkflowRecord,
    job: JobRecord,
    *,
    gateway: OpenAICompatibleModelGateway,
) -> None:
    expected_lease_token = job.lease_token
    if expected_lease_token is None:
        raise WorkflowFailure(
            "job-lease-lost",
            "The background job lease is no longer valid.",
            retryable=True,
        )
    _assert_route_job_lease(job, expected_lease_token)
    if workflow.creation_mode != "autonomous" or workflow.status != "routing":
        raise WorkflowFailure(
            "agent-run-not-routing",
            "The autonomous workflow is no longer waiting for intent routing.",
        )
    sources = _validated_sources(
        session,
        project_id=workflow.project_id,
        source_ids=list(workflow.selected_source_ids),
    )
    answered_context = _answered_context(session, workflow.id)
    invocation = _model_invocation_for_job(
        session,
        workflow,
        job,
    )
    decision_record: IntentDecisionRecord | None = None
    if invocation is not None:
        _assert_invocation_matches_remote_approval(session, workflow, invocation)
        decision_record = _decision_for_model_invocation(session, workflow, invocation)
        if decision_record is None and invocation.status == "pending":
            result = _recover_pending_model_result(
                workflow,
                sources,
                answered_context,
                invocation,
            )
            decision_record = _finalize_model_invocation(
                session,
                workflow,
                invocation,
                result,
            )
            session.commit()
            workflow, job, decision_record = _reload_routing_publication_state(
                session,
                workflow.id,
                job.id,
                decision_record.id,
                expected_lease_token,
            )
        elif decision_record is None:
            raise WorkflowFailure(
                "intent-invocation-incomplete",
                "A remote routing call was recorded without a validated decision; it will not be repeated.",
            )
    if decision_record is None:
        remote_call_expected = (
            workflow.generation_mode == "remote-model-assisted"
            and _remote_router_will_call_model(workflow.goal)
        )
        if remote_call_expected:
            assert_remote_gateway_matches_creation(session, workflow, gateway)
            invocation = _begin_model_invocation(
                session,
                workflow,
                job,
                sources,
                answered_context,
                gateway,
            )
            session.commit()
            session.expire_all()
            reloaded_workflow = session.get(WorkflowRecord, workflow.id)
            reloaded_job = session.get(JobRecord, job.id)
            reloaded_invocation = session.get(ModelInvocationRecord, invocation.id)
            if (
                reloaded_workflow is None
                or reloaded_job is None
                or reloaded_invocation is None
            ):
                raise WorkflowFailure(
                    "intent-invocation-persistence-failed",
                    "The durable remote routing request record could not be reloaded.",
                )
            workflow = reloaded_workflow
            job = reloaded_job
            invocation = reloaded_invocation
            _assert_route_job_lease(job, expected_lease_token)
            if workflow.cancel_requested_at is not None:
                _fail_pending_model_invocation(
                    session,
                    invocation,
                    code="model-request-cancelled-before-send",
                    message=(
                        "The workflow was cancelled before the remote routing request began."
                    ),
                )
                session.commit()
                raise WorkflowFailure(
                    "workflow-cancelled-during-job",
                    "The workflow was cancelled before the remote routing request began.",
                )
        result = _route_intent_sync(
            workflow.goal,
            sources,
            answered_context,
            gateway=(
                gateway
                if workflow.generation_mode == "remote-model-assisted"
                else None
            ),
        )
        decision = result.decision
        if not set(decision.selected_source_ids).issubset(
            workflow.selected_source_ids
        ):
            if invocation is not None:
                _fail_pending_model_invocation(
                    session,
                    invocation,
                    code="intent-source-authorization-failed",
                    message=(
                        "The model result selected a source outside the authorized run inputs."
                    ),
                )
                session.commit()
            raise WorkflowFailure(
                "intent-source-authorization-failed",
                "The intent router selected a source outside the authorized run inputs.",
            )
        if result.used_model:
            if invocation is None:
                raise WorkflowFailure(
                    "intent-invocation-missing",
                    "The remote routing result has no durable request record.",
                )
            decision_record = _finalize_model_invocation(
                session,
                workflow,
                invocation,
                result,
            )
            session.commit()
            workflow, job, decision_record = _reload_routing_publication_state(
                session,
                workflow.id,
                job.id,
                decision_record.id,
                expected_lease_token,
            )
        else:
            decision_record = _new_intent_decision(
                session,
                workflow,
                result,
                invocation=None,
            )
    if workflow.cancel_requested_at is not None:
        raise WorkflowFailure(
            "workflow-cancelled-during-job",
            "The workflow was cancelled before the routing result could be published.",
        )
    if workflow.status != "routing":
        raise WorkflowFailure(
            "agent-run-not-routing",
            "The autonomous workflow changed before the routing result could be published.",
        )
    finish_job(session, job, "succeeded")
    append_workflow_events(
        session,
        workflow,
        [
            (
                "intent.decision-recorded",
                IntentDecisionEventData(
                    intent_decision_id=decision_record.id,
                    intent=cast(Any, decision_record.intent),
                    confidence=decision_record.confidence,
                    output_sha256=decision_record.output_sha256,
                ),
                None,
                job.id,
            )
        ],
    )
    selected = [
        source for source in sources if source.id in decision_record.selected_source_ids
    ]
    if decision_record.intent == "literature-synthesis":
        _resolve_agent_workflow(
            session,
            workflow,
            workflow_type="literature-synthesis",
            decision_id=decision_record.id,
            dataset=None,
            generation_mode=workflow.generation_mode,
        )
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "workflow.created",
                    CreatedEventData(
                        workflow_type="literature-synthesis",
                        goal_sha256=hashlib.sha256(
                            workflow.goal.encode("utf-8")
                        ).hexdigest(),
                        generation_mode=cast(GenerationMode, workflow.generation_mode),
                    ),
                    None,
                    job.id,
                )
            ],
        )
        enqueue_job(
            session,
            workflow,
            kind="generate-plan",
            operation_key=f"workflow:{workflow.id}:plan:{_next_plan_version(session, workflow)}",
        )
    elif decision_record.intent == "dataset-analysis":
        if len(selected) != 1:
            raise WorkflowFailure(
                "intent-dataset-selection-invalid",
                "Dataset analysis requires exactly one authorized ready dataset.",
            )
        project = session.get(ProjectRecord, workflow.project_id)
        if project is None:
            raise WorkflowFailure(
                "project-missing",
                "The autonomous workflow project is missing.",
            )
        dataset = verified_dataset_for_workflow(session, project, selected[0].id)
        _resolve_agent_workflow(
            session,
            workflow,
            workflow_type="dataset-analysis",
            decision_id=decision_record.id,
            dataset=dataset,
            generation_mode=workflow.generation_mode,
        )
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "workflow.created",
                    CreatedEventData(
                        workflow_type="dataset-analysis",
                        goal_sha256=hashlib.sha256(
                            workflow.goal.encode("utf-8")
                        ).hexdigest(),
                        generation_mode=cast(GenerationMode, workflow.generation_mode),
                    ),
                    None,
                    job.id,
                )
            ],
        )
        enqueue_job(
            session,
            workflow,
            kind="generate-plan",
            operation_key=f"workflow:{workflow.id}:plan:{_next_plan_version(session, workflow)}",
        )
    elif decision_record.intent == "clarification-required":
        workflow.current_intent_decision_id = decision_record.id
        transition_workflow(session, workflow, "waiting-clarification")
        _create_intent_interaction(
            session,
            workflow,
            decision_record,
            authorized_sources=sources,
        )
    else:
        workflow.current_intent_decision_id = decision_record.id
        code = "research-capability-unsupported"
        workflow.last_error_code = code
        workflow.last_error_message = (
            "This research goal is outside the registered autonomous capabilities."
        )
        transition_workflow(session, workflow, "unsupported", reason_code=code)


def _route_intent_sync(
    goal: str,
    sources: list[SourceRecord],
    answered_context: list[dict[str, object]],
    *,
    gateway: OpenAICompatibleModelGateway | None,
) -> Any:
    from .intent_router import IntentSource, SourceKind, route_intent

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise WorkflowFailure(
            "model-bridge-context-invalid",
            "Intent routing cannot run inside an existing event loop.",
            retryable=True,
        )
    return asyncio.run(
        route_intent(
            goal,
            [
                IntentSource(
                    id=source.id,
                    source_kind=cast(SourceKind, source.source_kind),
                    ingestion_status=source.ingestion_status,
                )
                for source in sources
            ],
            gateway=gateway,
            answered_context=answered_context,
        )
    )


def _remote_router_will_call_model(goal: str) -> bool:
    from .intent_router import unsupported_capabilities

    return not unsupported_capabilities(goal.strip())


def _intent_router_sources(sources: list[SourceRecord]) -> list[Any]:
    from .intent_router import IntentSource, SourceKind

    return [
        IntentSource(
            id=source.id,
            source_kind=cast(SourceKind, source.source_kind),
            ingestion_status=source.ingestion_status,
        )
        for source in sources
    ]


def _begin_model_invocation(
    session: Session,
    workflow: WorkflowRecord,
    job: JobRecord,
    sources: list[SourceRecord],
    answered_context: list[dict[str, object]],
    gateway: OpenAICompatibleModelGateway,
) -> ModelInvocationRecord:
    from .intent_router import INTENT_ROUTER_PROMPT_VERSION, intent_router_input_sha256

    model = gateway.default_model
    if not model:
        raise WorkflowFailure(
            "remote-gateway-model-missing",
            "The approved remote routing destination has no configured model.",
        )
    input_sha256 = intent_router_input_sha256(
        workflow.goal.strip(),
        _intent_router_sources(sources),
        answered_context,
        model=model,
    )
    record = ModelInvocationRecord(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        schema_version="1",
        operation_type="intent-router",
        operation_key=job.operation_key,
        attempt=job.attempt,
        generator="openai-compatible",
        model=model,
        endpoint_identity=gateway.endpoint_identity,
        prompt_version=INTENT_ROUTER_PROMPT_VERSION,
        input_sha256=input_sha256,
        output_sha256=None,
        token_usage={},
        validation_errors=[],
        request_idempotency_key=(
            "intent-router:"
            + content_sha256(
                {
                    "inputSha256": input_sha256,
                    "operationKey": job.operation_key,
                }
            )
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


def _recover_pending_model_result(
    workflow: WorkflowRecord,
    sources: list[SourceRecord],
    answered_context: list[dict[str, object]],
    invocation: ModelInvocationRecord,
) -> Any:
    from .intent_router import recover_unknown_model_request

    return recover_unknown_model_request(
        workflow.goal,
        _intent_router_sources(sources),
        answered_context,
        model=invocation.model,
        endpoint_identity=invocation.endpoint_identity,
    )


def _finalize_model_invocation(
    session: Session,
    workflow: WorkflowRecord,
    invocation: ModelInvocationRecord,
    result: Any,
) -> IntentDecisionRecord:
    if (
        not result.used_model
        or result.input_sha256 != invocation.input_sha256
        or result.model_used != invocation.model
        or result.endpoint_identity != invocation.endpoint_identity
    ):
        raise WorkflowFailure(
            "intent-invocation-result-mismatch",
            "The remote routing result does not match its durable request record.",
        )
    failed = result.parse_result in {
        "model-request-failed",
        "model-request-outcome-unknown",
        "model-output-invalid",
    }
    finalized = session.execute(
        update(ModelInvocationRecord)
        .where(
            ModelInvocationRecord.id == invocation.id,
            ModelInvocationRecord.workflow_id == workflow.id,
            ModelInvocationRecord.status == "pending",
        )
        .values(
            output_sha256=result.model_output_sha256,
            token_usage=result.token_usage,
            validation_errors=[
                {"code": code} for code in result.validation_errors
            ],
            status="failed" if failed else "succeeded",
            error_code=result.parse_result if failed else None,
            error_message=(
                "The remote request was unavailable, invalid, or had an unknown outcome; "
                "a safe deterministic fallback was used."
                if failed
                else None
            ),
            finished_at=utc_now(),
        )
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[object], finalized).rowcount != 1:
        session.expire_all()
        persisted = session.get(ModelInvocationRecord, invocation.id)
        if persisted is not None:
            existing = _decision_for_model_invocation(session, workflow, persisted)
            if existing is not None:
                return existing
        raise WorkflowFailure(
            "intent-invocation-finalize-conflict",
            "The durable remote routing request was finalized concurrently without a decision.",
        )
    return _new_intent_decision(
        session,
        workflow,
        result,
        invocation=invocation,
    )


def _fail_pending_model_invocation(
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


def _new_intent_decision(
    session: Session,
    workflow: WorkflowRecord,
    result: Any,
    *,
    invocation: ModelInvocationRecord | None,
) -> IntentDecisionRecord:
    decision = result.decision
    decision_revision = (
        session.scalar(
            select(IntentDecisionRecord.revision)
            .where(IntentDecisionRecord.workflow_id == workflow.id)
            .order_by(IntentDecisionRecord.revision.desc())
        )
        or 0
    ) + 1
    record = IntentDecisionRecord(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        revision=decision_revision,
        intent=decision.intent,
        confidence=decision.confidence,
        reasoning_summary=decision.reasoning_summary,
        selected_source_ids=list(decision.selected_source_ids),
        missing_inputs=list(decision.missing_inputs),
        proposed_workflow_type=decision.proposed_workflow_type,
        generator=(
            "model-assisted-intent-router-v1"
            if result.used_model
            else "deterministic-intent-router-v1"
        ),
        used_model=result.used_model,
        model=result.model_used,
        prompt_version=result.prompt_version,
        parse_result=result.parse_result,
        model_invocation_id=invocation.id if invocation is not None else None,
        input_sha256=result.input_sha256,
        output_sha256=result.output_sha256,
    )
    session.add(record)
    session.flush()
    return record


def _model_invocation_for_job(
    session: Session,
    workflow: WorkflowRecord,
    job: JobRecord,
) -> ModelInvocationRecord | None:
    invocations = list(
        session.scalars(
        select(ModelInvocationRecord)
        .where(
            ModelInvocationRecord.workflow_id == workflow.id,
            ModelInvocationRecord.operation_key == job.operation_key,
        )
        .order_by(ModelInvocationRecord.attempt.desc())
        )
    )
    if not invocations:
        return None
    if len(invocations) != 1:
        raise WorkflowFailure(
            "intent-invocation-identity-conflict",
            "More than one remote request record exists for this logical routing operation.",
        )
    return invocations[0]


def _decision_for_model_invocation(
    session: Session,
    workflow: WorkflowRecord,
    invocation: ModelInvocationRecord,
) -> IntentDecisionRecord | None:
    decision = session.scalar(
        select(IntentDecisionRecord).where(
            IntentDecisionRecord.model_invocation_id == invocation.id
        )
    )
    if decision is not None:
        _assert_intent_provenance_binding(workflow, invocation, decision)
    return decision


def _assert_intent_provenance_binding(
    workflow: WorkflowRecord,
    invocation: ModelInvocationRecord,
    decision: IntentDecisionRecord,
) -> None:
    _assert_intent_decision_output_integrity(decision)
    failure_results = {
        "model-request-failed",
        "model-request-outcome-unknown",
        "model-output-invalid",
    }
    expected_status = "succeeded" if decision.parse_result == "valid" else "failed"
    if (
        invocation.workflow_id != workflow.id
        or decision.workflow_id != workflow.id
        or decision.model_invocation_id != invocation.id
        or not decision.used_model
        or invocation.operation_type != "intent-router"
        or decision.parse_result not in {"valid", *failure_results}
        or invocation.status != expected_status
        or (
            decision.parse_result in failure_results
            and invocation.error_code != decision.parse_result
        )
        or decision.model != invocation.model
        or decision.prompt_version != invocation.prompt_version
        or decision.input_sha256 != invocation.input_sha256
        or invocation.request_payload_sha256 != invocation.input_sha256
    ):
        raise WorkflowFailure(
            "intent-provenance-binding-invalid",
            "The stored routing decision is not bound to its terminal model request.",
        )


def _assert_intent_decision_output_integrity(
    decision: IntentDecisionRecord,
) -> None:
    from .intent_router import IntentDecision, intent_decision_sha256

    try:
        canonical_decision = IntentDecision(
            intent=cast(Any, decision.intent),
            confidence=decision.confidence,
            reasoning_summary=decision.reasoning_summary,
            selected_source_ids=list(decision.selected_source_ids),
            missing_inputs=list(decision.missing_inputs),
            proposed_workflow_type=cast(Any, decision.proposed_workflow_type),
        )
    except (TypeError, ValueError) as error:
        raise WorkflowFailure(
            "intent-provenance-binding-invalid",
            "The stored routing decision does not satisfy its canonical schema.",
        ) from error
    if intent_decision_sha256(canonical_decision) != decision.output_sha256:
        raise WorkflowFailure(
            "intent-provenance-binding-invalid",
            "The stored routing decision does not match its canonical output hash.",
        )


def _assert_invocation_matches_remote_approval(
    session: Session,
    workflow: WorkflowRecord,
    invocation: ModelInvocationRecord,
) -> None:
    approval_event = session.scalar(
        select(EventRecord)
        .where(
            EventRecord.workflow_id == workflow.id,
            EventRecord.event_type == "remote-data.approved",
        )
        .order_by(EventRecord.sequence)
    )
    payload = approval_event.payload if approval_event is not None else {}
    if (
        invocation.workflow_id != workflow.id
        or payload.get("provider") != "openai-compatible"
        or payload.get("endpointIdentity") != invocation.endpoint_identity
        or payload.get("model") != invocation.model
        or payload.get("dataCategories")
        != list(AUTONOMOUS_REMOTE_DATA_CATEGORIES)
    ):
        raise WorkflowFailure(
            "intent-invocation-approval-mismatch",
            "The stored remote routing request does not match its durable data approval.",
        )


def _reload_routing_publication_state(
    session: Session,
    workflow_id: str,
    job_id: str,
    decision_id: str,
    expected_lease_token: str,
) -> tuple[WorkflowRecord, JobRecord, IntentDecisionRecord]:
    session.expire_all()
    workflow = session.get(WorkflowRecord, workflow_id)
    job = session.get(JobRecord, job_id)
    decision = session.get(IntentDecisionRecord, decision_id)
    if workflow is None or job is None or decision is None:
        raise WorkflowFailure(
            "intent-publication-state-missing",
            "The durable routing result could not be reloaded for publication.",
        )
    _assert_route_job_lease(job, expected_lease_token)
    if decision.workflow_id != workflow.id:
        raise WorkflowFailure(
            "intent-provenance-binding-invalid",
            "The stored routing decision is not bound to this workflow.",
        )
    return workflow, job, decision


def _assert_route_job_lease(
    job: JobRecord,
    expected_lease_token: str | None,
) -> None:
    if (
        expected_lease_token is None
        or job.status != "leased"
        or job.lease_token != expected_lease_token
    ):
        raise WorkflowFailure(
            "job-lease-lost",
            "The background job lease is no longer valid.",
            retryable=True,
        )


def _next_plan_version(session: Session, workflow: WorkflowRecord) -> int:
    latest_version = session.scalar(
        select(PlanRecord.version)
        .where(PlanRecord.workflow_id == workflow.id)
        .order_by(PlanRecord.version.desc())
    )
    prefix = f"workflow:{workflow.id}:plan:"
    reserved_versions = [
        int(suffix)
        for operation_key in session.scalars(
            select(JobRecord.operation_key).where(
                JobRecord.workflow_id == workflow.id,
                JobRecord.kind == "generate-plan",
            )
        )
        if (suffix := operation_key.removeprefix(prefix)).isdigit()
        and operation_key.startswith(prefix)
    ]
    return max([latest_version or 0, *reserved_versions]) + 1


def _resolve_agent_workflow(
    session: Session,
    workflow: WorkflowRecord,
    *,
    workflow_type: str,
    decision_id: str,
    dataset: SourceRecord | None,
    generation_mode: str,
) -> None:
    current = workflow.status
    if current != "routing" or not workflow_transition_allowed(current, "planning"):
        raise WorkflowConflict(
            "invalid-workflow-transition",
            f"Workflow cannot move from {current} to planning.",
        )
    now = utc_now()
    result = session.execute(
        update(WorkflowRecord)
        .where(
            WorkflowRecord.id == workflow.id,
            WorkflowRecord.row_version == workflow.row_version,
            WorkflowRecord.status == current,
            WorkflowRecord.workflow_type.is_(None),
        )
        .values(
            workflow_type=workflow_type,
            current_intent_decision_id=decision_id,
            dataset_source_id=dataset.id if dataset is not None else None,
            dataset_content_hash=(
                dataset.content_hash if dataset is not None else None
            ),
            generation_mode=generation_mode,
            status="planning",
            row_version=WorkflowRecord.row_version + 1,
            updated_at=now,
            finished_at=None,
            blocking_code=None,
            blocking_message=None,
            last_error_code=None,
            last_error_message=None,
        )
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[object], result).rowcount != 1:
        session.expire_all()
        raise WorkflowConflict(
            "workflow-revision-conflict",
            "The workflow changed before intent resolution was applied.",
            retryable=True,
        )
    session.flush()
    session.refresh(workflow)
    append_workflow_events(
        session,
        workflow,
        [
            (
                "workflow.status-changed",
                StatusChangedEventData(
                    previous_status="routing",
                    status="planning",
                    reason_code=None,
                ),
                None,
                None,
            )
        ],
    )


def _answered_context(session: Session, workflow_id: str) -> list[dict[str, object]]:
    interactions = list(
        session.scalars(
            select(InteractionRequestRecord)
            .where(InteractionRequestRecord.workflow_id == workflow_id)
            .order_by(InteractionRequestRecord.created_at, InteractionRequestRecord.id)
        )
    )
    context: list[dict[str, object]] = []
    for interaction in interactions:
        response = session.scalar(
            select(UserResponseRecord)
            .where(UserResponseRecord.interaction_id == interaction.id)
            .order_by(UserResponseRecord.revision.desc())
        )
        if response is None:
            continue
        context.append(
            {
                "interactionId": interaction.id,
                "requestType": interaction.request_type,
                "options": list(interaction.options),
                "response": response.response_json,
            }
        )
    return context


def _create_intent_interaction(
    session: Session,
    workflow: WorkflowRecord,
    decision: IntentDecisionRecord,
    *,
    authorized_sources: list[SourceRecord],
) -> InteractionRequestRecord:
    session.execute(
        update(InteractionRequestRecord)
        .where(
            InteractionRequestRecord.workflow_id == workflow.id,
            InteractionRequestRecord.status.in_(["pending", "answered"]),
        )
        .values(status="superseded")
    )
    request_revision = (
        session.scalar(
            select(InteractionRequestRecord.revision)
            .where(
                InteractionRequestRecord.workflow_id == workflow.id,
                InteractionRequestRecord.request_key == "intent-clarification",
            )
            .order_by(InteractionRequestRecord.revision.desc())
        )
        or 0
    ) + 1
    missing_inputs = set(decision.missing_inputs)
    dataset_sources = [
        source for source in authorized_sources if source.source_kind == "dataset"
    ]
    if (
        "select-exactly-one-ready-dataset" in missing_inputs
        and len(dataset_sources) > 1
    ):
        request_type = "single-choice"
        question = "Which one of the selected datasets should this run analyze?"
        options = [
            {
                "value": source.id,
                "label": source.title,
                "sourceKind": source.source_kind,
            }
            for source in dataset_sources
        ]
        response_schema: dict[str, Any] = {
            "type": "string",
            "enum": [source.id for source in dataset_sources],
            "semantic": "source-id",
            "generator": "clarification-generator-v1",
        }
    else:
        available_kinds = {source.source_kind for source in authorized_sources}
        options: list[dict[str, Any]] = []
        if "pdf" in available_kinds:
            options.append(
                {"value": "literature-synthesis", "label": "Literature research"}
            )
        if "dataset" in available_kinds:
            options.append(
                {"value": "dataset-analysis", "label": "Dataset analysis"}
            )
        if options:
            request_type = "single-choice"
            question = (
                "Which single supported research path should this run use first?"
                if {"pdf", "dataset"}.issubset(available_kinds)
                else "Which supported research path best matches your goal?"
            )
            response_schema = {
                "type": "string",
                "enum": [option["value"] for option in options],
                "generator": "clarification-generator-v1",
            }
        else:
            request_type = "text"
            question = (
                "Add at least one ready PDF or CSV source, then describe the intended "
                "research path in a new autonomous run."
            )
            response_schema = {
                "type": "string",
                "minLength": 1,
                "generator": "clarification-generator-v1",
            }
    request_payload = {
        "decisionId": decision.id,
        "missingInputs": decision.missing_inputs,
        "options": options,
        "question": question,
        "requestType": request_type,
        "responseSchema": response_schema,
        "workflowRevision": workflow.row_version,
    }
    record = InteractionRequestRecord(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        step_id=None,
        request_key="intent-clarification",
        revision=request_revision,
        workflow_revision=workflow.row_version,
        request_type=request_type,
        question=question,
        options=options,
        required=True,
        status="pending",
        response_schema=response_schema,
        request_sha256=content_sha256(request_payload),
    )
    session.add(record)
    session.flush()
    append_workflow_events(
        session,
        workflow,
        [
            (
                "interaction.requested",
                InteractionRequestedEventData(
                    interaction_id=record.id,
                    request_type=cast(Any, record.request_type),
                    required=True,
                    expected_workflow_revision=workflow.row_version,
                ),
                None,
                None,
            )
        ],
    )
    return record


def respond_to_interaction(
    session: Session,
    interaction: InteractionRequestRecord,
    payload: InteractionRespondIn,
    idempotency_key: str,
) -> WorkflowRecord:
    request_payload_sha256 = content_sha256(
        {
            "expectedWorkflowRevision": payload.expected_workflow_revision,
            "interactionId": interaction.id,
            "response": payload.response,
        }
    )
    replay = session.scalar(
        select(UserResponseRecord).where(
            UserResponseRecord.idempotency_key == idempotency_key
        )
    )
    if replay is not None:
        if (
            replay.interaction_id != interaction.id
            or replay.request_payload_sha256 != request_payload_sha256
        ):
            raise WorkflowConflict(
                "idempotency-key-reused",
                "This Idempotency-Key was already used with a different response.",
            )
        workflow = session.get(WorkflowRecord, interaction.workflow_id)
        if workflow is None:
            raise WorkflowConflict("workflow-missing", "The interaction workflow is missing.")
        return workflow
    workflow = session.get(WorkflowRecord, interaction.workflow_id)
    if workflow is None or workflow.creation_mode != "autonomous":
        raise WorkflowConflict(
            "interaction-workflow-invalid",
            "The interaction does not belong to an autonomous workflow.",
        )
    if workflow.row_version != payload.expected_workflow_revision:
        raise WorkflowConflict(
            "workflow-revision-conflict",
            "The workflow changed before this response was submitted. Reload and try again.",
            retryable=True,
        )
    latest_interaction = session.scalar(
        select(InteractionRequestRecord)
        .where(
            InteractionRequestRecord.workflow_id == workflow.id,
            InteractionRequestRecord.status.in_(["pending", "answered"]),
        )
        .order_by(
            InteractionRequestRecord.created_at.desc(),
            InteractionRequestRecord.id.desc(),
        )
    )
    if latest_interaction is None or latest_interaction.id != interaction.id:
        raise WorkflowConflict(
            "interaction-superseded",
            "A newer clarification request has replaced this interaction.",
        )
    if interaction.status not in {"pending", "answered"}:
        raise WorkflowConflict(
            "interaction-not-answerable",
            "This clarification request is no longer answerable.",
        )
    if workflow.status not in {
        "waiting-clarification",
        "planning",
        "waiting-plan-approval",
    }:
        raise WorkflowConflict(
            "interaction-not-answerable",
            "Clarification responses cannot change a workflow after execution begins.",
        )
    response = _validated_interaction_response(interaction, payload.response)
    scientific_interaction = is_scientific_interaction(interaction)
    if scientific_interaction and workflow.workflow_type != "dataset-analysis":
        raise WorkflowConflict(
            "interaction-workflow-invalid",
            "Scientific method clarification requires a resolved dataset workflow.",
        )
    selected_source_ids = list(workflow.selected_source_ids)
    response_semantic = interaction.response_schema.get("semantic")
    if response_semantic == "source-ids":
        selected_source_ids = cast(list[str], response)
        _validated_sources(
            session,
            project_id=workflow.project_id,
            source_ids=selected_source_ids,
        )
    elif response_semantic == "source-id":
        selected_source_ids = [cast(str, response)]
        _validated_sources(
            session,
            project_id=workflow.project_id,
            source_ids=selected_source_ids,
        )
    response_sha256 = content_sha256(response)
    response_revision = (
        session.scalar(
            select(UserResponseRecord.revision)
            .where(UserResponseRecord.interaction_id == interaction.id)
            .order_by(UserResponseRecord.revision.desc())
        )
        or 0
    ) + 1
    if scientific_interaction:
        _supersede_pending_plan(session, workflow)
        _advance_scientific_planning_revision(
            session,
            workflow,
            expected_revision=payload.expected_workflow_revision,
        )
    elif workflow.status in {"planning", "waiting-plan-approval"}:
        _supersede_pending_plan(session, workflow)
        _reset_agent_workflow_to_routing(
            session,
            workflow,
            selected_source_ids=selected_source_ids,
        )
    else:
        if selected_source_ids != workflow.selected_source_ids:
            workflow.selected_source_ids = selected_source_ids
        workflow.current_intent_decision_id = None
        transition_workflow(
            session,
            workflow,
            "routing",
            expected_revision=payload.expected_workflow_revision,
        )
    response_record = UserResponseRecord(
        id=str(uuid.uuid4()),
        interaction_id=interaction.id,
        revision=response_revision,
        expected_workflow_revision=payload.expected_workflow_revision,
        response_json=response,
        response_sha256=response_sha256,
        idempotency_key=idempotency_key,
        request_payload_sha256=request_payload_sha256,
    )
    session.add(response_record)
    interaction.status = "answered"
    interaction.answered_at = utc_now()
    session.flush()
    commit_user_response_memory(session, workflow, response_record)
    append_workflow_events(
        session,
        workflow,
        [
            (
                "interaction.answered",
                InteractionAnsweredEventData(
                    interaction_id=interaction.id,
                    request_type=cast(Any, interaction.request_type),
                    required=interaction.required,
                    response_id=response_record.id,
                    response_revision=response_record.revision,
                    expected_workflow_revision=payload.expected_workflow_revision,
                ),
                None,
                None,
            )
        ],
    )
    if scientific_interaction:
        enqueue_job(
            session,
            workflow,
            kind="generate-plan",
            operation_key=(
                f"workflow:{workflow.id}:plan:{_next_plan_version(session, workflow)}"
            ),
            request_idempotency_key=idempotency_key,
            request_payload_sha256=request_payload_sha256,
        )
    else:
        enqueue_job(
            session,
            workflow,
            kind="route-intent",
            operation_key=f"workflow:{workflow.id}:intent-response:{response_record.id}",
            request_idempotency_key=idempotency_key,
            request_payload_sha256=request_payload_sha256,
        )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        replay = session.scalar(
            select(UserResponseRecord).where(
                UserResponseRecord.idempotency_key == idempotency_key
            )
        )
        if (
            replay is not None
            and replay.interaction_id == interaction.id
            and replay.request_payload_sha256 == request_payload_sha256
        ):
            persisted = session.get(WorkflowRecord, interaction.workflow_id)
            if persisted is not None:
                return persisted
        raise
    session.refresh(workflow)
    return workflow


def resolve_agent_decision(
    session: Session,
    workflow: WorkflowRecord,
    decision: AgentDecisionRecord,
    payload: AgentDecisionResolveIn,
) -> WorkflowRecord:
    if (
        workflow.creation_mode != "autonomous"
        or decision.workflow_id != workflow.id
        or decision.action != "revise-analysis-spec"
        or not decision.requires_user_confirmation
    ):
        raise WorkflowConflict(
            "agent-decision-resolution-invalid",
            "Only a pending scientific revision for this autonomous workflow can be resolved.",
        )
    _agent_decision_out(session, workflow, decision)
    observation = session.get(StepObservationRecord, decision.observation_id)
    if observation is None or observation.workflow_id != workflow.id:
        raise WorkflowConflict(
            "agent-decision-integrity-failed",
            "The scientific revision is missing its verified observation.",
        )
    _step_observation_out(observation)
    if (
        payload.decision_output_sha256 != decision.output_sha256
        or payload.expected_workflow_revision != decision.expected_workflow_revision
    ):
        raise WorkflowConflict(
            "agent-decision-resolution-mismatch",
            "The submitted decision does not match the displayed scientific revision.",
        )
    if payload.decision == "approved" and decision.status in {"proposed", "applied"}:
        return workflow
    if payload.decision == "rejected" and decision.status == "rejected":
        return workflow
    if decision.status != "waiting-user-confirmation":
        raise WorkflowConflict(
            "agent-decision-already-resolved",
            "The scientific revision was already resolved with a different decision.",
        )
    if workflow.row_version != payload.expected_workflow_revision:
        raise WorkflowConflict(
            "workflow-revision-conflict",
            "The workflow changed before the scientific revision was resolved. Reload and try again.",
            retryable=True,
        )
    target_status = "proposed" if payload.decision == "approved" else "rejected"
    changed = session.execute(
        update(AgentDecisionRecord)
        .where(
            AgentDecisionRecord.id == decision.id,
            AgentDecisionRecord.workflow_id == workflow.id,
            AgentDecisionRecord.status == "waiting-user-confirmation",
            AgentDecisionRecord.output_sha256 == payload.decision_output_sha256,
            AgentDecisionRecord.expected_workflow_revision
            == payload.expected_workflow_revision,
        )
        .values(status=target_status)
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[object], changed).rowcount != 1:
        session.expire_all()
        persisted = session.get(AgentDecisionRecord, decision.id)
        persisted_workflow = session.get(WorkflowRecord, workflow.id)
        if (
            persisted is not None
            and persisted_workflow is not None
            and (
                (payload.decision == "approved" and persisted.status in {"proposed", "applied"})
                or (payload.decision == "rejected" and persisted.status == "rejected")
            )
        ):
            return persisted_workflow
        raise WorkflowConflict(
            "agent-decision-resolution-conflict",
            "The scientific revision was resolved concurrently. Reload the workflow.",
            retryable=True,
        )
    session.flush()
    session.refresh(decision)
    current_spec = session.scalar(
        select(AnalysisSpecRecord)
        .where(
            AnalysisSpecRecord.workflow_id == workflow.id,
            AnalysisSpecRecord.status == "approved",
        )
        .order_by(AnalysisSpecRecord.revision.desc())
    )
    event = AgentDecisionEventData(
        observation_id=observation.id,
        decision_id=decision.id,
        action="revise-analysis-spec",
        task_id=observation.task_id,
        target_step_key=None,
        previous_analysis_spec_id=(current_spec.id if current_spec is not None else None),
        proposed_analysis_spec_id=None,
        expected_workflow_revision=decision.expected_workflow_revision,
        reason_code=decision.reason_code,
    )
    append_workflow_events(
        session,
        workflow,
        [
            (
                (
                    "agent.decision-approved"
                    if payload.decision == "approved"
                    else "agent.decision-rejected"
                ),
                event,
                observation.task_id,
                None,
            )
        ],
    )
    if payload.decision == "approved":
        enqueue_job(
            session,
            workflow,
            kind="apply-agent-decision",
            operation_key=f"workflow:{workflow.id}:apply-decision:{decision.id}",
        )
    elif workflow.status in {"reviewing", "running"}:
        transition_workflow(
            session,
            workflow,
            "blocked",
            reason_code="agent-analysis-spec-revision-rejected",
            blocking_message="The proposed scientific method revision was rejected.",
        )
    elif workflow.status not in {"blocked", "failed"}:
        raise WorkflowConflict(
            "agent-decision-resolution-state-invalid",
            "The workflow is no longer waiting for this scientific revision.",
        )
    session.commit()
    session.refresh(workflow)
    return workflow


def _validated_interaction_response(
    interaction: InteractionRequestRecord,
    response: Any,
) -> Any:
    try:
        response_size = len(canonical_json_bytes(response))
    except (TypeError, ValueError):
        response_size = 65_537
    if response_size > 65_536:
        raise WorkflowConflict(
            "interaction-response-invalid",
            "The clarification response exceeds the 64 KiB limit.",
        )
    option_values = [option.get("value") for option in interaction.options]
    expects_multiple_values = interaction.request_type == "multi-choice" or (
        interaction.request_type == "column-selection"
        and interaction.response_schema.get("type") == "array"
    )
    if expects_multiple_values:
        response_items = cast(list[Any], response) if isinstance(response, list) else []
        if (
            not isinstance(response, list)
            or not response_items
            or len(response_items) > 100
            or len({content_sha256(item) for item in response_items})
            != len(response_items)
            or any(item not in option_values for item in response_items)
        ):
            raise WorkflowConflict(
                "interaction-response-invalid",
                "Select one or more of the available options.",
            )
        return response_items
    if interaction.request_type in {
        "single-choice",
        "column-selection",
        "method-confirmation",
        "assumption-confirmation",
    }:
        if option_values and response not in option_values:
            raise WorkflowConflict(
                "interaction-response-invalid",
                "Select one of the available options.",
            )
        if not option_values and not isinstance(response, (str, bool)):
            raise WorkflowConflict(
                "interaction-response-invalid",
                "The confirmation response has an invalid type.",
            )
        return response
    if interaction.request_type == "text":
        if not isinstance(response, str) or not response.strip() or len(response) > 8_000:
            raise WorkflowConflict(
                "interaction-response-invalid",
                "Enter a non-empty response of at most 8,000 characters.",
            )
        return response.strip()
    if interaction.request_type == "number":
        finite_response = False
        if not isinstance(response, bool) and isinstance(response, (int, float)):
            try:
                finite_response = math.isfinite(float(response))
            except (OverflowError, ValueError):
                finite_response = False
        if not finite_response:
            raise WorkflowConflict(
                "interaction-response-invalid",
                "Enter a finite numeric response.",
            )
        return response
    if interaction.request_type == "boolean":
        if not isinstance(response, bool):
            raise WorkflowConflict(
                "interaction-response-invalid",
                "Choose either true or false.",
            )
        return response
    raise WorkflowConflict(
        "interaction-response-invalid",
        "The interaction request type is unsupported.",
    )


def _supersede_pending_plan(session: Session, workflow: WorkflowRecord) -> None:
    leased = session.scalar(
        select(JobRecord.id).where(
            JobRecord.workflow_id == workflow.id,
            JobRecord.status == "leased",
        )
    )
    if leased is not None:
        raise WorkflowConflict(
            "workflow-busy",
            "Wait for the current planning operation to finish before changing this answer.",
            retryable=True,
        )
    now = utc_now()
    session.execute(
        update(JobRecord)
        .where(
            JobRecord.workflow_id == workflow.id,
            JobRecord.status == "queued",
        )
        .values(status="cancelled", finished_at=now, updated_at=now)
    )
    plan = session.scalar(
        select(PlanRecord)
        .where(
            PlanRecord.workflow_id == workflow.id,
            PlanRecord.status == "pending-approval",
        )
        .order_by(PlanRecord.version.desc())
    )
    if plan is None:
        return
    plan.status = "superseded"
    plan.superseded_at = now
    analysis_spec_id = plan.spec_json.get("analysisSpecId")
    analysis_spec = (
        session.get(AnalysisSpecRecord, analysis_spec_id)
        if isinstance(analysis_spec_id, str)
        else None
    )
    if (
        analysis_spec is not None
        and analysis_spec.workflow_id == workflow.id
        and analysis_spec.status in {"pending-approval", "approved"}
    ):
        analysis_spec.status = "superseded"
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "analysis.spec-superseded",
                    AnalysisSpecEventData(
                        analysis_spec_id=analysis_spec.id,
                        revision=analysis_spec.revision,
                        spec_sha256=analysis_spec.spec_sha256,
                        dataset_profile_sha256=(
                            analysis_spec.dataset_profile_sha256
                        ),
                        selector_kind=cast(Any, analysis_spec.selector_kind),
                        prompt_version=analysis_spec.prompt_version,
                    ),
                    None,
                    None,
                )
            ],
        )
    session.execute(
        update(ApprovalRecord)
        .where(
            ApprovalRecord.workflow_id == workflow.id,
            ApprovalRecord.plan_id == plan.id,
            ApprovalRecord.user_decision.is_(None),
        )
        .values(user_decision="superseded", decided_at=now)
    )
    session.execute(
        update(TaskRecord)
        .where(
            TaskRecord.workflow_id == workflow.id,
            TaskRecord.plan_id == plan.id,
            TaskRecord.status.in_(["draft-plan", "pending", "queued", "waiting-approval"]),
        )
        .values(status="cancelled", finished_at=now, updated_at=now)
    )


def _advance_scientific_planning_revision(
    session: Session,
    workflow: WorkflowRecord,
    *,
    expected_revision: int,
) -> None:
    current = workflow.status
    if current not in {"planning", "waiting-plan-approval"}:
        raise WorkflowConflict(
            "interaction-not-answerable",
            "Scientific clarification can only change a workflow during planning.",
        )
    result = session.execute(
        update(WorkflowRecord)
        .where(
            WorkflowRecord.id == workflow.id,
            WorkflowRecord.row_version == expected_revision,
            WorkflowRecord.status == current,
        )
        .values(
            status="planning",
            row_version=WorkflowRecord.row_version + 1,
            updated_at=utc_now(),
            blocking_code=None,
            blocking_message=None,
            last_error_code=None,
            last_error_message=None,
        )
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[object], result).rowcount != 1:
        session.expire_all()
        raise WorkflowConflict(
            "workflow-revision-conflict",
            "The workflow changed before the scientific response was applied.",
            retryable=True,
        )
    session.flush()
    session.refresh(workflow)
    if current != "planning":
        append_workflow_events(
            session,
            workflow,
            [
                (
                    "workflow.status-changed",
                    StatusChangedEventData(
                        previous_status=cast(WorkflowStatus, current),
                        status="planning",
                        reason_code="scientific-answer-changed",
                    ),
                    None,
                    None,
                )
            ],
        )


def _reset_agent_workflow_to_routing(
    session: Session,
    workflow: WorkflowRecord,
    *,
    selected_source_ids: list[str],
) -> None:
    current = workflow.status
    if not workflow_transition_allowed(current, "routing"):
        raise WorkflowConflict(
            "invalid-workflow-transition",
            f"Workflow cannot move from {current} to routing.",
        )
    result = session.execute(
        update(WorkflowRecord)
        .where(
            WorkflowRecord.id == workflow.id,
            WorkflowRecord.row_version == workflow.row_version,
            WorkflowRecord.status == current,
        )
        .values(
            selected_source_ids=selected_source_ids,
            current_intent_decision_id=None,
            workflow_type=None,
            dataset_source_id=None,
            dataset_content_hash=None,
            generation_mode="local-deterministic",
            status="routing",
            row_version=WorkflowRecord.row_version + 1,
            updated_at=utc_now(),
            finished_at=None,
            blocking_code=None,
            blocking_message=None,
            last_error_code=None,
            last_error_message=None,
        )
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[object], result).rowcount != 1:
        session.expire_all()
        raise WorkflowConflict(
            "workflow-revision-conflict",
            "The workflow changed before the clarification response was applied.",
            retryable=True,
        )
    session.flush()
    session.refresh(workflow)
    append_workflow_events(
        session,
        workflow,
        [
            (
                "workflow.status-changed",
                StatusChangedEventData(
                    previous_status=cast(WorkflowStatus, current),
                    status="routing",
                    reason_code="clarification-answer-changed",
                ),
                None,
                None,
            )
        ],
    )


def interaction_requests(
    session: Session,
    workflow: WorkflowRecord,
) -> list[InteractionRequestOut]:
    records = list(
        session.scalars(
            select(InteractionRequestRecord)
            .where(InteractionRequestRecord.workflow_id == workflow.id)
            .order_by(InteractionRequestRecord.created_at, InteractionRequestRecord.id)
        )
    )
    return [_interaction_out(session, record) for record in records]


def _interaction_out(
    session: Session,
    interaction: InteractionRequestRecord,
) -> InteractionRequestOut:
    response = session.scalar(
        select(UserResponseRecord)
        .where(UserResponseRecord.interaction_id == interaction.id)
        .order_by(UserResponseRecord.revision.desc())
    )
    return InteractionRequestOut(
        id=interaction.id,
        workflow_id=interaction.workflow_id,
        step_id=interaction.step_id,
        request_type=cast(Any, interaction.request_type),
        question=interaction.question,
        options=interaction.options,
        required=interaction.required,
        status=cast(Any, interaction.status),
        response_schema=interaction.response_schema,
        workflow_revision=interaction.workflow_revision,
        latest_response=(
            UserResponseOut(
                id=response.id,
                interaction_id=response.interaction_id,
                revision=response.revision,
                response=response.response_json,
                response_sha256=response.response_sha256,
                created_at=response.created_at,
            )
            if response is not None
            else None
        ),
        created_at=interaction.created_at,
        answered_at=interaction.answered_at,
    )


def _step_observation_out(record: StepObservationRecord) -> StepObservationOut:
    payload = {
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
    }
    observation = StepObservation.model_validate_json(
        json.dumps(payload, allow_nan=False, ensure_ascii=False)
    )
    if step_observation_sha256(observation) != record.output_sha256:
        raise WorkflowConflict(
            "agent-observation-integrity-failed",
            "The persisted Agent observation no longer matches its immutable hash.",
        )
    return StepObservationOut.model_validate(
        {
            **observation.model_dump(mode="python", by_alias=True),
            "id": record.id,
            "inputSha256": record.input_sha256,
            "outputSha256": record.output_sha256,
            "generator": record.generator,
            "promptVersion": record.prompt_version,
            "model": record.model,
            "modelInvocationId": record.model_invocation_id,
            "createdAt": record.created_at,
        }
    )


def _decision_research_context_provenance(
    session: Session,
    workflow: WorkflowRecord,
    record: AgentDecisionRecord,
) -> tuple[str | None, str | None]:
    matching_events = [
        event
        for event in session.scalars(
            select(EventRecord).where(
                EventRecord.workflow_id == workflow.id,
                EventRecord.event_type == "agent.decision-proposed",
            )
        )
        if event.payload.get("decisionId") == record.id
    ]
    if not matching_events:
        # Events written before context provenance was introduced remain readable.
        return None, None
    if len(matching_events) != 1:
        raise WorkflowConflict(
            "agent-decision-provenance-invalid",
            "The Agent decision has no unique proposed-event provenance.",
        )
    try:
        event_data = AgentDecisionEventData.model_validate(
            matching_events[0].payload, strict=True
        )
    except ValidationError as error:
        raise WorkflowConflict(
            "agent-decision-provenance-invalid",
            "The Agent decision proposed-event payload is invalid.",
        ) from error
    if (
        event_data.decision_id != record.id
        or event_data.observation_id != record.observation_id
        or event_data.action != record.action
        or event_data.expected_workflow_revision != record.expected_workflow_revision
        or event_data.reason_code != record.reason_code
    ):
        raise WorkflowConflict(
            "agent-decision-provenance-invalid",
            "The Agent decision proposed-event does not match the decision record.",
        )
    if event_data.research_context_snapshot_id is None:
        return None, None
    snapshot = session.get(
        AgentContextSnapshotRecord, event_data.research_context_snapshot_id
    )
    if (
        snapshot is None
        or snapshot.workflow_id != workflow.id
        or snapshot.context_sha256 != event_data.research_context_snapshot_sha256
    ):
        raise WorkflowConflict(
            "agent-decision-provenance-invalid",
            "The Agent decision context snapshot provenance is invalid.",
        )
    return (event_data.research_context_snapshot_id, snapshot.context_sha256)


def _agent_decision_out(
    session: Session,
    workflow: WorkflowRecord,
    record: AgentDecisionRecord,
) -> AgentDecisionOut:
    payload = {
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
    decision = AgentDecision.model_validate_json(
        json.dumps(payload, allow_nan=False, ensure_ascii=False)
    )
    if agent_decision_sha256(decision) != record.output_sha256:
        raise WorkflowConflict(
            "agent-decision-integrity-failed",
            "The persisted Agent decision no longer matches its immutable hash.",
        )
    research_context_snapshot_id, research_context_snapshot_sha256 = (
        _decision_research_context_provenance(session, workflow, record)
    )
    return AgentDecisionOut.model_validate(
        {
            **decision.model_dump(mode="python", by_alias=True),
            "id": record.id,
            "workflowId": record.workflow_id,
            "observationId": record.observation_id,
            "decisionRevision": record.decision_revision,
            "status": record.status,
            "expectedWorkflowRevision": record.expected_workflow_revision,
            "generator": record.generator,
            "promptVersion": record.prompt_version,
            "model": record.model,
            "modelInvocationId": record.model_invocation_id,
            "inputSha256": record.input_sha256,
            "outputSha256": record.output_sha256,
            "researchContextSnapshotId": research_context_snapshot_id,
            "researchContextSnapshotSha256": research_context_snapshot_sha256,
            "appliedAt": record.applied_at,
            "createdAt": record.created_at,
        }
    )


def _agent_decision_summary(
    session: Session,
    workflow: WorkflowRecord,
    record: AgentDecisionRecord,
) -> AgentDecisionSummaryOut:
    research_context_snapshot_id, research_context_snapshot_sha256 = (
        _decision_research_context_provenance(session, workflow, record)
    )
    return AgentDecisionSummaryOut(
        id=record.id,
        observation_id=record.observation_id,
        action=cast(Any, record.action),
        reason=record.reason,
        status=cast(Any, record.status),
        requires_user_confirmation=record.requires_user_confirmation,
        research_context_snapshot_id=research_context_snapshot_id,
        research_context_snapshot_sha256=research_context_snapshot_sha256,
        created_at=record.created_at,
        applied_at=record.applied_at,
    )


def _limit_usage(count: int, limit: int) -> AgentLoopLimitUsageOut:
    return AgentLoopLimitUsageOut(
        count=count,
        limit=limit,
        reached=count >= limit,
    )


def _agent_loop_snapshot_fields(
    session: Session,
    workflow: WorkflowRecord,
) -> tuple[
    StepObservationOut | None,
    AgentDecisionOut | None,
    list[AgentDecisionSummaryOut],
    AgentLoopLimitStateOut,
]:
    observations = list(
        session.scalars(
            select(StepObservationRecord)
            .where(StepObservationRecord.workflow_id == workflow.id)
            .order_by(StepObservationRecord.created_at, StepObservationRecord.id)
        )
    )
    decisions = list(
        session.scalars(
            select(AgentDecisionRecord)
            .where(AgentDecisionRecord.workflow_id == workflow.id)
            .order_by(AgentDecisionRecord.created_at, AgentDecisionRecord.id)
        )
    )
    live_decisions = [
        record
        for record in decisions
        if record.status in {"proposed", "waiting-user-confirmation"}
    ]
    if len(live_decisions) > 1:
        raise WorkflowConflict(
            "agent-decision-integrity-failed",
            "The workflow has more than one pending Agent decision.",
        )
    latest_observation = (
        _step_observation_out(observations[-1]) if observations else None
    )
    pending_decision = (
        _agent_decision_out(session, workflow, live_decisions[0]) if live_decisions else None
    )
    spec_revision = session.scalar(
        select(func.max(AnalysisSpecRecord.revision)).where(
            AnalysisSpecRecord.workflow_id == workflow.id
        )
    )
    model_invocation_ids = {
        record.model_invocation_id
        for record in decisions
        if record.model_invocation_id is not None
    }
    model_invocations = (
        list(
            session.scalars(
                select(ModelInvocationRecord).where(
                    ModelInvocationRecord.workflow_id == workflow.id,
                    ModelInvocationRecord.id.in_(model_invocation_ids),
                )
            )
        )
        if model_invocation_ids
        else []
    )
    if len(model_invocations) != len(model_invocation_ids):
        raise WorkflowConflict(
            "agent-decision-integrity-failed",
            "An Agent decision is missing its model invocation provenance.",
        )
    limits = AgentLoopLimitStateOut(
        agent_steps=_limit_usage(len(observations), MAX_AGENT_STEPS),
        plan_revisions=_limit_usage(
            sum(
                record.action == "revise-analysis-spec" and record.status == "applied"
                for record in decisions
            ),
            MAX_PLAN_REVISIONS,
        ),
        analysis_spec_revisions=_limit_usage(
            max((spec_revision or 1) - 1, 0),
            MAX_ANALYSIS_SPEC_REVISIONS,
        ),
        step_retries=_limit_usage(
            sum(
                record.action == "retry-step" and record.status == "applied"
                for record in decisions
            ),
            MAX_STEP_RETRIES,
        ),
        clarification_rounds=_limit_usage(
            sum(
                record.action == "request-clarification"
                and record.status == "applied"
                for record in decisions
            ),
            MAX_CLARIFICATION_ROUNDS,
        ),
        model_decisions=_limit_usage(len(model_invocation_ids), MAX_MODEL_DECISIONS),
        invalid_model_decisions=_limit_usage(
            sum(
                invocation.status == "failed" or bool(invocation.validation_errors)
                for invocation in model_invocations
            ),
            MAX_INVALID_MODEL_DECISIONS,
        ),
    )
    return (
        latest_observation,
        pending_decision,
        [
            _agent_decision_summary(session, workflow, record)
            for record in reversed(decisions)
        ],
        limits,
    )


def agent_run_snapshot(
    session: Session,
    workflow: WorkflowRecord,
) -> AgentRunSnapshot:
    if workflow.creation_mode != "autonomous":
        raise WorkflowConflict(
            "agent-run-not-found",
            "The requested workflow is not an autonomous agent run.",
        )
    decision = (
        session.get(IntentDecisionRecord, workflow.current_intent_decision_id)
        if workflow.current_intent_decision_id is not None
        else None
    )
    decision_required = workflow.workflow_type is not None or workflow.status in {
        "waiting-clarification",
        "unsupported",
    }
    if decision is None and decision_required:
        raise WorkflowConflict(
            "intent-decision-integrity-failed",
            "The autonomous workflow is missing its current intent decision.",
        )
    if decision is not None and decision.workflow_id != workflow.id:
        raise WorkflowConflict(
            "intent-decision-integrity-failed",
            "The current intent decision does not belong to this workflow.",
        )
    if (
        decision is not None
        and workflow.workflow_type is not None
        and (
            decision.intent != workflow.workflow_type
            or decision.proposed_workflow_type != workflow.workflow_type
        )
    ):
        raise WorkflowConflict(
            "intent-decision-integrity-failed",
            "The current intent decision does not match the resolved workflow type.",
        )
    if (
        decision is not None
        and workflow.status == "waiting-clarification"
        and decision.intent != "clarification-required"
    ):
        raise WorkflowConflict(
            "intent-decision-integrity-failed",
            "The waiting workflow is not bound to a clarification decision.",
        )
    if (
        decision is not None
        and workflow.status == "unsupported"
        and decision.intent not in {"mixed-research", "unsupported"}
    ):
        raise WorkflowConflict(
            "intent-decision-integrity-failed",
            "The unsupported workflow is not bound to an unsupported decision.",
        )
    decision_invocation: ModelInvocationRecord | None = None
    if decision is not None and decision.model_invocation_id is not None:
        decision_invocation = session.get(
            ModelInvocationRecord,
            decision.model_invocation_id,
        )
        if decision_invocation is None:
            raise WorkflowConflict(
                "intent-decision-integrity-failed",
                "The current intent decision is missing its model invocation provenance.",
            )
        try:
            _assert_intent_provenance_binding(workflow, decision_invocation, decision)
        except WorkflowFailure as error:
            raise WorkflowConflict(
                "intent-decision-integrity-failed",
                error.user_message,
            ) from error
    elif decision is not None:
        try:
            _assert_intent_decision_output_integrity(decision)
        except WorkflowFailure as error:
            raise WorkflowConflict(
                "intent-decision-integrity-failed",
                error.user_message,
            ) from error
    base = None
    if workflow.workflow_type is not None:
        active_plan = session.scalar(
            select(PlanRecord.id).where(
                PlanRecord.workflow_id == workflow.id,
                PlanRecord.status.in_(["pending-approval", "approved"]),
            )
        )
        if active_plan is not None or workflow.status != "planning":
            base = workflow_snapshot(session, workflow)
    jobs = list(
        session.scalars(select(JobRecord).where(JobRecord.workflow_id == workflow.id))
    )
    retry_count = sum(1 for job in jobs if job.attempt > 1)
    status_reason = _agent_status_reason(workflow)
    plan_version = base.workflow.plan_version if base is not None else None
    current_step_id = base.workflow.current_step_id if base is not None else None
    interactions = interaction_requests(session, workflow)
    if workflow.status == "waiting-clarification" and not any(
        item.status == "pending" for item in interactions
    ):
        raise WorkflowConflict(
            "interaction-integrity-failed",
            "The waiting workflow is missing its pending clarification request.",
        )
    (
        latest_observation,
        pending_decision,
        decision_history,
        agent_loop_limits,
    ) = _agent_loop_snapshot_fields(session, workflow)
    if any(
        item.status == "answered" and item.latest_response is None
        for item in interactions
    ):
        raise WorkflowConflict(
            "interaction-integrity-failed",
            "An answered clarification request is missing its durable response.",
        )
    can_revise_answer = workflow.status in {
        "planning",
        "waiting-plan-approval",
    } and any(item.status == "answered" for item in interactions)
    can_respond_interaction = any(
        item.status == "pending" for item in interactions
    ) or can_revise_answer
    allowed_actions: list[AgentAllowedAction]
    if base is not None:
        allowed_actions = [
            cast(AgentAllowedAction, action) for action in base.allowed_actions
        ]
        if can_respond_interaction:
            allowed_actions.insert(0, "respond-interaction")
    elif workflow.status == "routing":
        allowed_actions = ["cancel"]
    elif workflow.status == "waiting-clarification":
        allowed_actions = ["respond-interaction", "cancel"]
    elif workflow.status == "planning":
        allowed_actions = (
            ["respond-interaction", "cancel"]
            if can_respond_interaction
            else ["cancel"]
        )
    elif workflow.status == "failed":
        allowed_actions = ["retry", "cancel"]
    else:
        allowed_actions = []
    if (
        pending_decision is not None
        and pending_decision.status == "waiting-user-confirmation"
        and pending_decision.requires_user_confirmation
    ):
        allowed_actions = [
            "approve-agent-decision",
            "reject-agent-decision",
            *(["cancel"] if "cancel" in allowed_actions else []),
        ]
    return AgentRunSnapshot(
        workflow=AgentWorkflowStateOut(
            id=workflow.id,
            project_id=workflow.project_id,
            workflow_type=cast(
                ResolvedAgentWorkflowType | None,
                workflow.workflow_type,
            ),
            goal=workflow.goal,
            source_ids=list(workflow.selected_source_ids),
            mode="autonomous",
            generation_mode=cast(Any, workflow.generation_mode),
            status=cast(Any, workflow.status),
            revision=workflow.row_version,
            plan_version=plan_version,
            current_step_id=current_step_id,
            retry_count=retry_count,
            status_reason=status_reason,
            cancel_requested_at=workflow.cancel_requested_at,
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
            completed_at=workflow.finished_at,
        ),
        intent_decision=(
            IntentDecisionOut(
                id=decision.id,
                workflow_id=decision.workflow_id,
                intent=cast(Any, decision.intent),
                confidence=decision.confidence,
                reasoning_summary=decision.reasoning_summary,
                selected_source_ids=decision.selected_source_ids,
                missing_inputs=decision.missing_inputs,
                proposed_workflow_type=cast(Any, decision.proposed_workflow_type),
                generator=decision.generator,
                used_model=decision.used_model,
                model=decision.model,
                endpoint_identity=(
                    decision_invocation.endpoint_identity
                    if decision_invocation is not None
                    else None
                ),
                prompt_version=decision.prompt_version,
                parse_result=cast(Any, decision.parse_result),
                input_sha256=decision.input_sha256,
                output_sha256=decision.output_sha256,
                created_at=decision.created_at,
            )
            if decision is not None
            else None
        ),
        interactions=interactions,
        plan=base.plan if base is not None else None,
        pending_approvals=base.pending_approvals if base is not None else [],
        result=base.result if base is not None else None,
        latest_review=base.latest_review if base is not None else None,
        dataset_profile=base.dataset_profile if base is not None else None,
        analysis_intent=base.analysis_intent if base is not None else None,
        analysis_run=base.analysis_run if base is not None else None,
        analysis_spec=base.analysis_spec if base is not None else None,
        structured_result=base.structured_result if base is not None else None,
        review_warning_acceptance=(
            base.review_warning_acceptance if base is not None else None
        ),
        latest_observation=latest_observation,
        pending_decision=pending_decision,
        decision_history=decision_history,
        agent_loop_limits=agent_loop_limits,
        allowed_actions=allowed_actions,
        event_cursor=workflow.event_sequence,
    )


def _agent_status_reason(workflow: WorkflowRecord) -> AgentStatusReasonOut | None:
    if workflow.status == "blocked":
        return AgentStatusReasonOut(
            code=workflow.blocking_code or "blocked",
            user_message=workflow.blocking_message or "The workflow is blocked.",
        )
    if workflow.status in {"failed", "unsupported"}:
        return AgentStatusReasonOut(
            code=workflow.last_error_code or workflow.status,
            user_message=(
                workflow.last_error_message
                or "The workflow cannot continue in its current state."
            ),
        )
    return None


def list_agent_runs(
    session: Session,
    project_id: str,
    *,
    active_only: bool,
    limit: int,
) -> list[WorkflowRecord]:
    query = select(WorkflowRecord).where(
        WorkflowRecord.project_id == project_id,
        WorkflowRecord.creation_mode == "autonomous",
    )
    if active_only:
        query = query.where(
            WorkflowRecord.status.not_in(
                ["completed", "unsupported", "cancelled"]
            )
        )
    return list(
        session.scalars(query.order_by(WorkflowRecord.updated_at.desc()).limit(limit))
    )
