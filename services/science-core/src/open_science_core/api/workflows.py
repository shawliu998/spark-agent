from __future__ import annotations

from collections.abc import Generator
from typing import Never

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..db import database_session
from ..models import ProjectRecord, WorkflowRecord
from ..workflow.agent_schemas import AgentRunSnapshot
from ..workflow.agent_service import agent_run_snapshot
from ..workflow.schemas import (
    AcceptReviewWarningsIn,
    ApprovePlanIn,
    ResearchWorkflowCreateIn,
    ResearchWorkflowSnapshot,
    RetryWorkflowIn,
    WorkflowAnalysisDecisionIn,
    WorkflowEventsOut,
    WorkflowMutationIn,
)
from ..workflow.service import (
    WorkflowConflict,
    accept_review_warnings,
    approve_plan,
    decide_analysis_execution,
    list_workflows,
    request_cancel,
    resume_workflow,
    retry_workflow,
    start_workflow,
    workflow_events,
    workflow_snapshot,
)

router = APIRouter(tags=["research-workflows"])
WorkflowSnapshotResponse = ResearchWorkflowSnapshot | AgentRunSnapshot


def get_workflow_session() -> Generator[Session, None, None]:
    yield from database_session()


def _project_or_404(session: Session, project_id: str) -> ProjectRecord:
    project = session.get(ProjectRecord, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _workflow_or_404(session: Session, workflow_id: str) -> WorkflowRecord:
    workflow = session.get(WorkflowRecord, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Research workflow not found")
    return workflow


def _raise_conflict(error: WorkflowConflict) -> Never:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": error.code,
            "userMessage": error.user_message,
            "retryable": error.retryable,
        },
    ) from error


def _fixed_snapshot_or_conflict(
    session: Session,
    workflow: WorkflowRecord,
) -> ResearchWorkflowSnapshot:
    try:
        return workflow_snapshot(session, workflow)
    except WorkflowConflict as error:
        _raise_conflict(error)
    except ValidationError:
        _raise_conflict(
            WorkflowConflict(
                "workflow-snapshot-integrity-failed",
                "The stored workflow does not satisfy its public snapshot contract.",
            )
        )


def _snapshot_or_conflict(
    session: Session,
    workflow: WorkflowRecord,
) -> WorkflowSnapshotResponse:
    if workflow.creation_mode != "autonomous":
        return _fixed_snapshot_or_conflict(session, workflow)
    try:
        return agent_run_snapshot(session, workflow)
    except WorkflowConflict as error:
        _raise_conflict(error)
    except ValidationError:
        _raise_conflict(
            WorkflowConflict(
                "agent-run-snapshot-integrity-failed",
                "The stored autonomous run does not satisfy its public snapshot contract.",
            )
        )


@router.post(
    "/v1/projects/{project_id}/workflows",
    response_model=ResearchWorkflowSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_workflow(
    project_id: str,
    payload: ResearchWorkflowCreateIn,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=200
    ),
    session: Session = Depends(get_workflow_session),
) -> ResearchWorkflowSnapshot:
    project = _project_or_404(session, project_id)
    try:
        workflow = start_workflow(session, project, payload, idempotency_key)
    except WorkflowConflict as error:
        _raise_conflict(error)
    return _fixed_snapshot_or_conflict(session, workflow)


@router.post(
    "/v1/workflows/{workflow_id}/analysis-intents/{intent_id}/decision",
    response_model=WorkflowSnapshotResponse,
)
def decide_workflow_analysis(
    workflow_id: str,
    intent_id: str,
    payload: WorkflowAnalysisDecisionIn,
    session: Session = Depends(get_workflow_session),
) -> WorkflowSnapshotResponse:
    workflow = _workflow_or_404(session, workflow_id)
    try:
        workflow = decide_analysis_execution(
            session,
            workflow,
            approval_id=payload.approval_id,
            intent_id=intent_id,
            decision=payload.decision,
            payload_sha256=payload.payload_sha256,
            expected_revision=payload.expected_workflow_revision,
        )
    except WorkflowConflict as error:
        session.rollback()
        _raise_conflict(error)
    return _snapshot_or_conflict(session, workflow)


@router.post(
    "/v1/workflows/{workflow_id}/accept-review-warnings",
    response_model=WorkflowSnapshotResponse,
)
def accept_workflow_review_warnings(
    workflow_id: str,
    payload: AcceptReviewWarningsIn,
    session: Session = Depends(get_workflow_session),
) -> WorkflowSnapshotResponse:
    workflow = _workflow_or_404(session, workflow_id)
    try:
        workflow = accept_review_warnings(
            session,
            workflow,
            review_id=payload.review_id,
            review_input_sha256=payload.review_input_sha256,
            expected_revision=payload.expected_workflow_revision,
        )
    except WorkflowConflict as error:
        session.rollback()
        _raise_conflict(error)
    return _snapshot_or_conflict(session, workflow)


@router.get(
    "/v1/projects/{project_id}/workflows",
    response_model=list[ResearchWorkflowSnapshot],
)
def get_project_workflows(
    project_id: str,
    active_only: bool = Query(default=False, alias="activeOnly"),
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_workflow_session),
) -> list[ResearchWorkflowSnapshot]:
    _project_or_404(session, project_id)
    return [
        _fixed_snapshot_or_conflict(session, workflow)
        for workflow in list_workflows(
            session,
            project_id,
            active_only=active_only,
            limit=limit,
        )
    ]


@router.get(
    "/v1/workflows/{workflow_id}",
    response_model=WorkflowSnapshotResponse,
)
def get_workflow(
    workflow_id: str,
    session: Session = Depends(get_workflow_session),
) -> WorkflowSnapshotResponse:
    return _snapshot_or_conflict(
        session,
        _workflow_or_404(session, workflow_id),
    )


@router.post(
    "/v1/workflows/{workflow_id}/approve-plan",
    response_model=WorkflowSnapshotResponse,
)
def approve_workflow_plan(
    workflow_id: str,
    payload: ApprovePlanIn,
    session: Session = Depends(get_workflow_session),
) -> WorkflowSnapshotResponse:
    workflow = _workflow_or_404(session, workflow_id)
    try:
        workflow = approve_plan(
            session,
            workflow,
            approval_id=payload.approval_id,
            plan_id=payload.plan_id,
            plan_version=payload.plan_version,
            plan_sha256=payload.plan_sha256,
            expected_revision=payload.expected_workflow_revision,
        )
    except WorkflowConflict as error:
        session.rollback()
        _raise_conflict(error)
    return _snapshot_or_conflict(session, workflow)


@router.post(
    "/v1/workflows/{workflow_id}/cancel",
    response_model=WorkflowSnapshotResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_workflow(
    workflow_id: str,
    payload: WorkflowMutationIn,
    session: Session = Depends(get_workflow_session),
) -> WorkflowSnapshotResponse:
    workflow = _workflow_or_404(session, workflow_id)
    try:
        workflow = request_cancel(
            session,
            workflow,
            expected_revision=payload.expected_workflow_revision,
        )
    except WorkflowConflict as error:
        session.rollback()
        _raise_conflict(error)
    return _snapshot_or_conflict(session, workflow)


@router.post(
    "/v1/workflows/{workflow_id}/retry",
    response_model=WorkflowSnapshotResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_failed_workflow(
    workflow_id: str,
    payload: RetryWorkflowIn,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=200
    ),
    session: Session = Depends(get_workflow_session),
) -> WorkflowSnapshotResponse:
    workflow = _workflow_or_404(session, workflow_id)
    try:
        workflow = retry_workflow(
            session,
            workflow,
            task_id=payload.task_id,
            expected_revision=payload.expected_workflow_revision,
            idempotency_key=idempotency_key,
        )
    except WorkflowConflict as error:
        session.rollback()
        _raise_conflict(error)
    return _snapshot_or_conflict(session, workflow)


@router.post(
    "/v1/workflows/{workflow_id}/resume",
    response_model=WorkflowSnapshotResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def resume_blocked_workflow(
    workflow_id: str,
    payload: WorkflowMutationIn,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=200
    ),
    session: Session = Depends(get_workflow_session),
) -> WorkflowSnapshotResponse:
    workflow = _workflow_or_404(session, workflow_id)
    try:
        workflow = resume_workflow(
            session,
            workflow,
            expected_revision=payload.expected_workflow_revision,
            idempotency_key=idempotency_key,
        )
    except WorkflowConflict as error:
        session.rollback()
        _raise_conflict(error)
    return _snapshot_or_conflict(session, workflow)


@router.get(
    "/v1/workflows/{workflow_id}/events",
    response_model=WorkflowEventsOut,
)
def get_workflow_events(
    workflow_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_workflow_session),
) -> WorkflowEventsOut:
    return workflow_events(
        session,
        _workflow_or_404(session, workflow_id),
        after=after,
        limit=limit,
    )
