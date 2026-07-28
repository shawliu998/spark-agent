"""Small, deterministic project-scoped research-memory read/write boundary."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import (
    AgentContextSnapshotRecord,
    EventRecord,
    EvidenceSpanRecord,
    InteractionRequestRecord,
    PlanRecord,
    ResearchMemoryRecord,
    SourceRecord,
    StepObservationRecord,
    UserResponseRecord,
    WorkflowRecord,
    utc_now,
)
from ._service.integrity import WorkflowConflict, content_sha256
from .agent_loop.schemas import StepObservation, step_observation_sha256
from .evidence_coverage import assert_verified_evidence_span_current
from .research_memory_schemas import (
    MemoryAction,
    MemoryContextReasonCode,
    ResearchMemoryContextOut,
    ResearchMemoryOut,
    ResearchMemoryWorkspaceCounts,
    ResearchMemoryWorkspaceItemOut,
    ResearchMemoryWorkspaceOut,
    VerifiedEpisodeOut,
)
from .state import WorkflowFailure

MEMORY_MAX_ITEMS = 12
MEMORY_MAX_BYTES = 12_000
_TYPE_ORDER = {
    "user-decision": 0,
    "open-question": 1,
    "assumption": 2,
    "failure-lesson": 3,
    "operational-fact": 4,
}
_OBSERVATION_CANDIDATE_CREATOR = "step-observation-candidate-v1"
_USER_DECISION_CREATOR = "interaction-response-v1"
_REMEMBERED_EVIDENCE_CREATOR = "remembered-evidence-action-v1"
_REMEMBERED_EVIDENCE_RULE = (
    "Invalidated when the bound local source or verified evidence passage changes."
)
_REMEMBERED_EVIDENCE_EPISODE_EVENT = "research-memory.remembered-evidence-verified"


@dataclass(frozen=True, slots=True)
class _MemoryDependencyState:
    eligible: bool
    reason_code: MemoryContextReasonCode | None
    projection: dict[str, object]
    bound: bool

    @property
    def sha256(self) -> str:
        return content_sha256(self.projection)


def create_evidence_memory_candidate(
    session: Session,
    workflow: WorkflowRecord,
    *,
    evidence_id: str,
    expected_source_content_hash: str,
    expected_quote_hash: str,
    after_candidate_hook: Callable[[], None] | None = None,
) -> tuple[ResearchMemoryRecord, str, VerifiedEpisodeOut]:
    """Create one reviewable citation-memory candidate from verified local evidence."""
    evidence = session.get(EvidenceSpanRecord, evidence_id)
    source = (
        session.scalar(
            select(SourceRecord).where(
                SourceRecord.id == evidence.source_id,
                SourceRecord.project_id == workflow.project_id,
            )
        )
        if evidence is not None
        else None
    )
    if evidence is None or source is None:
        raise WorkflowFailure(
            "research-memory-evidence-missing",
            "The verified evidence passage is not available in this project.",
        )
    if (
        source.ingestion_status != "ready"
        or source.content_hash != expected_source_content_hash
    ):
        raise WorkflowFailure(
            "research-memory-source-stale",
            "The local source changed before the evidence could be remembered.",
            retryable=True,
        )
    if not evidence.verified or evidence.quote_hash != expected_quote_hash:
        raise WorkflowFailure(
            "research-memory-evidence-stale",
            "The evidence passage changed before it could be remembered.",
            retryable=True,
        )
    try:
        assert_verified_evidence_span_current(session, workflow, evidence)
    except WorkflowConflict as error:
        raise WorkflowFailure(
            "research-memory-evidence-invalid",
            "The evidence passage no longer matches its local source page.",
        ) from error

    subject_key = f"remembered-evidence:{workflow.id}:{evidence.id}"
    content: dict[str, object] = {
        "schemaVersion": "1",
        "kind": "remembered-evidence",
        "evidenceId": evidence.id,
        "sourceId": source.id,
        "pageIndex": evidence.page_index,
        "quoteHash": evidence.quote_hash,
        "claimStatus": "not-a-claim",
    }
    source_refs = [
        {"id": source.id, "sha256": source.content_hash, "type": "source"},
        {"id": evidence.id, "sha256": evidence.quote_hash, "type": "evidence"},
    ]
    material = _memory_material(
        artifact_refs=[],
        content=content,
        invalidation_rule=_REMEMBERED_EVIDENCE_RULE,
        scope_workflow_id=workflow.id,
        source_refs=source_refs,
        subject_key=subject_key,
        memory_type="user-decision",
    )
    memory_sha256 = content_sha256(material)
    head = session.scalar(
        select(ResearchMemoryRecord)
        .where(
            ResearchMemoryRecord.project_id == workflow.project_id,
            ResearchMemoryRecord.scope_workflow_id == workflow.id,
            ResearchMemoryRecord.subject_key == subject_key,
        )
        .order_by(
            ResearchMemoryRecord.revision.desc(),
            ResearchMemoryRecord.id.desc(),
        )
        .limit(1)
    )
    if head is not None and head.status in {"candidate", "committed"}:
        _assert_remembered_evidence_provenance(session, workflow, head)
        if head.memory_sha256 != memory_sha256:
            raise WorkflowFailure(
                "research-memory-idempotency-conflict",
                "The remembered evidence identity no longer matches its current passage.",
            )
        memory = head
        outcome = "already-remembered"
    else:
        revision = 1 if head is None else head.revision + 1
        creation_key = _remembered_evidence_creation_key(
            workflow,
            evidence.id,
            evidence.quote_hash,
            revision,
        )
        existing = session.scalar(
            select(ResearchMemoryRecord).where(
                ResearchMemoryRecord.project_id == workflow.project_id,
                ResearchMemoryRecord.creation_key == creation_key,
            )
        )
        if existing is not None:
            _assert_remembered_evidence_provenance(session, workflow, existing)
            memory = existing
            outcome = (
                "already-remembered"
                if existing.status in {"candidate", "committed"}
                else "candidate-reopened"
            )
        else:
            memory = ResearchMemoryRecord(
                id=str(uuid.uuid4()),
                project_id=workflow.project_id,
                scope_workflow_id=workflow.id,
                subject_key=subject_key,
                revision=revision,
                previous_id=head.id if head is not None else None,
                schema_version="1",
                type="user-decision",
                content_json=content,
                source_refs=source_refs,
                artifact_refs=[],
                invalidation_rule=_REMEMBERED_EVIDENCE_RULE,
                status="candidate",
                created_by=_REMEMBERED_EVIDENCE_CREATOR,
                creation_key=creation_key,
                memory_sha256=memory_sha256,
            )
            session.add(memory)
            session.flush()
            _assert_remembered_evidence_provenance(session, workflow, memory)
            outcome = "candidate-created" if head is None else "candidate-reopened"
    if after_candidate_hook is not None:
        after_candidate_hook()
    episode = _get_or_create_remembered_evidence_episode(
        session,
        workflow,
        source=source,
        evidence=evidence,
        memory=memory,
    )
    return memory, outcome, episode


def commit_user_response_memory(
    session: Session,
    workflow: WorkflowRecord,
    response: UserResponseRecord,
) -> ResearchMemoryRecord:
    """Commit one immutable decision revision per durable UserResponse."""
    creation_key = f"user-response:{response.id}"
    subject_key = f"interaction-response:{response.interaction_id}"
    content = {
        "response": response.response_json,
        "responseId": response.id,
        "responseRevision": response.revision,
    }
    source_refs = [{"id": response.id, "sha256": response.response_sha256, "type": "user-response"}]
    material = _memory_material(
        artifact_refs=[],
        content=content,
        invalidation_rule="Superseded only by a later response to this interaction.",
        scope_workflow_id=workflow.id,
        source_refs=source_refs,
        subject_key=subject_key,
        memory_type="user-decision",
    )
    existing = session.scalar(
        select(ResearchMemoryRecord).where(
            ResearchMemoryRecord.project_id == workflow.project_id,
            ResearchMemoryRecord.creation_key == creation_key,
        )
    )
    if existing is not None:
        if (
            existing.scope_workflow_id != workflow.id
            or existing.subject_key != subject_key
            or existing.revision != response.revision
            or existing.status != "committed"
            or existing.memory_sha256 != content_sha256(material)
        ):
            raise WorkflowFailure(
                "research-memory-idempotency-conflict",
                "The stored research memory does not match the durable user response.",
            )
        _assert_memory_integrity(existing)
        return existing
    prior_revisions = list(
        session.scalars(
            select(ResearchMemoryRecord)
            .where(
                ResearchMemoryRecord.project_id == workflow.project_id,
                ResearchMemoryRecord.scope_workflow_id == workflow.id,
                ResearchMemoryRecord.subject_key == subject_key,
            )
            .order_by(ResearchMemoryRecord.revision.desc(), ResearchMemoryRecord.id.desc())
        )
    )
    previous = prior_revisions[0] if prior_revisions else None
    for prior in prior_revisions:
        if prior.status == "committed":
            prior.status = "superseded"
    record = ResearchMemoryRecord(
        id=str(uuid.uuid4()),
        project_id=workflow.project_id,
        scope_workflow_id=workflow.id,
        subject_key=subject_key,
        revision=response.revision,
        previous_id=previous.id if previous is not None else None,
        schema_version="1",
        type="user-decision",
        content_json=content,
        source_refs=source_refs,
        artifact_refs=[],
        invalidation_rule="Superseded only by a later response to this interaction.",
        status="committed",
        created_by="interaction-response-v1",
        creation_key=creation_key,
        memory_sha256=content_sha256(material),
    )
    session.add(record)
    session.flush()
    return record


def create_observation_memory_candidates(
    session: Session,
    workflow: WorkflowRecord,
    observation: StepObservationRecord,
) -> tuple[ResearchMemoryRecord, ...]:
    """Create only deterministic question/failure candidates from verified structure."""
    value = _observation_value(observation, workflow)
    candidates: list[ResearchMemoryRecord] = []
    for question in value.unresolved_questions:
        candidates.append(
            _create_observation_candidate(
                session,
                workflow,
                observation,
                creation_key=(
                    f"step-observation:{observation.id}:open-question:{question.code}"
                ),
                subject_key=(
                    f"open-question:{workflow.id}:{value.step_key}:{question.code}"
                ),
                memory_type="open-question",
                content={
                    "answerType": question.answer_type,
                    "code": question.code,
                    "question": question.question,
                    "stepKey": value.step_key,
                },
                invalidation_rule="Invalidated when the question is answered or no longer applies.",
            )
        )
    if (
        value.status in {"failed", "blocked"}
        and observation.generator == "deterministic-observer-v1"
        and observation.model_invocation_id is None
    ):
        primary_warning_code = (
            value.warnings[0].code if value.warnings else "no-primary-warning"
        )
        failure_signature = f"{value.failure_category}:{primary_warning_code}"
        candidates.append(
            _create_observation_candidate(
                session,
                workflow,
                observation,
                creation_key=f"step-observation:{observation.id}:failure-lesson",
                subject_key=(
                    f"failure-lesson:{workflow.id}:{value.step_key}:"
                    f"{failure_signature}"
                ),
                memory_type="failure-lesson",
                content={
                    "attempt": value.attempt,
                    "failureCategory": value.failure_category,
                    "recommendedActions": list(value.recommended_actions),
                    "status": value.status,
                    "stepKey": value.step_key,
                    "warnings": [
                        warning.model_dump(mode="json", by_alias=True)
                        for warning in value.warnings
                    ],
                },
                invalidation_rule="Invalidated when the failure condition or bounded method changes.",
            )
        )
    return tuple(candidates)


def list_workflow_memories(
    session: Session,
    workflow: WorkflowRecord,
) -> tuple[ResearchMemoryRecord, ...]:
    records = tuple(
        session.scalars(
            select(ResearchMemoryRecord)
            .where(
                ResearchMemoryRecord.project_id == workflow.project_id,
                ResearchMemoryRecord.scope_workflow_id == workflow.id,
            )
            .order_by(
                ResearchMemoryRecord.created_at.desc(),
                ResearchMemoryRecord.id.desc(),
            )
        )
    )
    for memory in records:
        _assert_memory_integrity(memory)
        _assert_memory_lineage(session, workflow, memory)
        _assert_reviewable_memory_provenance(session, workflow, memory)
    return records


def begin_memory_read_snapshot(session: Session) -> None:
    """Start the SQLite read snapshot before the first workspace SELECT."""

    if session.get_bind().dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN DEFERRED")


def begin_memory_write_transaction(session: Session) -> None:
    """Serialize SQLite Memory writers before reading mutation guards."""

    if session.get_bind().dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")


def resolve_memory_candidate(
    session: Session,
    workflow: WorkflowRecord,
    memory: ResearchMemoryRecord,
    *,
    decision: str,
    expected_content_hash: str,
    expected_status: str,
    expected_revision: int,
    expected_subject_head_id: str,
    expected_subject_head_revision: int,
) -> ResearchMemoryRecord:
    target = "committed" if decision == "accept" else "rejected"
    if decision == "accept" and memory.status == "committed":
        _assert_memory_target(session, workflow, memory, expected_content_hash)
        head = session.execute(
            select(ResearchMemoryRecord.id, ResearchMemoryRecord.revision)
            .where(
                ResearchMemoryRecord.project_id == workflow.project_id,
                ResearchMemoryRecord.scope_workflow_id == workflow.id,
                ResearchMemoryRecord.subject_key == memory.subject_key,
            )
            .order_by(
                ResearchMemoryRecord.revision.desc(),
                ResearchMemoryRecord.id.desc(),
            )
            .limit(1)
        ).one_or_none()
        if (
            expected_status == "candidate"
            and memory.revision == expected_revision
            and head is not None
            and head.id == expected_subject_head_id
            and head.revision == expected_subject_head_revision
        ):
            _assert_reviewable_candidate_provenance(session, workflow, memory)
            return memory
    _assert_memory_mutation_guard(
        session,
        workflow,
        memory,
        expected_content_hash=expected_content_hash,
        expected_status=expected_status,
        expected_revision=expected_revision,
        expected_subject_head_id=expected_subject_head_id,
        expected_subject_head_revision=expected_subject_head_revision,
    )
    _assert_reviewable_candidate_provenance(session, workflow, memory)
    if target == "committed":
        for prior in session.scalars(
            select(ResearchMemoryRecord).where(
                ResearchMemoryRecord.project_id == workflow.project_id,
                ResearchMemoryRecord.scope_workflow_id == workflow.id,
                ResearchMemoryRecord.subject_key == memory.subject_key,
                ResearchMemoryRecord.id != memory.id,
                ResearchMemoryRecord.status == "committed",
            )
        ):
            _assert_memory_integrity(prior)
            prior.status = "superseded"
            prior.updated_at = utc_now()
        # Release the partial unique committed-subject slot before promoting
        # the new revision. Both flushes remain inside the caller's transaction.
        session.flush()
    memory.status = target
    memory.updated_at = utc_now()
    session.flush()
    return memory


def invalidate_memory(
    session: Session,
    workflow: WorkflowRecord,
    memory: ResearchMemoryRecord,
    *,
    expected_content_hash: str,
    expected_status: str,
    expected_revision: int,
    expected_subject_head_id: str,
    expected_subject_head_revision: int,
) -> ResearchMemoryRecord:
    _assert_memory_mutation_guard(
        session,
        workflow,
        memory,
        expected_content_hash=expected_content_hash,
        expected_status=expected_status,
        expected_revision=expected_revision,
        expected_subject_head_id=expected_subject_head_id,
        expected_subject_head_revision=expected_subject_head_revision,
    )
    memory.status = "invalidated"
    memory.updated_at = utc_now()
    session.flush()
    return memory


def get_research_memory_workspace(
    session: Session,
    workflow: WorkflowRecord,
) -> ResearchMemoryWorkspaceOut:
    """Read one integrity-checked, project/workflow-scoped Memory workspace."""

    records = list_workflow_memories(session, workflow)
    latest_snapshot = session.scalar(
        select(AgentContextSnapshotRecord)
        .where(AgentContextSnapshotRecord.workflow_id == workflow.id)
        .order_by(
            AgentContextSnapshotRecord.created_at.desc(),
            AgentContextSnapshotRecord.id.desc(),
        )
    )
    selected_ids: set[str] = set()
    if latest_snapshot is not None:
        _assert_snapshot_integrity(
            session,
            latest_snapshot,
            workflow,
            latest_snapshot.plan_id,
            latest_snapshot.observation_id,
        )
        selected_ids = {
            cast(str, ref["id"])
            for ref in latest_snapshot.selected_memory_refs
        }
    items: list[ResearchMemoryWorkspaceItemOut] = []
    subject_heads: dict[str, ResearchMemoryRecord] = {}
    for memory in records:
        head = subject_heads.get(memory.subject_key)
        if head is None or memory.revision > head.revision:
            subject_heads[memory.subject_key] = memory
    counts = {status: 0 for status in (
        "candidate",
        "committed",
        "rejected",
        "superseded",
        "invalidated",
    )}
    dependency_states = {
        memory.id: _memory_dependency_state(session, workflow, memory)
        for memory in records
        if memory.status == "committed"
    }
    for memory in records:
        counts[memory.status] += 1
        context = _memory_context_state(
            memory,
            latest_snapshot,
            selected_ids,
            dependency_states.get(memory.id),
        )
        memory_out = ResearchMemoryOut.model_validate(memory)
        items.append(
            ResearchMemoryWorkspaceItemOut.model_validate(
                {
                    **memory_out.model_dump(mode="python", by_alias=True),
                    "subjectHeadId": subject_heads[memory.subject_key].id,
                    "subjectHeadRevision": (
                        subject_heads[memory.subject_key].revision
                    ),
                    "availableActions": _available_memory_actions(memory.status),
                    "context": context.model_dump(mode="python", by_alias=True),
                }
            )
        )
    count_model = ResearchMemoryWorkspaceCounts.model_validate(counts)
    material: dict[str, object] = {
        "schemaVersion": "1",
        "projectId": workflow.project_id,
        "workflowId": workflow.id,
        "latestContextSnapshotId": (
            latest_snapshot.id if latest_snapshot is not None else None
        ),
        "latestContextSnapshotSha256": (
            latest_snapshot.context_sha256 if latest_snapshot is not None else None
        ),
        "counts": count_model.model_dump(mode="json", by_alias=True),
        "items": [
            item.model_dump(mode="json", by_alias=True)
            for item in items
        ],
    }
    return ResearchMemoryWorkspaceOut(
        schema_version="1",
        project_id=workflow.project_id,
        workflow_id=workflow.id,
        latest_context_snapshot_id=(
            latest_snapshot.id if latest_snapshot is not None else None
        ),
        latest_context_snapshot_sha256=(
            latest_snapshot.context_sha256 if latest_snapshot is not None else None
        ),
        counts=count_model,
        items=items,
        workspace_sha256=content_sha256(material),
    )


def _available_memory_actions(status: str) -> list[MemoryAction]:
    if status == "candidate":
        return ["accept", "reject"]
    if status == "committed":
        return ["invalidate"]
    return []


def _memory_context_state(
    memory: ResearchMemoryRecord,
    latest_snapshot: AgentContextSnapshotRecord | None,
    selected_ids: set[str],
    dependency_state: _MemoryDependencyState | None,
) -> ResearchMemoryContextOut:
    snapshot_id = latest_snapshot.id if latest_snapshot is not None else None
    snapshot_sha256 = (
        latest_snapshot.context_sha256 if latest_snapshot is not None else None
    )
    if memory.status != "committed":
        reason_by_status = {
            "candidate": "candidate-excluded",
            "rejected": "rejected-excluded",
            "superseded": "superseded-excluded",
            "invalidated": "invalidated-excluded",
        }
        return ResearchMemoryContextOut(
            state="excluded",
            reason_code=cast(
                MemoryContextReasonCode,
                reason_by_status[memory.status],
            ),
            snapshot_id=snapshot_id,
            snapshot_sha256=snapshot_sha256,
        )
    if dependency_state is not None and not dependency_state.eligible:
        return ResearchMemoryContextOut(
            state="excluded",
            reason_code=cast(MemoryContextReasonCode, dependency_state.reason_code),
            snapshot_id=snapshot_id,
            snapshot_sha256=snapshot_sha256,
        )
    if latest_snapshot is not None and memory.id in selected_ids:
        return ResearchMemoryContextOut(
            state="selected",
            reason_code="selected-in-latest-snapshot",
            snapshot_id=latest_snapshot.id,
            snapshot_sha256=latest_snapshot.context_sha256,
        )
    if latest_snapshot is None or memory.updated_at > latest_snapshot.created_at:
        return ResearchMemoryContextOut(
            state="eligible",
            reason_code="eligible-for-future-snapshot",
            snapshot_id=snapshot_id,
            snapshot_sha256=snapshot_sha256,
        )
    return ResearchMemoryContextOut(
        state="excluded",
        reason_code="bounded-context-excluded",
        snapshot_id=latest_snapshot.id,
        snapshot_sha256=latest_snapshot.context_sha256,
    )


def get_or_create_context_snapshot(
    session: Session,
    workflow: WorkflowRecord,
    *,
    plan_id: str | None,
    observation_id: str,
) -> AgentContextSnapshotRecord:
    """Freeze the bounded selected context once for an observation/recovery identity."""
    observation = session.scalar(
        select(StepObservationRecord).where(
            StepObservationRecord.workflow_id == workflow.id,
            StepObservationRecord.id == observation_id,
        )
    )
    if observation is None or observation.plan_id != plan_id:
        raise WorkflowFailure(
            "research-context-identity-invalid",
            "The research context does not match its observation and plan.",
        )
    if plan_id is not None:
        plan = session.scalar(
            select(PlanRecord).where(
                PlanRecord.workflow_id == workflow.id,
                PlanRecord.id == plan_id,
            )
        )
        if plan is None:
            raise WorkflowFailure(
                "research-context-identity-invalid",
                "The research context plan is missing from this workflow.",
            )
    candidates = list(
        session.scalars(
            select(ResearchMemoryRecord).where(
                ResearchMemoryRecord.project_id == workflow.project_id,
                ResearchMemoryRecord.status == "committed",
                or_(
                    ResearchMemoryRecord.scope_workflow_id.is_(None),
                    ResearchMemoryRecord.scope_workflow_id == workflow.id,
                ),
            )
        )
    )
    candidates.sort(key=lambda item: (_TYPE_ORDER[item.type], item.created_at, item.id))
    dependency_states: dict[str, _MemoryDependencyState] = {}
    for memory in candidates:
        _assert_memory_integrity(memory)
        dependency_states[memory.id] = _memory_dependency_state(
            session,
            workflow,
            memory,
        )
    context_generation_sha256 = _context_generation_sha256(
        candidates,
        dependency_states,
    )
    existing_snapshots = list(
        session.scalars(
            select(AgentContextSnapshotRecord)
            .where(
                AgentContextSnapshotRecord.workflow_id == workflow.id,
                AgentContextSnapshotRecord.observation_id == observation_id,
            )
            .order_by(
                AgentContextSnapshotRecord.created_at,
                AgentContextSnapshotRecord.id,
            )
        )
    )
    for existing in existing_snapshots:
        _assert_snapshot_integrity(session, existing, workflow, plan_id, observation_id)
        existing_generation = (
            existing.context_generation_sha256
            if existing.schema_version == "2"
            else _legacy_context_generation_sha256(
                session,
                existing.selected_memory_refs,
            )
        )
        if existing_generation == context_generation_sha256:
            return existing
    selected: list[dict[str, object]] = []
    for memory in candidates:
        if not dependency_states[memory.id].eligible:
            continue
        item: dict[str, object] = {
            "content": memory.content_json,
            "id": memory.id,
            "memorySha256": memory.memory_sha256,
            "sourceRefs": memory.source_refs,
            "type": memory.type,
        }
        proposed = _context_payload(
            [*selected, item],
            context_generation_sha256=context_generation_sha256,
        )
        if (
            len(selected) >= MEMORY_MAX_ITEMS
            or len(_canonical_json(proposed).encode("utf-8")) > MEMORY_MAX_BYTES
        ):
            continue
        selected.append(item)
    context = _context_payload(
        selected,
        context_generation_sha256=context_generation_sha256,
    )
    refs = _selected_refs(selected)
    record = AgentContextSnapshotRecord(
        id=str(uuid.uuid4()),
        project_id=workflow.project_id,
        workflow_id=workflow.id,
        plan_id=plan_id,
        observation_id=observation_id,
        schema_version="2",
        selection_version=1,
        max_items=MEMORY_MAX_ITEMS,
        max_bytes=MEMORY_MAX_BYTES,
        context_json=context,
        context_sha256=content_sha256(context),
        context_generation_sha256=context_generation_sha256,
        selected_memory_refs=refs,
    )
    session.add(record)
    session.flush()
    _assert_snapshot_integrity(session, record, workflow, plan_id, observation_id)
    return record


def decision_context_payload(snapshot: AgentContextSnapshotRecord) -> dict[str, object]:
    return {
        "id": snapshot.id,
        "sha256": snapshot.context_sha256,
        "items": snapshot.context_json["items"],
    }


def _create_observation_candidate(
    session: Session,
    workflow: WorkflowRecord,
    observation: StepObservationRecord,
    *,
    creation_key: str,
    subject_key: str,
    memory_type: str,
    content: dict[str, object],
    invalidation_rule: str,
) -> ResearchMemoryRecord:
    source_refs = [
        {
            "id": observation.id,
            "sha256": observation.output_sha256,
            "type": "step-observation",
        }
    ]
    material = _memory_material(
        artifact_refs=[],
        content=content,
        invalidation_rule=invalidation_rule,
        scope_workflow_id=workflow.id,
        source_refs=source_refs,
        subject_key=subject_key,
        memory_type=memory_type,
    )
    memory_sha256 = content_sha256(material)
    existing = session.scalar(
        select(ResearchMemoryRecord).where(
            ResearchMemoryRecord.project_id == workflow.project_id,
            ResearchMemoryRecord.creation_key == creation_key,
        )
    )
    if existing is not None:
        _assert_memory_integrity(existing)
        if (
            existing.scope_workflow_id != workflow.id
            or existing.subject_key != subject_key
            or existing.type != memory_type
            or existing.memory_sha256 != memory_sha256
            or existing.created_by != _OBSERVATION_CANDIDATE_CREATOR
        ):
            raise WorkflowFailure(
                "research-memory-idempotency-conflict",
                "The stored memory candidate does not match its observation.",
            )
        _assert_observation_candidate_provenance(session, workflow, existing)
        return existing
    prior_revisions = list(
        session.scalars(
            select(ResearchMemoryRecord)
            .where(
                ResearchMemoryRecord.project_id == workflow.project_id,
                ResearchMemoryRecord.scope_workflow_id == workflow.id,
                ResearchMemoryRecord.subject_key == subject_key,
            )
            .order_by(
                ResearchMemoryRecord.revision.desc(),
                ResearchMemoryRecord.id.desc(),
            )
        )
    )
    previous = prior_revisions[0] if prior_revisions else None
    revision = (
        session.scalar(
            select(func.max(ResearchMemoryRecord.revision)).where(
                ResearchMemoryRecord.project_id == workflow.project_id,
                ResearchMemoryRecord.scope_workflow_id == workflow.id,
                ResearchMemoryRecord.subject_key == subject_key,
            )
        )
        or 0
    ) + 1
    for prior in prior_revisions:
        _assert_memory_integrity(prior)
        if prior.status == "candidate":
            prior.status = "superseded"
            prior.updated_at = utc_now()
    record = ResearchMemoryRecord(
        id=str(uuid.uuid4()),
        project_id=workflow.project_id,
        scope_workflow_id=workflow.id,
        subject_key=subject_key,
        revision=revision,
        previous_id=previous.id if previous is not None else None,
        schema_version="1",
        type=memory_type,
        content_json=content,
        source_refs=source_refs,
        artifact_refs=[],
        invalidation_rule=invalidation_rule,
        status="candidate",
        created_by=_OBSERVATION_CANDIDATE_CREATOR,
        creation_key=creation_key,
        memory_sha256=memory_sha256,
    )
    session.add(record)
    session.flush()
    return record


def _assert_memory_target(
    session: Session,
    workflow: WorkflowRecord,
    memory: ResearchMemoryRecord,
    expected_content_hash: str,
) -> None:
    if (
        memory.project_id != workflow.project_id
        or memory.scope_workflow_id != workflow.id
    ):
        raise WorkflowFailure(
            "research-memory-scope-invalid",
            "The research memory does not belong to this project workflow.",
        )
    _assert_memory_integrity(memory)
    _assert_memory_lineage(session, workflow, memory)
    if memory.memory_sha256 != expected_content_hash:
        raise WorkflowFailure(
            "research-memory-content-stale",
            "The research memory content changed before this review action.",
            retryable=True,
        )


def _assert_memory_mutation_guard(
    session: Session,
    workflow: WorkflowRecord,
    memory: ResearchMemoryRecord,
    *,
    expected_content_hash: str,
    expected_status: str,
    expected_revision: int,
    expected_subject_head_id: str,
    expected_subject_head_revision: int,
) -> None:
    _assert_memory_target(session, workflow, memory, expected_content_hash)
    head = session.execute(
        select(ResearchMemoryRecord.id, ResearchMemoryRecord.revision)
        .where(
            ResearchMemoryRecord.project_id == workflow.project_id,
            ResearchMemoryRecord.scope_workflow_id == workflow.id,
            ResearchMemoryRecord.subject_key == memory.subject_key,
        )
        .order_by(
            ResearchMemoryRecord.revision.desc(),
            ResearchMemoryRecord.id.desc(),
        )
        .limit(1)
    ).one_or_none()
    if (
        memory.status != expected_status
        or memory.revision != expected_revision
        or head is None
        or head.id != expected_subject_head_id
        or head.revision != expected_subject_head_revision
    ):
        raise WorkflowFailure(
            "research-memory-state-stale",
            "The research memory state changed before this review action.",
            retryable=True,
        )


def _assert_memory_lineage(
    session: Session,
    workflow: WorkflowRecord,
    memory: ResearchMemoryRecord,
) -> None:
    if (
        memory.project_id != workflow.project_id
        or memory.scope_workflow_id != workflow.id
        or memory.revision < 1
    ):
        raise WorkflowFailure(
            "research-memory-provenance-invalid",
            "The research memory revision is outside this workflow lineage.",
        )
    if memory.revision == 1:
        if memory.previous_id is not None:
            raise WorkflowFailure(
                "research-memory-provenance-invalid",
                "The first research memory revision cannot have a predecessor.",
            )
        return
    predecessor = session.scalar(
        select(ResearchMemoryRecord).where(
            ResearchMemoryRecord.project_id == workflow.project_id,
            ResearchMemoryRecord.scope_workflow_id == workflow.id,
            ResearchMemoryRecord.subject_key == memory.subject_key,
            ResearchMemoryRecord.revision == memory.revision - 1,
        )
    )
    if (
        predecessor is None
        or memory.previous_id != predecessor.id
        or predecessor.subject_key != memory.subject_key
        or predecessor.type != memory.type
        or predecessor.scope_workflow_id != memory.scope_workflow_id
    ):
        raise WorkflowFailure(
            "research-memory-provenance-invalid",
            "The research memory revision does not identify its exact predecessor.",
        )
    _assert_memory_integrity(predecessor)


def _assert_reviewable_memory_provenance(
    session: Session,
    workflow: WorkflowRecord,
    memory: ResearchMemoryRecord,
) -> None:
    if memory.created_by == _OBSERVATION_CANDIDATE_CREATOR:
        _assert_observation_candidate_provenance(session, workflow, memory)
    elif memory.created_by == _USER_DECISION_CREATOR:
        _assert_user_decision_provenance(session, workflow, memory)
    elif memory.created_by == _REMEMBERED_EVIDENCE_CREATOR:
        _assert_remembered_evidence_provenance(session, workflow, memory)
    else:
        raise WorkflowFailure(
            "research-memory-provenance-invalid",
            "The research memory does not have a supported producer.",
        )


def _assert_reviewable_candidate_provenance(
    session: Session,
    workflow: WorkflowRecord,
    memory: ResearchMemoryRecord,
) -> None:
    if memory.created_by == _OBSERVATION_CANDIDATE_CREATOR:
        _assert_observation_candidate_provenance(session, workflow, memory)
        return
    if memory.created_by == _REMEMBERED_EVIDENCE_CREATOR:
        _assert_remembered_evidence_provenance(session, workflow, memory)
        return
    raise WorkflowFailure(
        "research-memory-provenance-invalid",
        "The memory candidate does not have a supported review provenance.",
    )


def _remembered_evidence_creation_key(
    workflow: WorkflowRecord,
    evidence_id: str,
    quote_hash: str,
    revision: int,
) -> str:
    return (
        "remembered-evidence:v1:"
        + content_sha256(
            {
                "actionVersion": "1",
                "evidenceId": evidence_id,
                "projectId": workflow.project_id,
                "quoteHash": quote_hash,
                "revision": revision,
                "workflowId": workflow.id,
            }
        )
    )


def _remembered_evidence_episode_material(
    workflow: WorkflowRecord,
    *,
    source: SourceRecord,
    evidence: EvidenceSpanRecord,
    memory: ResearchMemoryRecord,
) -> dict[str, object]:
    return {
        "schemaVersion": "1",
        "action": _REMEMBERED_EVIDENCE_CREATOR,
        "actionVersion": "1",
        "projectId": workflow.project_id,
        "workflowId": workflow.id,
        "source": {
            "id": source.id,
            "contentHash": source.content_hash,
        },
        "evidence": {
            "id": evidence.id,
            "sourceId": evidence.source_id,
            "quoteHash": evidence.quote_hash,
            "verified": True,
        },
        "output": {
            "memoryCandidateId": memory.id,
            "revision": memory.revision,
            "memoryContentSha256": memory.memory_sha256,
            "createdStatus": "candidate",
        },
        "boundaries": {
            "createsClaim": False,
            "createsSource": False,
            "changesPermission": False,
            "changesDisclosure": False,
            "promotesContext": False,
        },
    }


def _remembered_evidence_episode_payload(
    workflow: WorkflowRecord,
    *,
    source: SourceRecord,
    evidence: EvidenceSpanRecord,
    memory: ResearchMemoryRecord,
) -> dict[str, object]:
    material = _remembered_evidence_episode_material(
        workflow,
        source=source,
        evidence=evidence,
        memory=memory,
    )
    episode_sha256 = content_sha256(material)
    episode_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"spark:verified-episode:{episode_sha256}",
        )
    )
    return {
        **material,
        "episodeId": episode_id,
        "episodeSha256": episode_sha256,
    }


def _episode_identity(payload: Mapping[str, object]) -> VerifiedEpisodeOut:
    return VerifiedEpisodeOut.model_validate(
        {
            "episodeId": payload.get("episodeId"),
            "episodeSha256": payload.get("episodeSha256"),
            "action": payload.get("action"),
            "schemaVersion": payload.get("schemaVersion"),
        },
        strict=True,
    )


def _get_or_create_remembered_evidence_episode(
    session: Session,
    workflow: WorkflowRecord,
    *,
    source: SourceRecord,
    evidence: EvidenceSpanRecord,
    memory: ResearchMemoryRecord,
) -> VerifiedEpisodeOut:
    payload = _remembered_evidence_episode_payload(
        workflow,
        source=source,
        evidence=evidence,
        memory=memory,
    )
    episode_id = cast(str, payload["episodeId"])
    events = tuple(
        session.scalars(
            select(EventRecord).where(
                EventRecord.project_id == workflow.project_id,
                EventRecord.workflow_id == workflow.id,
                EventRecord.event_type == _REMEMBERED_EVIDENCE_EPISODE_EVENT,
            )
        )
    )
    matches = [event for event in events if event.payload.get("episodeId") == episode_id]
    same_output = [
        event
        for event in events
        if isinstance(event.payload.get("output"), dict)
        and cast(dict[object, object], event.payload["output"]).get(
            "memoryCandidateId"
        )
        == memory.id
    ]
    if len(matches) > 1 or any(event not in matches for event in same_output):
        raise WorkflowFailure(
            "verified-episode-idempotency-conflict",
            "The verified episode identity conflicts with an existing action output.",
        )
    if matches:
        event = matches[0]
        if event.payload != payload:
            raise WorkflowFailure(
                "verified-episode-idempotency-conflict",
                "The stored verified episode does not match this immutable action output.",
            )
        return _verify_remembered_evidence_episode_event(
            session,
            workflow,
            event,
            expected_episode_id=episode_id,
        )
    event = EventRecord(
        id=str(uuid.uuid4()),
        project_id=workflow.project_id,
        workflow_id=workflow.id,
        event_type=_REMEMBERED_EVIDENCE_EPISODE_EVENT,
        payload=payload,
    )
    session.add(event)
    session.flush()
    return _verify_remembered_evidence_episode_event(
        session,
        workflow,
        event,
        expected_episode_id=episode_id,
    )


def get_and_verify_remembered_evidence_episode(
    session: Session,
    workflow: WorkflowRecord,
    episode_id: str,
) -> VerifiedEpisodeOut:
    events = tuple(
        session.scalars(
            select(EventRecord).where(
                EventRecord.project_id == workflow.project_id,
                EventRecord.workflow_id == workflow.id,
                EventRecord.event_type == _REMEMBERED_EVIDENCE_EPISODE_EVENT,
            )
        )
    )
    matches = [event for event in events if event.payload.get("episodeId") == episode_id]
    if len(matches) != 1:
        raise WorkflowFailure(
            "verified-episode-not-found",
            "The verified episode is missing or ambiguous in this project workflow.",
        )
    return _verify_remembered_evidence_episode_event(
        session,
        workflow,
        matches[0],
        expected_episode_id=episode_id,
    )


def _verify_remembered_evidence_episode_event(
    session: Session,
    workflow: WorkflowRecord,
    event: EventRecord,
    *,
    expected_episode_id: str,
) -> VerifiedEpisodeOut:
    payload = event.payload
    expected_keys = {
        "schemaVersion",
        "action",
        "actionVersion",
        "projectId",
        "workflowId",
        "source",
        "evidence",
        "output",
        "boundaries",
        "episodeId",
        "episodeSha256",
    }
    source_value = payload.get("source")
    evidence_value = payload.get("evidence")
    output_value = payload.get("output")
    boundaries = payload.get("boundaries")
    if (
        event.project_id != workflow.project_id
        or event.workflow_id != workflow.id
        or event.event_type != _REMEMBERED_EVIDENCE_EPISODE_EVENT
        or set(payload) != expected_keys
        or payload.get("schemaVersion") != "1"
        or payload.get("action") != _REMEMBERED_EVIDENCE_CREATOR
        or payload.get("actionVersion") != "1"
        or payload.get("projectId") != workflow.project_id
        or payload.get("workflowId") != workflow.id
        or payload.get("episodeId") != expected_episode_id
        or not isinstance(source_value, dict)
        or not isinstance(evidence_value, dict)
        or not isinstance(output_value, dict)
        or boundaries
        != {
            "createsClaim": False,
            "createsSource": False,
            "changesPermission": False,
            "changesDisclosure": False,
            "promotesContext": False,
        }
    ):
        raise WorkflowFailure(
            "verified-episode-integrity-invalid",
            "The verified episode has an invalid scoped action envelope.",
        )
    source_data = cast(dict[object, object], source_value)
    evidence_data = cast(dict[object, object], evidence_value)
    output_data = cast(dict[object, object], output_value)
    source_id = source_data.get("id")
    source_hash = source_data.get("contentHash")
    evidence_id = evidence_data.get("id")
    memory_id = output_data.get("memoryCandidateId")
    if (
        set(source_data) != {"id", "contentHash"}
        or set(evidence_data) != {"id", "sourceId", "quoteHash", "verified"}
        or set(output_data)
        != {
            "memoryCandidateId",
            "revision",
            "memoryContentSha256",
            "createdStatus",
        }
        or not isinstance(source_id, str)
        or not _is_sha256(source_hash)
        or not isinstance(evidence_id, str)
        or evidence_data.get("sourceId") != source_id
        or not _is_sha256(evidence_data.get("quoteHash"))
        or evidence_data.get("verified") is not True
        or not isinstance(memory_id, str)
        or not isinstance(output_data.get("revision"), int)
        or isinstance(output_data.get("revision"), bool)
        or not _is_sha256(output_data.get("memoryContentSha256"))
        or output_data.get("createdStatus") != "candidate"
    ):
        raise WorkflowFailure(
            "verified-episode-integrity-invalid",
            "The verified episode has invalid source, evidence, or output identities.",
        )
    source = session.scalar(
        select(SourceRecord).where(
            SourceRecord.id == source_id,
            SourceRecord.project_id == workflow.project_id,
        )
    )
    evidence = session.get(EvidenceSpanRecord, evidence_id)
    memory = session.scalar(
        select(ResearchMemoryRecord).where(
            ResearchMemoryRecord.id == memory_id,
            ResearchMemoryRecord.project_id == workflow.project_id,
            ResearchMemoryRecord.scope_workflow_id == workflow.id,
        )
    )
    if (
        source is None
        or source.ingestion_status != "ready"
        or source.content_hash != source_hash
        or evidence is None
        or not evidence.verified
        or evidence.source_id != source.id
        or evidence.quote_hash != evidence_data.get("quoteHash")
        or memory is None
        or memory.revision != output_data.get("revision")
        or memory.memory_sha256 != output_data.get("memoryContentSha256")
        or memory.status not in {"candidate", "committed", "rejected", "invalidated"}
    ):
        raise WorkflowFailure(
            "verified-episode-dependency-stale",
            "The verified episode no longer matches its current immutable dependencies.",
        )
    try:
        assert_verified_evidence_span_current(session, workflow, evidence)
    except WorkflowConflict as error:
        raise WorkflowFailure(
            "verified-episode-dependency-stale",
            "The verified episode evidence no longer matches its local source page.",
        ) from error
    _assert_remembered_evidence_provenance(session, workflow, memory)
    material = {
        key: value
        for key, value in payload.items()
        if key not in {"episodeId", "episodeSha256"}
    }
    episode_sha256 = content_sha256(material)
    expected_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"spark:verified-episode:{episode_sha256}",
        )
    )
    if (
        payload.get("episodeSha256") != episode_sha256
        or expected_id != expected_episode_id
    ):
        raise WorkflowFailure(
            "verified-episode-integrity-invalid",
            "The verified episode hash or deterministic identity is invalid.",
        )
    return _episode_identity(payload)


def _assert_remembered_evidence_provenance(
    session: Session,
    workflow: WorkflowRecord,
    memory: ResearchMemoryRecord,
) -> None:
    _assert_memory_integrity(memory)
    _assert_memory_lineage(session, workflow, memory)
    content = memory.content_json
    evidence_id = content.get("evidenceId")
    source_id = content.get("sourceId")
    quote_hash = content.get("quoteHash")
    page_index = content.get("pageIndex")
    valid_refs = (
        len(memory.source_refs) == 2
        and memory.source_refs[0].get("type") == "source"
        and memory.source_refs[0].get("id") == source_id
        and _is_sha256(memory.source_refs[0].get("sha256"))
        and memory.source_refs[1]
        == {"id": evidence_id, "sha256": quote_hash, "type": "evidence"}
    )
    if (
        memory.created_by != _REMEMBERED_EVIDENCE_CREATOR
        or memory.type != "user-decision"
        or not isinstance(evidence_id, str)
        or not isinstance(source_id, str)
        or not _is_sha256(quote_hash)
        or not isinstance(page_index, int)
        or isinstance(page_index, bool)
        or memory.content_json
        != {
            "schemaVersion": "1",
            "kind": "remembered-evidence",
            "evidenceId": evidence_id,
            "sourceId": source_id,
            "pageIndex": page_index,
            "quoteHash": quote_hash,
            "claimStatus": "not-a-claim",
        }
        or not valid_refs
        or memory.artifact_refs != []
        or memory.invalidation_rule != _REMEMBERED_EVIDENCE_RULE
        or memory.subject_key != f"remembered-evidence:{workflow.id}:{evidence_id}"
        or memory.creation_key
        != _remembered_evidence_creation_key(
            workflow,
            evidence_id,
            cast(str, quote_hash),
            memory.revision,
        )
    ):
        raise WorkflowFailure(
            "research-memory-provenance-invalid",
            "The remembered evidence candidate has an invalid immutable envelope.",
        )


def _assert_user_decision_provenance(
    session: Session,
    workflow: WorkflowRecord,
    memory: ResearchMemoryRecord,
) -> None:
    if (
        memory.created_by != _USER_DECISION_CREATOR
        or memory.type != "user-decision"
        or memory.status in {"candidate", "rejected"}
        or len(memory.source_refs) != 1
    ):
        raise WorkflowFailure(
            "research-memory-provenance-invalid",
            "The user decision memory has an invalid producer envelope.",
        )
    source = memory.source_refs[0]
    response_id = source.get("id")
    if (
        source.get("type") != "user-response"
        or not isinstance(response_id, str)
    ):
        raise WorkflowFailure(
            "research-memory-provenance-invalid",
            "The user decision memory does not identify a durable response.",
        )
    response = session.get(UserResponseRecord, response_id)
    interaction = (
        session.get(InteractionRequestRecord, response.interaction_id)
        if response is not None
        else None
    )
    if response is None or interaction is None or interaction.workflow_id != workflow.id:
        raise WorkflowFailure(
            "research-memory-provenance-invalid",
            "The user decision source is missing from this workflow.",
        )
    expected_source_refs = [
        {
            "id": response.id,
            "sha256": response.response_sha256,
            "type": "user-response",
        }
    ]
    expected_content = {
        "response": response.response_json,
        "responseId": response.id,
        "responseRevision": response.revision,
    }
    expected_rule = "Superseded only by a later response to this interaction."
    expected_material = _memory_material(
        artifact_refs=[],
        content=expected_content,
        invalidation_rule=expected_rule,
        scope_workflow_id=workflow.id,
        source_refs=expected_source_refs,
        subject_key=f"interaction-response:{response.interaction_id}",
        memory_type="user-decision",
    )
    if (
        memory.creation_key != f"user-response:{response.id}"
        or memory.subject_key != f"interaction-response:{response.interaction_id}"
        or memory.revision != response.revision
        or memory.content_json != expected_content
        or memory.source_refs != expected_source_refs
        or memory.artifact_refs != []
        or memory.invalidation_rule != expected_rule
        or memory.memory_sha256 != content_sha256(expected_material)
    ):
        raise WorkflowFailure(
            "research-memory-provenance-invalid",
            "The user decision memory no longer matches its durable response.",
        )


def _assert_observation_candidate_provenance(
    session: Session,
    workflow: WorkflowRecord,
    memory: ResearchMemoryRecord,
) -> None:
    if (
        memory.scope_workflow_id != workflow.id
        or memory.created_by != _OBSERVATION_CANDIDATE_CREATOR
        or memory.type not in {"open-question", "failure-lesson"}
    ):
        raise WorkflowFailure(
            "research-memory-provenance-invalid",
            "The memory candidate does not have supported observation provenance.",
        )
    observation_refs = [
        ref for ref in memory.source_refs if ref.get("type") == "step-observation"
    ]
    dependency_refs = [
        ref
        for ref in memory.source_refs
        if ref.get("type") in {"source", "evidence"}
    ]
    if (
        len(observation_refs) != 1
        or len(observation_refs) + len(dependency_refs) != len(memory.source_refs)
        or memory.source_refs[0] != observation_refs[0]
    ):
        raise WorkflowFailure(
            "research-memory-provenance-invalid",
            "The memory candidate has an invalid observation dependency envelope.",
        )
    source = observation_refs[0]
    observation_id = source.get("id")
    if (
        source.get("type") != "step-observation"
        or not isinstance(observation_id, str)
    ):
        raise WorkflowFailure(
            "research-memory-provenance-invalid",
            "The memory candidate does not identify one step observation.",
        )
    observation = session.scalar(
        select(StepObservationRecord).where(
            StepObservationRecord.id == observation_id,
            StepObservationRecord.workflow_id == workflow.id,
        )
    )
    if observation is None:
        raise WorkflowFailure(
            "research-memory-provenance-invalid",
            "The source observation is missing from this workflow.",
        )
    value = _observation_value(observation, workflow)
    if memory.type == "open-question":
        code = memory.content_json.get("code")
        question = next(
            (
                item
                for item in value.unresolved_questions
                if isinstance(code, str) and item.code == code
            ),
            None,
        )
        if question is None:
            raise WorkflowFailure(
                "research-memory-provenance-invalid",
                "The open question is not present in its source observation.",
            )
        expected_key = (
            f"step-observation:{observation.id}:open-question:{question.code}"
        )
        expected_subject = (
            f"open-question:{workflow.id}:{value.step_key}:{question.code}"
        )
        expected_content: dict[str, object] = {
            "answerType": question.answer_type,
            "code": question.code,
            "question": question.question,
            "stepKey": value.step_key,
        }
        expected_rule = (
            "Invalidated when the question is answered or no longer applies."
        )
    else:
        if (
            value.status not in {"failed", "blocked"}
            or observation.generator != "deterministic-observer-v1"
            or observation.model_invocation_id is not None
        ):
            raise WorkflowFailure(
                "research-memory-provenance-invalid",
                "The failure lesson is not backed by a deterministic failed observation.",
            )
        primary_warning_code = (
            value.warnings[0].code if value.warnings else "no-primary-warning"
        )
        expected_key = f"step-observation:{observation.id}:failure-lesson"
        expected_subject = (
            f"failure-lesson:{workflow.id}:{value.step_key}:"
            f"{value.failure_category}:{primary_warning_code}"
        )
        expected_content = {
            "attempt": value.attempt,
            "failureCategory": value.failure_category,
            "recommendedActions": list(value.recommended_actions),
            "status": value.status,
            "stepKey": value.step_key,
            "warnings": [
                warning.model_dump(mode="json", by_alias=True)
                for warning in value.warnings
            ],
        }
        expected_rule = (
            "Invalidated when the failure condition or bounded method changes."
        )
    expected_observation_ref = {
        "id": observation.id,
        "sha256": observation.output_sha256,
        "type": "step-observation",
    }
    expected_source_refs = [
        expected_observation_ref,
        *dependency_refs,
    ]
    expected_material = _memory_material(
        artifact_refs=[],
        content=expected_content,
        invalidation_rule=expected_rule,
        scope_workflow_id=workflow.id,
        source_refs=expected_source_refs,
        subject_key=expected_subject,
        memory_type=memory.type,
    )
    if (
        memory.creation_key != expected_key
        or memory.subject_key != expected_subject
        or memory.content_json != expected_content
        or memory.source_refs != expected_source_refs
        or memory.artifact_refs != []
        or memory.invalidation_rule != expected_rule
        or memory.memory_sha256 != content_sha256(expected_material)
    ):
        raise WorkflowFailure(
            "research-memory-provenance-invalid",
            "The memory candidate no longer matches its source observation.",
        )


def _observation_value(
    observation: StepObservationRecord,
    workflow: WorkflowRecord,
) -> StepObservation:
    if observation.workflow_id != workflow.id:
        raise WorkflowFailure(
            "research-memory-provenance-invalid",
            "The source observation does not belong to this workflow.",
        )
    value = StepObservation.model_validate(
        {
            "schemaVersion": observation.schema_version,
            "workflowId": observation.workflow_id,
            "planId": observation.plan_id,
            "taskId": observation.task_id,
            "sourceJobId": observation.source_job_id,
            "runId": observation.run_id,
            "reviewId": observation.review_id,
            "observationType": observation.observation_type,
            "stepKey": observation.step_key,
            "attempt": observation.attempt,
            "status": observation.status,
            "facts": observation.facts_json,
            "warnings": observation.warnings_json,
            "unresolvedQuestions": observation.unresolved_questions_json,
            "artifactIds": observation.artifact_ids_json,
            "failureCategory": observation.failure_category,
            "recommendedActions": observation.recommended_actions_json,
        },
        strict=True,
    )
    if step_observation_sha256(value) != observation.output_sha256:
        raise WorkflowFailure(
            "agent-observation-integrity-failed",
            "The source observation no longer matches its immutable hash.",
        )
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _memory_material(
    *,
    artifact_refs: Sequence[Mapping[str, object]],
    content: Mapping[str, object],
    invalidation_rule: str | None,
    scope_workflow_id: str | None,
    source_refs: Sequence[Mapping[str, object]],
    subject_key: str,
    memory_type: str,
) -> dict[str, object]:
    return {
        "artifactRefs": list(artifact_refs),
        "content": dict(content),
        "invalidationRule": invalidation_rule,
        "scopeWorkflowId": scope_workflow_id,
        "sourceRefs": list(source_refs),
        "subjectKey": subject_key,
        "type": memory_type,
    }


def _assert_memory_integrity(memory: ResearchMemoryRecord) -> None:
    material = _memory_material(
        artifact_refs=memory.artifact_refs,
        content=memory.content_json,
        invalidation_rule=memory.invalidation_rule,
        scope_workflow_id=memory.scope_workflow_id,
        source_refs=memory.source_refs,
        subject_key=memory.subject_key,
        memory_type=memory.type,
    )
    if content_sha256(material) != memory.memory_sha256:
        raise WorkflowFailure(
            "research-memory-integrity-invalid",
            "A committed research memory does not match its immutable content.",
        )


def _memory_dependency_state(
    session: Session,
    workflow: WorkflowRecord,
    memory: ResearchMemoryRecord,
) -> _MemoryDependencyState:
    source_refs = sorted(
        (ref for ref in memory.source_refs if ref.get("type") == "source"),
        key=lambda ref: (str(ref.get("id")), str(ref.get("sha256"))),
    )
    evidence_refs = sorted(
        (ref for ref in memory.source_refs if ref.get("type") == "evidence"),
        key=lambda ref: (str(ref.get("id")), str(ref.get("sha256"))),
    )
    if not source_refs and not evidence_refs:
        return _MemoryDependencyState(
            eligible=True,
            reason_code=None,
            projection={"schemaVersion": "1", "dependencies": []},
            bound=False,
        )

    projection: list[dict[str, object]] = []
    source_by_id: dict[str, tuple[dict[str, object], SourceRecord]] = {}
    reason: MemoryContextReasonCode | None = None
    for ref in source_refs:
        source_id = ref.get("id")
        expected_sha256 = ref.get("sha256")
        if not _is_sha256(expected_sha256) or not isinstance(source_id, str):
            projection.append(
                {
                    "expectedSha256": expected_sha256,
                    "id": source_id,
                    "state": "invalid-reference",
                    "type": "source",
                }
            )
            reason = reason or "source-stale"
            continue
        source = session.scalar(
            select(SourceRecord).where(
                SourceRecord.id == source_id,
                SourceRecord.project_id == workflow.project_id,
            )
        )
        if source is None:
            projection.append(
                {
                    "expectedSha256": expected_sha256,
                    "id": source_id,
                    "state": "missing",
                    "type": "source",
                }
            )
            reason = reason or "source-missing"
            continue
        state = (
            "not-ready"
            if source.ingestion_status != "ready"
            else "stale"
            if source.content_hash != expected_sha256
            else "ready"
        )
        projection.append(
            {
                "actualSha256": source.content_hash,
                "expectedSha256": expected_sha256,
                "id": source_id,
                "ingestionStatus": source.ingestion_status,
                "state": state,
                "type": "source",
            }
        )
        source_by_id[source_id] = (ref, source)
        if state == "not-ready":
            reason = reason or "source-not-ready"
        elif state == "stale":
            reason = reason or "source-stale"

    for ref in evidence_refs:
        evidence_id = ref.get("id")
        expected_quote_sha256 = ref.get("sha256")
        if not _is_sha256(expected_quote_sha256) or not isinstance(evidence_id, str):
            projection.append(
                {
                    "expectedSha256": expected_quote_sha256,
                    "id": evidence_id,
                    "state": "invalid-reference",
                    "type": "evidence",
                }
            )
            reason = reason or "evidence-invalid"
            continue
        evidence = session.scalar(
            select(EvidenceSpanRecord)
            .join(SourceRecord, SourceRecord.id == EvidenceSpanRecord.source_id)
            .where(
                EvidenceSpanRecord.id == evidence_id,
                SourceRecord.project_id == workflow.project_id,
            )
        )
        if evidence is None:
            projection.append(
                {
                    "expectedSha256": expected_quote_sha256,
                    "id": evidence_id,
                    "state": "missing",
                    "type": "evidence",
                }
            )
            reason = reason or "evidence-missing"
            continue
        paired_source = source_by_id.get(evidence.source_id)
        valid = (
            paired_source is not None
            and paired_source[1].ingestion_status == "ready"
            and paired_source[1].content_hash == paired_source[0].get("sha256")
            and evidence.verified
            and evidence.quote_hash == expected_quote_sha256
        )
        if valid:
            try:
                assert_verified_evidence_span_current(session, workflow, evidence)
            except WorkflowConflict:
                valid = False
        projection.append(
            {
                "actualSha256": evidence.quote_hash,
                "expectedSha256": expected_quote_sha256,
                "id": evidence_id,
                "sourceId": evidence.source_id,
                "state": "verified" if valid else "invalid",
                "type": "evidence",
                "verified": evidence.verified,
            }
        )
        if not valid:
            reason = reason or "evidence-invalid"

    return _MemoryDependencyState(
        eligible=reason is None,
        reason_code=reason,
        projection={
            "schemaVersion": "1",
            "dependencies": projection,
        },
        bound=True,
    )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _identity_context_generation_sha256(
    memories: list[ResearchMemoryRecord],
) -> str:
    return content_sha256(
        {
            "schemaVersion": "1",
            "memories": [
                {
                    "id": memory.id,
                    "memorySha256": memory.memory_sha256,
                    "revision": memory.revision,
                }
                for memory in memories
            ],
        }
    )


def _context_generation_sha256(
    memories: list[ResearchMemoryRecord],
    dependency_states: Mapping[str, _MemoryDependencyState],
) -> str:
    if not any(dependency_states[memory.id].bound for memory in memories):
        return _identity_context_generation_sha256(memories)
    return content_sha256(
        {
            "schemaVersion": "2",
            "memories": [
                {
                    "dependencyStateSha256": dependency_states[memory.id].sha256,
                    "eligible": dependency_states[memory.id].eligible,
                    "id": memory.id,
                    "memorySha256": memory.memory_sha256,
                    "reasonCode": dependency_states[memory.id].reason_code,
                    "revision": memory.revision,
                }
                for memory in memories
            ],
        }
    )


def _legacy_context_generation_sha256(
    session: Session,
    selected_memory_refs: list[dict[str, object]],
) -> str:
    memories: list[ResearchMemoryRecord] = []
    for ref in selected_memory_refs:
        memory_id = ref.get("id")
        if not isinstance(memory_id, str):
            raise WorkflowFailure(
                "research-context-integrity-invalid",
                "The stored legacy research context has an invalid memory identity.",
            )
        memory = session.get(ResearchMemoryRecord, memory_id)
        if memory is None:
            raise WorkflowFailure(
                "research-context-integrity-invalid",
                "The stored legacy research context references a missing memory.",
            )
        memories.append(memory)
    return _identity_context_generation_sha256(memories)


def _context_payload(
    items: list[dict[str, object]],
    *,
    context_generation_sha256: str,
) -> dict[str, object]:
    return {
        "contextGenerationSha256": context_generation_sha256,
        "items": items,
        "maxBytes": MEMORY_MAX_BYTES,
        "maxItems": MEMORY_MAX_ITEMS,
        "selectionVersion": 1,
    }


def _selected_refs(items: list[dict[str, object]]) -> list[dict[str, object]]:
    return [{"id": item["id"], "sha256": item["memorySha256"]} for item in items]


def _assert_snapshot_integrity(
    session: Session,
    snapshot: AgentContextSnapshotRecord,
    workflow: WorkflowRecord,
    plan_id: str | None,
    observation_id: str,
) -> None:
    context = snapshot.context_json
    items_value: object = context.get("items")
    if (
        snapshot.project_id != workflow.project_id
        or snapshot.workflow_id != workflow.id
        or snapshot.plan_id != plan_id
        or snapshot.observation_id != observation_id
        or snapshot.schema_version not in {"1", "2"}
        or snapshot.selection_version != 1
        or snapshot.max_items != MEMORY_MAX_ITEMS
        or snapshot.max_bytes != MEMORY_MAX_BYTES
        or context.get("selectionVersion") != 1
        or context.get("maxItems") != MEMORY_MAX_ITEMS
        or context.get("maxBytes") != MEMORY_MAX_BYTES
        or not isinstance(items_value, list)
        or len(_canonical_json(context).encode("utf-8")) > MEMORY_MAX_BYTES
        or content_sha256(context) != snapshot.context_sha256
    ):
        raise WorkflowFailure(
            "research-context-integrity-invalid",
            "The stored research context snapshot does not match its immutable identity.",
        )
    if snapshot.schema_version == "1":
        if (
            snapshot.context_generation_sha256 is not None
            or "contextGenerationSha256" in context
        ):
            raise WorkflowFailure(
                "research-context-integrity-invalid",
                "The stored legacy research context generation is invalid.",
            )
    elif (
        snapshot.context_generation_sha256 is None
        or context.get("contextGenerationSha256")
        != snapshot.context_generation_sha256
    ):
        raise WorkflowFailure(
            "research-context-integrity-invalid",
            "The stored research context generation is invalid.",
        )
    items = cast(list[object], items_value)
    if len(items) > MEMORY_MAX_ITEMS or not all(isinstance(item, dict) for item in items):
        raise WorkflowFailure(
            "research-context-integrity-invalid",
            "The stored research context contains an invalid memory reference.",
        )
    typed_items = [cast(dict[str, object], item) for item in items]
    expected_item_keys = {"content", "id", "memorySha256", "sourceRefs", "type"}
    if any(set(item) != expected_item_keys for item in typed_items):
        raise WorkflowFailure(
            "research-context-integrity-invalid",
            "The stored research context contains an invalid memory item shape.",
        )
    if _selected_refs(typed_items) != snapshot.selected_memory_refs:
        raise WorkflowFailure(
            "research-context-integrity-invalid",
            "The stored research context references do not match its immutable content.",
        )
    for item in typed_items:
        memory_id = item.get("id")
        memory = session.get(ResearchMemoryRecord, memory_id) if isinstance(memory_id, str) else None
        if memory is None or memory.project_id != workflow.project_id:
            raise WorkflowFailure(
                "research-context-integrity-invalid",
                "The stored research context references missing project memory.",
            )
        _assert_memory_integrity(memory)
        if (
            memory.scope_workflow_id not in {None, workflow.id}
            or item.get("memorySha256") != memory.memory_sha256
            or item.get("content") != memory.content_json
            or item.get("sourceRefs") != memory.source_refs
            or item.get("type") != memory.type
        ):
            raise WorkflowFailure(
                "research-context-integrity-invalid",
                "The stored research context memory does not match its scoped source.",
            )


__all__ = (
    "begin_memory_read_snapshot",
    "begin_memory_write_transaction",
    "commit_user_response_memory",
    "create_evidence_memory_candidate",
    "create_observation_memory_candidates",
    "decision_context_payload",
    "get_and_verify_remembered_evidence_episode",
    "get_or_create_context_snapshot",
    "get_research_memory_workspace",
    "invalidate_memory",
    "list_workflow_memories",
    "resolve_memory_candidate",
)
