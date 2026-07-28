"""Public creation and read-only snapshots for exact-scope paper discovery."""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    AgentDecisionRecord,
    ApprovalRecord,
    CandidateOccurrenceRecord,
    DiscoveryCandidateRecord,
    DiscoverySpecRecord,
    EventRecord,
    IntentDecisionRecord,
    PlanRecord,
    ProjectRecord,
    SourceRecord,
    ToolInvocationRecord,
    WorkflowRecord,
)
from ._service.events import append_workflow_events
from ._service.integrity import (
    WorkflowConflict,
    assert_plan_approval_integrity,
    assert_plan_for_workflow,
    content_sha256,
    plan_approval_hash,
)
from .discovery_adapter import (
    discovery_operation_key,
    discovery_operations,
    discovery_plan_spec,
)
from .discovery_schemas import (
    DISCOVERY_PLAN_APPROVAL_REASON,
    DISCOVERY_PLAN_APPROVAL_SCHEMA_VERSION,
    DiscoveryAgentSelectionOut,
    DiscoveryCandidate,
    DiscoveryCandidateOccurrenceOut,
    DiscoveryCandidateOut,
    DiscoveryCandidatePageOut,
    DiscoveryOperationProgressOut,
    DiscoveryPolicyStopReason,
    DiscoveryProvider,
    DiscoveryRetryClassification,
    DiscoveryRunCreateIn,
    DiscoverySpec,
    DiscoverySummaryOut,
    WorkflowDiscoverySnapshotOut,
    discovery_approval_resources,
    discovery_candidate_sha256,
    discovery_sha256,
)
from .intent_router import (
    IntentDecision,
    intent_decision_sha256,
)
from .schemas import (
    AgentDecisionEventData,
    AgentRunCreatedEventData,
    ApprovalEventData,
    IntentDecisionEventData,
    PaperDiscoveryPlanSpec,
    PlanEventData,
)


def start_discovery_run(
    session: Session,
    project: ProjectRecord,
    payload: DiscoveryRunCreateIn,
    idempotency_key: str,
) -> WorkflowRecord:
    """Persist an exact proposal without enqueueing or sending external work."""

    payload_hash = content_sha256(
        payload.model_dump(mode="json", by_alias=True, exclude_none=False)
    )
    replay = _discovery_create_replay(
        session,
        project_id=project.id,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
    )
    if replay is not None:
        return replay

    workflow = WorkflowRecord(
        id=str(uuid.uuid4()),
        project_id=project.id,
        create_idempotency_key=idempotency_key,
        create_payload_sha256=payload_hash,
        creation_mode="autonomous",
        selected_source_ids=[],
        current_intent_decision_id=None,
        workflow_type="literature-synthesis",
        dataset_source_id=None,
        dataset_content_hash=None,
        goal=payload.goal,
        generation_mode="local-deterministic",
        status="waiting-plan-approval",
        row_version=1,
        event_sequence=0,
    )
    session.add(workflow)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        replay = _discovery_create_replay(
            session,
            project_id=project.id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
        if replay is not None:
            return replay
        raise

    canonical_spec = payload.discovery_spec.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=False,
    )
    spec_hash = discovery_sha256(payload.discovery_spec)
    discovery_record = DiscoverySpecRecord(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        revision=1,
        previous_spec_id=None,
        schema_version="1",
        spec_json=canonical_spec,
        spec_sha256=spec_hash,
        status="pending-approval",
    )
    session.add(discovery_record)
    session.flush()

    intent = IntentDecision(
        intent="literature-synthesis",
        confidence=1.0,
        reasoning_summary=("The caller supplied a complete strict public paper-discovery scope."),
        selected_source_ids=[],
        missing_inputs=[],
        proposed_workflow_type="literature-synthesis",
    )
    intent_record = IntentDecisionRecord(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        revision=1,
        intent=intent.intent,
        confidence=intent.confidence,
        reasoning_summary=intent.reasoning_summary,
        selected_source_ids=[],
        missing_inputs=[],
        proposed_workflow_type=intent.proposed_workflow_type,
        generator="public-discovery-intent-v1",
        used_model=False,
        model=None,
        prompt_version="public-discovery-intent-v1",
        parse_result="valid",
        model_invocation_id=None,
        input_sha256=content_sha256(
            {
                "discoverySpecSha256": spec_hash,
                "goal": payload.goal,
                "route": "literature-synthesis",
            }
        ),
        output_sha256=intent_decision_sha256(intent),
    )
    session.add(intent_record)
    session.flush()
    workflow.current_intent_decision_id = intent_record.id

    plan_json = discovery_plan_spec(discovery_record, payload.discovery_spec)
    plan = PlanRecord(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        version=1,
        spec_json=plan_json,
        spec_sha256=content_sha256(plan_json),
        status="pending-approval",
        generator="paper-discovery-v1",
        model=None,
        prompt_version="paper-discovery-v1",
    )
    session.add(plan)
    session.flush()

    affected_resources = discovery_approval_resources(
        project_id=project.id,
        spec_id=discovery_record.id,
        revision=discovery_record.revision,
        spec_sha256=discovery_record.spec_sha256,
        spec=payload.discovery_spec,
    )
    approval = ApprovalRecord(
        id=str(uuid.uuid4()),
        task_id=None,
        workflow_id=workflow.id,
        plan_id=plan.id,
        subject_type="plan",
        subject_id=plan.id,
        payload_schema_version=DISCOVERY_PLAN_APPROVAL_SCHEMA_VERSION,
        row_version=1,
        intent_hash=plan_approval_hash(
            plan,
            affected_resources,
            schema_version=DISCOVERY_PLAN_APPROVAL_SCHEMA_VERSION,
            workflow_goal=workflow.goal,
            risk_level="medium",
            reason=DISCOVERY_PLAN_APPROVAL_REASON,
            subject_id=plan.id,
            task_id=None,
        ),
        requested_action="approve-research-plan",
        risk_level="medium",
        reason=DISCOVERY_PLAN_APPROVAL_REASON,
        affected_resources=affected_resources,
    )
    session.add(approval)
    session.flush()
    assert_plan_for_workflow(workflow, plan)
    assert_plan_approval_integrity(session, workflow, plan)

    append_workflow_events(
        session,
        workflow,
        [
            (
                "agent-run.created",
                AgentRunCreatedEventData(
                    goal_sha256=hashlib.sha256(workflow.goal.encode("utf-8")).hexdigest(),
                    source_ids=[],
                    mode="autonomous",
                    generation_mode="local-deterministic",
                ),
                None,
                None,
            ),
            (
                "intent.decision-recorded",
                IntentDecisionEventData(
                    intent_decision_id=intent_record.id,
                    intent="literature-synthesis",
                    confidence=1.0,
                    output_sha256=intent_record.output_sha256,
                ),
                None,
                None,
            ),
            (
                "plan.generated",
                PlanEventData(
                    plan_id=plan.id,
                    version=plan.version,
                    plan_sha256=plan.spec_sha256,
                ),
                None,
                None,
            ),
            (
                "approval.requested",
                ApprovalEventData(
                    approval_id=approval.id,
                    subject_type="plan",
                    subject_id=plan.id,
                    action=approval.requested_action,
                    payload_sha256=approval.intent_hash,
                    risk_level=approval.risk_level,
                    reason=approval.reason,
                    affected_resources=approval.affected_resources,
                    approval_schema_version=approval.payload_schema_version,
                ),
                None,
                None,
            ),
        ],
    )
    session.commit()
    session.refresh(workflow)
    return workflow


def _discovery_create_replay(
    session: Session,
    *,
    project_id: str,
    idempotency_key: str,
    payload_hash: str,
) -> WorkflowRecord | None:
    existing = session.scalar(
        select(WorkflowRecord)
        .where(
            WorkflowRecord.project_id == project_id,
            WorkflowRecord.create_idempotency_key == idempotency_key,
        )
        .execution_options(populate_existing=True)
    )
    if existing is None:
        return None
    if existing.create_payload_sha256 != payload_hash:
        raise WorkflowConflict(
            "idempotency-key-reused",
            "This Idempotency-Key was already used with a different discovery request.",
        )
    plan = session.scalar(
        select(PlanRecord).where(
            PlanRecord.workflow_id == existing.id,
            PlanRecord.status.in_(["pending-approval", "approved"]),
        )
    )
    spec = session.scalar(
        select(DiscoverySpecRecord).where(
            DiscoverySpecRecord.workflow_id == existing.id,
            DiscoverySpecRecord.status.in_(["pending-approval", "approved"]),
        )
    )
    if (
        existing.creation_mode != "autonomous"
        or existing.workflow_type != "literature-synthesis"
        or plan is None
        or plan.generator != "paper-discovery-v1"
        or spec is None
    ):
        raise WorkflowConflict(
            "idempotency-key-reused",
            "This Idempotency-Key belongs to a different workflow creation contract.",
        )
    assert_plan_approval_integrity(session, existing, plan)
    return existing


def workflow_discovery_snapshot(
    session: Session,
    workflow: WorkflowRecord,
    *,
    offset: int,
    limit: int,
) -> WorkflowDiscoverySnapshotOut:
    """Rebuild a bounded discovery view entirely from durable workflow state."""

    plan = session.scalar(
        select(PlanRecord).where(
            PlanRecord.workflow_id == workflow.id,
            PlanRecord.status.in_(["pending-approval", "approved"]),
        )
    )
    if (
        workflow.creation_mode != "autonomous"
        or workflow.workflow_type != "literature-synthesis"
        or plan is None
        or plan.generator != "paper-discovery-v1"
    ):
        raise WorkflowConflict(
            "discovery-workflow-mismatch",
            "The requested workflow is not a public paper-discovery run.",
        )
    parsed_plan = assert_plan_for_workflow(workflow, plan)
    if not isinstance(parsed_plan, PaperDiscoveryPlanSpec):
        raise WorkflowConflict(
            "discovery-plan-invalid",
            "The public discovery run has no strict paper-discovery plan.",
        )
    assert_plan_approval_integrity(session, workflow, plan)
    discovery_record = session.get(
        DiscoverySpecRecord,
        parsed_plan.discovery_spec_id,
    )
    if discovery_record is None or discovery_record.workflow_id != workflow.id:
        raise WorkflowConflict(
            "discovery-spec-invalid",
            "The workflow discovery specification is missing or belongs elsewhere.",
        )
    try:
        spec = DiscoverySpec.model_validate(discovery_record.spec_json)
    except ValidationError:
        raise WorkflowConflict(
            "discovery-spec-invalid",
            "The workflow discovery specification no longer satisfies its strict schema.",
        ) from None
    if (
        discovery_sha256(spec) != discovery_record.spec_sha256
        or parsed_plan.discovery_spec_revision != discovery_record.revision
        or parsed_plan.discovery_spec_sha256 != discovery_record.spec_sha256
        or parsed_plan.goal != spec.question
        or workflow.goal != spec.question
    ):
        raise WorkflowConflict(
            "discovery-spec-integrity-invalid",
            "The workflow, plan, and discovery specification no longer match.",
        )

    operations = discovery_operations(discovery_record, spec)
    invocation_rows = list(
        session.scalars(
            select(ToolInvocationRecord)
            .where(
                ToolInvocationRecord.workflow_id == workflow.id,
                ToolInvocationRecord.discovery_spec_id == discovery_record.id,
            )
            .order_by(
                ToolInvocationRecord.operation_key,
                ToolInvocationRecord.attempt.desc(),
                ToolInvocationRecord.created_at.desc(),
            )
        )
    )
    latest_by_operation: dict[str, ToolInvocationRecord] = {}
    for invocation in invocation_rows:
        latest_by_operation.setdefault(invocation.operation_key, invocation)

    progress: list[DiscoveryOperationProgressOut] = []
    for operation in operations:
        operation_key = discovery_operation_key(
            discovery_record.id,
            operation.query.id,
            operation.provider,
        )
        invocation = latest_by_operation.get(operation_key)
        progress.append(
            _operation_progress(
                query_id=operation.query.id,
                provider=operation.provider,
                operation_key=operation_key,
                invocation=invocation,
            )
        )

    occurrence_filter = (
        CandidateOccurrenceRecord.project_id == workflow.project_id,
        ToolInvocationRecord.workflow_id == workflow.id,
        ToolInvocationRecord.discovery_spec_id == discovery_record.id,
    )
    unique_candidate_count = int(
        session.scalar(
            select(func.count(func.distinct(CandidateOccurrenceRecord.candidate_id)))
            .join(
                ToolInvocationRecord,
                ToolInvocationRecord.id == CandidateOccurrenceRecord.invocation_id,
            )
            .where(*occurrence_filter)
        )
        or 0
    )
    occurrence_count = int(
        session.scalar(
            select(func.count())
            .select_from(CandidateOccurrenceRecord)
            .join(
                ToolInvocationRecord,
                ToolInvocationRecord.id == CandidateOccurrenceRecord.invocation_id,
            )
            .where(*occurrence_filter)
        )
        or 0
    )
    candidate_ids = list(
        session.scalars(
            select(CandidateOccurrenceRecord.candidate_id)
            .join(
                ToolInvocationRecord,
                ToolInvocationRecord.id == CandidateOccurrenceRecord.invocation_id,
            )
            .where(*occurrence_filter)
            .group_by(CandidateOccurrenceRecord.candidate_id)
            .order_by(
                func.min(CandidateOccurrenceRecord.created_at),
                CandidateOccurrenceRecord.candidate_id,
            )
            .offset(offset)
            .limit(limit)
        )
    )
    candidate_records = {
        item.id: item
        for item in session.scalars(
            select(DiscoveryCandidateRecord).where(
                DiscoveryCandidateRecord.project_id == workflow.project_id,
                DiscoveryCandidateRecord.id.in_(candidate_ids),
            )
        )
    }
    occurrence_rows = list(
        session.execute(
            select(CandidateOccurrenceRecord, ToolInvocationRecord)
            .join(
                ToolInvocationRecord,
                ToolInvocationRecord.id == CandidateOccurrenceRecord.invocation_id,
            )
            .where(
                *occurrence_filter,
                CandidateOccurrenceRecord.candidate_id.in_(candidate_ids),
            )
            .order_by(
                CandidateOccurrenceRecord.candidate_id,
                ToolInvocationRecord.created_at,
                ToolInvocationRecord.attempt,
                CandidateOccurrenceRecord.rank,
            )
        )
    )
    occurrences_by_candidate: dict[
        str,
        list[DiscoveryCandidateOccurrenceOut],
    ] = defaultdict(list)
    for occurrence, invocation in occurrence_rows:
        occurrences_by_candidate[occurrence.candidate_id].append(
            DiscoveryCandidateOccurrenceOut(
                invocation_id=invocation.id,
                query_id=invocation.query_id,
                provider=invocation.provider,
                attempt=invocation.attempt,
                rank=occurrence.rank,
                raw_item_sha256=occurrence.raw_item_sha256,
            )
        )
    attached_sources: dict[str, str] = {}
    for event in session.scalars(
        select(EventRecord).where(
            EventRecord.project_id == workflow.project_id,
            EventRecord.workflow_id == workflow.id,
            EventRecord.event_type == "source.discovery-attached",
        )
    ):
        candidate_id = event.payload.get("candidateId")
        source_id = event.payload.get("sourceId")
        candidate_sha256 = event.payload.get("candidateSha256")
        source_content_hash = event.payload.get("sourceContentHash")
        if not (
            isinstance(candidate_id, str)
            and isinstance(source_id, str)
            and isinstance(candidate_sha256, str)
            and isinstance(source_content_hash, str)
        ):
            continue
        candidate_record = candidate_records.get(candidate_id)
        source_record = session.scalar(
            select(SourceRecord).where(
                SourceRecord.project_id == workflow.project_id,
                SourceRecord.id == source_id,
                SourceRecord.content_hash == source_content_hash,
                SourceRecord.ingestion_status == "ready",
            )
        )
        if (
            candidate_record is not None
            and candidate_record.candidate_sha256 == candidate_sha256
            and source_record is not None
        ):
            attached_sources[candidate_id] = source_id

    candidates = [
        _candidate_out(
            candidate_records.get(candidate_id),
            occurrences_by_candidate[candidate_id],
            attached_sources.get(candidate_id),
        )
        for candidate_id in candidate_ids
    ]

    return WorkflowDiscoverySnapshotOut(
        workflow_id=workflow.id,
        project_id=workflow.project_id,
        workflow_status=cast(Any, workflow.status),
        stop_reason=cast(
            DiscoveryPolicyStopReason,
            workflow.blocking_code,
        )
        if workflow.blocking_code
        in {
            "discovery-candidate-target-reached",
            "discovery-no-novelty-limit",
            "discovery-attempt-budget-reached",
        }
        else None,
        discovery_spec_id=discovery_record.id,
        discovery_spec_revision=discovery_record.revision,
        discovery_spec_sha256=discovery_record.spec_sha256,
        discovery_spec_status=cast(Any, discovery_record.status),
        exact_scope=spec,
        operations=progress,
        summary=DiscoverySummaryOut(
            total_operations=len(progress),
            not_started_operations=sum(item.status == "not-started" for item in progress),
            in_progress_operations=sum(item.status in {"prepared", "pending"} for item in progress),
            succeeded_operations=sum(item.status == "succeeded" for item in progress),
            failed_operations=sum(item.status == "failed" for item in progress),
            outcome_unknown_operations=sum(item.status == "outcome-unknown" for item in progress),
            cancelled_operations=sum(item.status == "cancelled" for item in progress),
            returned_count=sum(item.returned_count for item in progress),
            novel_candidate_count=sum(item.novel_candidate_count for item in progress),
            duplicate_count=sum(item.duplicate_count for item in progress),
            unique_candidate_count=unique_candidate_count,
            occurrence_count=occurrence_count,
        ),
        candidates=DiscoveryCandidatePageOut(
            offset=offset,
            limit=limit,
            total=unique_candidate_count,
            has_more=offset + len(candidates) < unique_candidate_count,
            items=candidates,
        ),
        latest_agent_selection=_latest_agent_selection(
            session,
            workflow,
            plan,
            discovery_record,
        ),
    )


def _latest_agent_selection(
    session: Session,
    workflow: WorkflowRecord,
    plan: PlanRecord,
    discovery_record: DiscoverySpecRecord,
) -> DiscoveryAgentSelectionOut | None:
    """Read the last applied, integrity-bound Discovery choice for the UI."""

    events = session.scalars(
        select(EventRecord)
        .where(
            EventRecord.workflow_id == workflow.id,
            EventRecord.event_type == "agent.decision-applied",
        )
        .order_by(
            EventRecord.sequence.desc(),
            EventRecord.created_at.desc(),
            EventRecord.id.desc(),
        )
    )
    for event in events:
        try:
            event_data = AgentDecisionEventData.model_validate(
                event.payload,
                strict=True,
            )
        except ValidationError:
            continue
        selection = event_data.discovery_selection
        selection_sha256 = event_data.discovery_selection_sha256
        if selection is None or selection_sha256 is None:
            continue
        selection_payload = selection.model_dump(mode="json", by_alias=True)
        if content_sha256(selection_payload) != selection_sha256:
            continue
        decision = session.get(AgentDecisionRecord, event_data.decision_id)
        if (
            decision is None
            or decision.workflow_id != workflow.id
            or decision.status != "applied"
            or decision.action != "continue"
            or decision.target_step_key != selection.selected_step_key
            or selection.workflow_id != workflow.id
            or selection.plan_id != plan.id
            or selection.plan_sha256 != plan.spec_sha256
            or plan.status != "approved"
            or selection.discovery_spec_id != discovery_record.id
            or selection.discovery_spec_revision != discovery_record.revision
            or selection.discovery_spec_sha256 != discovery_record.spec_sha256
            or discovery_record.status != "approved"
        ):
            continue
        proposed_events = [
            proposed
            for proposed in session.scalars(
                select(EventRecord).where(
                    EventRecord.workflow_id == workflow.id,
                    EventRecord.event_type == "agent.decision-proposed",
                )
            )
            if proposed.payload.get("decisionId") == decision.id
        ]
        if len(proposed_events) != 1:
            continue
        try:
            proposed_data = AgentDecisionEventData.model_validate(
                proposed_events[0].payload,
                strict=True,
            )
        except ValidationError:
            continue
        if (
            proposed_data.observation_id != decision.observation_id
            or event_data.observation_id != decision.observation_id
            or proposed_data.action != decision.action
            or event_data.action != decision.action
            or proposed_data.target_step_key != decision.target_step_key
            or event_data.target_step_key != decision.target_step_key
            or proposed_data.expected_workflow_revision
            != decision.expected_workflow_revision
            or event_data.expected_workflow_revision
            != decision.expected_workflow_revision
            or proposed_data.reason_code != decision.reason_code
            or event_data.reason_code != decision.reason_code
            or proposed_data.discovery_selection != selection
            or proposed_data.discovery_selection_sha256 != selection_sha256
        ):
            continue
        operations = selection.eligible_operations
        selected_operation_key = selection.selected_operation_key
        selected = [
            item
            for item in operations
            if item.operation_key == selected_operation_key
            and item.rank == 1
            and item.step_key == selection.selected_step_key
        ]
        if len(selected) != 1:
            continue
        signal = selected[0]
        return DiscoveryAgentSelectionOut(
            decision_id=decision.id,
            selected_operation_key=selected_operation_key,
            selected_step_key=selection.selected_step_key,
            query_id=signal.query_id,
            provider=signal.provider,
            reason_code=selection.reason_code,
            eligible_operation_count=len(operations),
            query_attempt_count=signal.query_attempt_count,
            provider_attempt_count=signal.provider_attempt_count,
            query_no_novelty_count=signal.query_no_novelty_count,
            query_novel_candidate_count=signal.query_novel_candidate_count,
            query_duplicate_count=signal.query_duplicate_count,
            selection_snapshot_sha256=selection.selection_snapshot_sha256,
        )
    return None


def _operation_progress(
    *,
    query_id: str,
    provider: DiscoveryProvider,
    operation_key: str,
    invocation: ToolInvocationRecord | None,
) -> DiscoveryOperationProgressOut:
    if invocation is None:
        return DiscoveryOperationProgressOut(
            operation_key=operation_key,
            query_id=cast(Any, query_id),
            provider=provider,
            status="not-started",
            attempt=None,
            invocation_id=None,
            returned_count=0,
            novel_candidate_count=0,
            duplicate_count=0,
            candidate_set_sha256=None,
            error_code=None,
            retry_classification=None,
            created_at=None,
            finished_at=None,
        )
    return DiscoveryOperationProgressOut(
        operation_key=operation_key,
        query_id=cast(Any, query_id),
        provider=provider,
        status=cast(Any, invocation.status),
        attempt=invocation.attempt,
        invocation_id=invocation.id,
        returned_count=int(invocation.returned_count or 0),
        novel_candidate_count=int(invocation.novel_candidate_count or 0),
        duplicate_count=int(invocation.duplicate_count or 0),
        candidate_set_sha256=cast(Any, invocation.candidate_set_sha256),
        error_code=invocation.error_code,
        retry_classification=_retry_classification(invocation),
        created_at=invocation.created_at,
        finished_at=invocation.finished_at,
    )


def _retry_classification(
    invocation: ToolInvocationRecord,
) -> DiscoveryRetryClassification | None:
    if invocation.status == "prepared":
        return "safe-to-retry"
    if invocation.status in {"pending", "outcome-unknown"}:
        return "manual-review"
    if invocation.status == "failed":
        return (
            "safe-to-retry"
            if invocation.error_code
            in {"connector-unavailable", "rate-limited", "prepared-not-sent"}
            else "never-retry"
        )
    if invocation.status == "cancelled":
        return "never-retry"
    return None


def discovery_candidate_from_record(
    record: DiscoveryCandidateRecord,
) -> DiscoveryCandidate:
    """Validate either persisted candidate envelope without weakening its identity."""

    metadata = record.metadata_json
    candidate_payload: object = metadata
    if (
        set(metadata) == {"candidate", "trustClassification"}
        and metadata.get("trustClassification") == "untrusted-metadata"
    ):
        candidate_payload = metadata["candidate"]
    try:
        candidate = DiscoveryCandidate.model_validate(candidate_payload)
    except ValidationError as error:
        raise ValueError("Discovery candidate metadata is invalid") from error
    if (
        candidate.provider != record.provider
        or candidate.provider_id != record.provider_id
        or discovery_candidate_sha256(candidate) != record.candidate_sha256
    ):
        raise ValueError("Discovery candidate identity changed")
    return candidate


def _candidate_out(
    record: DiscoveryCandidateRecord | None,
    occurrences: list[DiscoveryCandidateOccurrenceOut],
    attached_source_id: str | None = None,
) -> DiscoveryCandidateOut:
    if record is None or not occurrences:
        raise WorkflowConflict(
            "discovery-candidate-integrity-invalid",
            "A workflow candidate occurrence has no project-owned candidate record.",
        )
    try:
        candidate = discovery_candidate_from_record(record)
    except ValueError:
        raise WorkflowConflict(
            "discovery-candidate-integrity-invalid",
            "A stored discovery candidate no longer satisfies its bounded metadata schema.",
        ) from None
    return DiscoveryCandidateOut(
        id=record.id,
        provider=candidate.provider,
        provider_id=candidate.provider_id,
        title=candidate.title,
        authors=candidate.authors,
        abstract=candidate.abstract,
        publication_date=candidate.publication_date,
        doi=candidate.doi,
        arxiv_id=candidate.arxiv_id,
        pmid=candidate.pmid,
        candidate_sha256=record.candidate_sha256,
        trust_classification="untrusted-metadata",
        full_text_verification="not-verified",
        import_availability="manual-pdf-required",
        landing_page_availability=(
            "reported" if candidate.landing_url is not None else "not-reported"
        ),
        open_access_pdf_availability=(
            "reported" if candidate.open_access_pdf_url is not None else "not-reported"
        ),
        attachment_status=(
            "verified-local-source" if attached_source_id is not None else "manual-pdf-required"
        ),
        attached_source_id=attached_source_id,
        occurrences=occurrences,
    )


__all__ = ("start_discovery_run", "workflow_discovery_snapshot")
