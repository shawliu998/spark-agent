from __future__ import annotations

from collections.abc import Generator
from typing import Never

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import database_session
from ..models import (
    ProjectRecord,
    ResearchMemoryRecord,
    SkillActivationRecord,
    SkillCandidateRecord,
    WorkflowRecord,
)
from ..workflow.research_memory import (
    begin_memory_read_snapshot,
    begin_memory_write_transaction,
    create_evidence_memory_candidate,
    get_research_memory_workspace,
    invalidate_memory,
    list_workflow_memories,
    resolve_memory_candidate,
)
from ..workflow.research_memory_schemas import (
    CreateEvidenceMemoryCandidateIn,
    CreateEvidenceMemoryCandidateOut,
    MemoryCandidateResolveIn,
    MemoryInvalidateIn,
    ResearchMemoryOut,
    ResearchMemoryWorkspaceOut,
)
from ..workflow.skill_activations import (
    activation_preview,
    approve_and_activate,
    invoke_active_capability,
    list_activations,
    recover_project_activations,
    rollback_activation,
)
from ..workflow.skill_candidate_schemas import (
    ActiveSkillCapabilityInvokeIn,
    ActiveSkillCapabilityInvokeOut,
    ApproveSkillActivationIn,
    CreateSkillCandidateIn,
    CreateSkillCandidateOut,
    RollbackSkillActivationIn,
    SkillActivationOut,
    SkillActivationPreviewOut,
    SkillCandidateOut,
)
from ..workflow.skill_candidates import (
    assert_skill_candidate_integrity,
    create_skill_candidate,
    list_skill_candidates,
)
from ..workflow.state import WorkflowFailure

router = APIRouter(tags=["research-memory"])


def get_research_memory_session() -> Generator[Session, None, None]:
    yield from database_session()


def _workflow_or_404(
    session: Session,
    project_id: str,
    workflow_id: str,
) -> WorkflowRecord:
    workflow = session.scalar(
        select(WorkflowRecord).where(
            WorkflowRecord.id == workflow_id,
            WorkflowRecord.project_id == project_id,
        )
    )
    if workflow is None:
        raise HTTPException(status_code=404, detail="Research workflow not found in project")
    return workflow


def _memory_or_404(
    session: Session,
    project_id: str,
    workflow_id: str,
    memory_id: str,
) -> ResearchMemoryRecord:
    memory = session.scalar(
        select(ResearchMemoryRecord).where(
            ResearchMemoryRecord.id == memory_id,
            ResearchMemoryRecord.project_id == project_id,
            ResearchMemoryRecord.scope_workflow_id == workflow_id,
        )
    )
    if memory is None:
        raise HTTPException(status_code=404, detail="Research memory not found in workflow")
    return memory


def _skill_candidate_or_404(
    session: Session,
    project_id: str,
    workflow_id: str,
    candidate_id: str,
) -> SkillCandidateRecord:
    candidate = session.scalar(
        select(SkillCandidateRecord).where(
            SkillCandidateRecord.id == candidate_id,
            SkillCandidateRecord.project_id == project_id,
            SkillCandidateRecord.workflow_id == workflow_id,
        )
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="Skill candidate not found in workflow")
    return candidate


def _project_or_404(session: Session, project_id: str) -> ProjectRecord:
    project = session.scalar(select(ProjectRecord).where(ProjectRecord.id == project_id))
    if project is None:
        raise HTTPException(status_code=404, detail="Research project not found")
    return project


def _skill_activation_or_404(
    session: Session,
    project_id: str,
    activation_id: str,
) -> SkillActivationRecord:
    activation = session.scalar(
        select(SkillActivationRecord).where(
            SkillActivationRecord.id == activation_id,
            SkillActivationRecord.project_id == project_id,
        )
    )
    if activation is None:
        raise HTTPException(status_code=404, detail="Skill activation not found in project")
    return activation


def _raise_conflict(error: WorkflowFailure) -> Never:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": error.code,
            "userMessage": error.user_message,
            "retryable": error.retryable,
        },
    ) from error


@router.post(
    ("/v1/projects/{project_id}/active-skill-capabilities/remember-verified-evidence/invoke"),
    response_model=ActiveSkillCapabilityInvokeOut,
)
def invoke_active_remember_verified_evidence(
    project_id: str,
    payload: ActiveSkillCapabilityInvokeIn,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    session: Session = Depends(get_research_memory_session),
) -> ActiveSkillCapabilityInvokeOut:
    del idempotency_key
    project = _project_or_404(session, project_id)
    try:
        recover_project_activations(session, project)
        begin_memory_write_transaction(session)
        project = _project_or_404(session, project_id)
        result = invoke_active_capability(
            session,
            project,
            payload.model_dump(mode="json", by_alias=True),
        )
        session.commit()
        return ActiveSkillCapabilityInvokeOut.model_validate(result, strict=True)
    except WorkflowFailure as error:
        session.rollback()
        _raise_conflict(error)


@router.get(
    "/v1/projects/{project_id}/workflows/{workflow_id}/research-memory-workspace",
    response_model=ResearchMemoryWorkspaceOut,
)
def read_research_memory_workspace(
    project_id: str,
    workflow_id: str,
    session: Session = Depends(get_research_memory_session),
) -> ResearchMemoryWorkspaceOut:
    begin_memory_read_snapshot(session)
    workflow = _workflow_or_404(session, project_id, workflow_id)
    try:
        return get_research_memory_workspace(session, workflow)
    except WorkflowFailure as error:
        _raise_conflict(error)


@router.post(
    "/v1/projects/{project_id}/workflows/{workflow_id}/research-memory-candidates/from-evidence",
    response_model=CreateEvidenceMemoryCandidateOut,
)
def remember_verified_evidence(
    project_id: str,
    workflow_id: str,
    payload: CreateEvidenceMemoryCandidateIn,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    session: Session = Depends(get_research_memory_session),
) -> CreateEvidenceMemoryCandidateOut:
    del idempotency_key
    begin_memory_write_transaction(session)
    workflow = _workflow_or_404(session, project_id, workflow_id)
    try:
        memory, outcome, episode = create_evidence_memory_candidate(
            session,
            workflow,
            evidence_id=payload.evidence_id,
            expected_source_content_hash=payload.expected_source_content_hash,
            expected_quote_hash=payload.expected_quote_hash,
        )
        session.commit()
        session.refresh(memory)
        return CreateEvidenceMemoryCandidateOut.model_validate(
            {
                "outcome": outcome,
                "memory": memory,
                "verifiedEpisode": episode.model_dump(mode="json", by_alias=True),
            },
            strict=True,
        )
    except WorkflowFailure as error:
        session.rollback()
        _raise_conflict(error)


@router.post(
    "/v1/projects/{project_id}/workflows/{workflow_id}/skill-candidates",
    response_model=CreateSkillCandidateOut,
)
def create_project_skill_candidate(
    project_id: str,
    workflow_id: str,
    payload: CreateSkillCandidateIn,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    session: Session = Depends(get_research_memory_session),
) -> CreateSkillCandidateOut:
    del idempotency_key
    begin_memory_write_transaction(session)
    workflow = _workflow_or_404(session, project_id, workflow_id)
    try:
        candidate, outcome = create_skill_candidate(
            session,
            workflow,
            memory_id=payload.memory_id,
            expected_memory_content_hash=payload.expected_memory_content_hash,
            episode_id=payload.episode_id,
            expected_episode_sha256=payload.expected_episode_sha256,
        )
        session.commit()
        session.refresh(candidate)
        return CreateSkillCandidateOut.model_validate(
            {"outcome": outcome, "candidate": candidate},
            strict=True,
        )
    except WorkflowFailure as error:
        session.rollback()
        _raise_conflict(error)


@router.get(
    "/v1/projects/{project_id}/workflows/{workflow_id}/skill-candidates",
    response_model=list[SkillCandidateOut],
)
def list_project_skill_candidates(
    project_id: str,
    workflow_id: str,
    session: Session = Depends(get_research_memory_session),
) -> list[SkillCandidateOut]:
    workflow = _workflow_or_404(session, project_id, workflow_id)
    try:
        return [
            SkillCandidateOut.model_validate(candidate)
            for candidate in list_skill_candidates(session, workflow)
        ]
    except WorkflowFailure as error:
        _raise_conflict(error)


@router.get(
    "/v1/projects/{project_id}/workflows/{workflow_id}/skill-candidates/{candidate_id}",
    response_model=SkillCandidateOut,
)
def get_project_skill_candidate(
    project_id: str,
    workflow_id: str,
    candidate_id: str,
    session: Session = Depends(get_research_memory_session),
) -> SkillCandidateOut:
    workflow = _workflow_or_404(session, project_id, workflow_id)
    candidate = _skill_candidate_or_404(
        session,
        project_id,
        workflow_id,
        candidate_id,
    )
    try:
        assert_skill_candidate_integrity(session, workflow, candidate)
    except WorkflowFailure as error:
        _raise_conflict(error)
    return SkillCandidateOut.model_validate(candidate)


@router.get(
    (
        "/v1/projects/{project_id}/workflows/{workflow_id}/skill-candidates/"
        "{candidate_id}/activation-preview"
    ),
    response_model=SkillActivationPreviewOut,
)
def preview_project_skill_activation(
    project_id: str,
    workflow_id: str,
    candidate_id: str,
    session: Session = Depends(get_research_memory_session),
) -> SkillActivationPreviewOut:
    project = _project_or_404(session, project_id)
    workflow = _workflow_or_404(session, project_id, workflow_id)
    candidate = _skill_candidate_or_404(session, project_id, workflow_id, candidate_id)
    try:
        recover_project_activations(session, project)
        project = _project_or_404(session, project_id)
        workflow = _workflow_or_404(session, project_id, workflow_id)
        candidate = _skill_candidate_or_404(session, project_id, workflow_id, candidate_id)
        return activation_preview(session, project, workflow, candidate)
    except WorkflowFailure as error:
        _raise_conflict(error)


@router.post(
    (
        "/v1/projects/{project_id}/workflows/{workflow_id}/skill-candidates/"
        "{candidate_id}/approve-and-activate"
    ),
    response_model=SkillActivationOut,
)
def approve_project_skill_activation(
    project_id: str,
    workflow_id: str,
    candidate_id: str,
    payload: ApproveSkillActivationIn,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    session: Session = Depends(get_research_memory_session),
) -> SkillActivationOut:
    project = _project_or_404(session, project_id)
    workflow = _workflow_or_404(session, project_id, workflow_id)
    candidate = _skill_candidate_or_404(session, project_id, workflow_id, candidate_id)
    try:
        activation = approve_and_activate(
            session,
            project,
            workflow,
            candidate,
            payload,
            idempotency_key=idempotency_key,
        )
        return SkillActivationOut.model_validate(activation)
    except WorkflowFailure as error:
        session.rollback()
        _raise_conflict(error)


@router.get(
    "/v1/projects/{project_id}/skill-activations",
    response_model=list[SkillActivationOut],
)
def list_project_skill_activations(
    project_id: str,
    workflow_id: str | None = None,
    session: Session = Depends(get_research_memory_session),
) -> list[SkillActivationOut]:
    project = _project_or_404(session, project_id)
    try:
        recover_project_activations(session, project)
        return [
            SkillActivationOut.model_validate(activation)
            for activation in list_activations(
                session,
                project_id=project_id,
                workflow_id=workflow_id,
            )
        ]
    except WorkflowFailure as error:
        session.rollback()
        _raise_conflict(error)


@router.get(
    "/v1/projects/{project_id}/skill-activations/{activation_id}",
    response_model=SkillActivationOut,
)
def get_project_skill_activation(
    project_id: str,
    activation_id: str,
    session: Session = Depends(get_research_memory_session),
) -> SkillActivationOut:
    project = _project_or_404(session, project_id)
    try:
        recover_project_activations(session, project)
        return SkillActivationOut.model_validate(
            _skill_activation_or_404(session, project_id, activation_id)
        )
    except WorkflowFailure as error:
        session.rollback()
        _raise_conflict(error)


@router.post(
    "/v1/projects/{project_id}/skill-activations/{activation_id}/rollback",
    response_model=SkillActivationOut,
)
def rollback_project_skill_activation(
    project_id: str,
    activation_id: str,
    payload: RollbackSkillActivationIn,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    session: Session = Depends(get_research_memory_session),
) -> SkillActivationOut:
    project = _project_or_404(session, project_id)
    activation = _skill_activation_or_404(session, project_id, activation_id)
    workflow = _workflow_or_404(session, project_id, activation.workflow_id)
    candidate = _skill_candidate_or_404(
        session,
        project_id,
        workflow.id,
        activation.candidate_id,
    )
    try:
        rolled_back = rollback_activation(
            session,
            project,
            workflow,
            candidate,
            activation,
            payload,
            idempotency_key=idempotency_key,
        )
        return SkillActivationOut.model_validate(rolled_back)
    except WorkflowFailure as error:
        session.rollback()
        _raise_conflict(error)


@router.get(
    "/v1/projects/{project_id}/workflows/{workflow_id}/research-memories",
    response_model=list[ResearchMemoryOut],
)
def list_research_memories(
    project_id: str,
    workflow_id: str,
    session: Session = Depends(get_research_memory_session),
) -> list[ResearchMemoryOut]:
    workflow = _workflow_or_404(session, project_id, workflow_id)
    try:
        return [
            ResearchMemoryOut.model_validate(memory)
            for memory in list_workflow_memories(session, workflow)
        ]
    except WorkflowFailure as error:
        _raise_conflict(error)


@router.post(
    "/v1/projects/{project_id}/workflows/{workflow_id}/research-memories/{memory_id}/resolve",
    response_model=ResearchMemoryOut,
)
def resolve_research_memory(
    project_id: str,
    workflow_id: str,
    memory_id: str,
    payload: MemoryCandidateResolveIn,
    session: Session = Depends(get_research_memory_session),
) -> ResearchMemoryOut:
    begin_memory_write_transaction(session)
    workflow = _workflow_or_404(session, project_id, workflow_id)
    memory = _memory_or_404(session, project_id, workflow_id, memory_id)
    try:
        resolved = resolve_memory_candidate(
            session,
            workflow,
            memory,
            decision=payload.decision,
            expected_content_hash=payload.expected_content_hash,
            expected_status=payload.expected_status,
            expected_revision=payload.expected_revision,
            expected_subject_head_id=payload.expected_subject_head_id,
            expected_subject_head_revision=payload.expected_subject_head_revision,
        )
        session.commit()
        session.refresh(resolved)
        return ResearchMemoryOut.model_validate(resolved)
    except WorkflowFailure as error:
        session.rollback()
        _raise_conflict(error)


@router.post(
    "/v1/projects/{project_id}/workflows/{workflow_id}/research-memories/{memory_id}/invalidate",
    response_model=ResearchMemoryOut,
)
def invalidate_research_memory(
    project_id: str,
    workflow_id: str,
    memory_id: str,
    payload: MemoryInvalidateIn,
    session: Session = Depends(get_research_memory_session),
) -> ResearchMemoryOut:
    begin_memory_write_transaction(session)
    workflow = _workflow_or_404(session, project_id, workflow_id)
    memory = _memory_or_404(session, project_id, workflow_id, memory_id)
    try:
        invalidated = invalidate_memory(
            session,
            workflow,
            memory,
            expected_content_hash=payload.expected_content_hash,
            expected_status=payload.expected_status,
            expected_revision=payload.expected_revision,
            expected_subject_head_id=payload.expected_subject_head_id,
            expected_subject_head_revision=payload.expected_subject_head_revision,
        )
        session.commit()
        session.refresh(invalidated)
        return ResearchMemoryOut.model_validate(invalidated)
    except WorkflowFailure as error:
        session.rollback()
        _raise_conflict(error)


__all__ = ("get_research_memory_session", "router")
