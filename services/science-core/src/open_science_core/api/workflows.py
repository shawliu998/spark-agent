from __future__ import annotations

from collections.abc import Generator

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..db import database_session
from ..models import ProjectRecord, WorkflowRecord
from ..workflow.schemas import (
    ApprovePlanIn,
    ResearchWorkflowSnapshot,
    RetryWorkflowIn,
    WorkflowCreateIn,
    WorkflowEventsOut,
    WorkflowMutationIn,
)
from ..workflow.service import (
    WorkflowConflict,
    approve_plan,
    list_workflows,
    request_cancel,
    resume_workflow,
    retry_workflow,
    start_workflow,
    workflow_events,
    workflow_snapshot,
)


router = APIRouter(tags=["research-workflows"])


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


def _raise_conflict(error: WorkflowConflict) -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": error.code,
            "userMessage": error.user_message,
            "retryable": error.retryable,
        },
    ) from error


def _snapshot_or_conflict(
    session: Session,
    workflow: WorkflowRecord,
) -> ResearchWorkflowSnapshot:
    try:
        return workflow_snapshot(session, workflow)
    except WorkflowConflict as error:
        _raise_conflict(error)


@router.post(
    "/v1/projects/{project_id}/workflows",
    response_model=ResearchWorkflowSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_workflow(
    project_id: str,
    payload: WorkflowCreateIn,
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
        _snapshot_or_conflict(session, workflow)
        for workflow in list_workflows(
            session,
            project_id,
            active_only=active_only,
            limit=limit,
        )
    ]


@router.get(
    "/v1/workflows/{workflow_id}",
    response_model=ResearchWorkflowSnapshot,
)
def get_workflow(
    workflow_id: str,
    session: Session = Depends(get_workflow_session),
) -> ResearchWorkflowSnapshot:
    return _snapshot_or_conflict(
        session,
        _workflow_or_404(session, workflow_id),
    )


@router.post(
    "/v1/workflows/{workflow_id}/approve-plan",
    response_model=ResearchWorkflowSnapshot,
)
def approve_workflow_plan(
    workflow_id: str,
    payload: ApprovePlanIn,
    session: Session = Depends(get_workflow_session),
) -> ResearchWorkflowSnapshot:
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
    response_model=ResearchWorkflowSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_workflow(
    workflow_id: str,
    payload: WorkflowMutationIn,
    session: Session = Depends(get_workflow_session),
) -> ResearchWorkflowSnapshot:
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
    response_model=ResearchWorkflowSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_failed_workflow(
    workflow_id: str,
    payload: RetryWorkflowIn,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=200
    ),
    session: Session = Depends(get_workflow_session),
) -> ResearchWorkflowSnapshot:
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
    response_model=ResearchWorkflowSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
def resume_blocked_workflow(
    workflow_id: str,
    payload: WorkflowMutationIn,
    idempotency_key: str = Header(
        alias="Idempotency-Key", min_length=8, max_length=200
    ),
    session: Session = Depends(get_workflow_session),
) -> ResearchWorkflowSnapshot:
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
