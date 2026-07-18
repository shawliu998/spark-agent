from __future__ import annotations

from collections.abc import Generator
from typing import Never

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..db import database_session
from ..model_gateway import model_gateway
from ..models import (
    InteractionRequestRecord,
    ProjectRecord,
    WorkflowRecord,
)
from ..workflow._service.integrity import WorkflowConflict
from ..workflow.agent_schemas import (
    AgentRunCreateIn,
    AgentRunSnapshot,
    InteractionRequestOut,
    InteractionRespondIn,
)
from ..workflow.agent_service import (
    agent_run_snapshot,
    interaction_requests,
    list_agent_runs,
    respond_to_interaction,
    start_agent_run,
)

router = APIRouter(tags=["autonomous-agent-runs"])


def get_agent_session() -> Generator[Session, None, None]:
    yield from database_session()


def _project_or_404(session: Session, project_id: str) -> ProjectRecord:
    project = session.get(ProjectRecord, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _workflow_or_404(session: Session, workflow_id: str) -> WorkflowRecord:
    workflow = session.get(WorkflowRecord, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Autonomous agent run not found")
    return workflow


def _interaction_or_404(
    session: Session,
    interaction_id: str,
) -> InteractionRequestRecord:
    interaction = session.get(InteractionRequestRecord, interaction_id)
    if interaction is None:
        raise HTTPException(status_code=404, detail="Interaction request not found")
    return interaction


def _raise_conflict(error: WorkflowConflict) -> Never:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": error.code,
            "userMessage": error.user_message,
            "retryable": error.retryable,
        },
    ) from error


def _agent_snapshot_or_conflict(
    session: Session,
    workflow: WorkflowRecord,
) -> AgentRunSnapshot:
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
    "/v1/projects/{project_id}/agent-runs",
    response_model=AgentRunSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_agent_run(
    project_id: str,
    payload: AgentRunCreateIn,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=200,
    ),
    session: Session = Depends(get_agent_session),
) -> AgentRunSnapshot:
    try:
        workflow = start_agent_run(
            session,
            _project_or_404(session, project_id),
            payload,
            idempotency_key,
            gateway=model_gateway,
        )
    except WorkflowConflict as error:
        session.rollback()
        _raise_conflict(error)
    return _agent_snapshot_or_conflict(session, workflow)


@router.get(
    "/v1/projects/{project_id}/agent-runs",
    response_model=list[AgentRunSnapshot],
)
def get_project_agent_runs(
    project_id: str,
    active_only: bool = Query(default=False, alias="activeOnly"),
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = Depends(get_agent_session),
) -> list[AgentRunSnapshot]:
    _project_or_404(session, project_id)
    return [
        _agent_snapshot_or_conflict(session, workflow)
        for workflow in list_agent_runs(
            session,
            project_id,
            active_only=active_only,
            limit=limit,
        )
    ]


@router.get(
    "/v1/agent-runs/{workflow_id}",
    response_model=AgentRunSnapshot,
)
def get_agent_run(
    workflow_id: str,
    session: Session = Depends(get_agent_session),
) -> AgentRunSnapshot:
    return _agent_snapshot_or_conflict(
        session,
        _workflow_or_404(session, workflow_id),
    )


@router.get(
    "/v1/workflows/{workflow_id}/interactions",
    response_model=list[InteractionRequestOut],
)
def get_workflow_interactions(
    workflow_id: str,
    session: Session = Depends(get_agent_session),
) -> list[InteractionRequestOut]:
    workflow = _workflow_or_404(session, workflow_id)
    try:
        return interaction_requests(session, workflow)
    except WorkflowConflict as error:
        _raise_conflict(error)
    except ValidationError:
        _raise_conflict(
            WorkflowConflict(
                "interaction-snapshot-integrity-failed",
                "A stored clarification request does not satisfy its public contract.",
            )
        )


@router.post(
    "/v1/interactions/{interaction_id}/respond",
    response_model=AgentRunSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
def respond_to_workflow_interaction(
    interaction_id: str,
    payload: InteractionRespondIn,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=200,
    ),
    session: Session = Depends(get_agent_session),
) -> AgentRunSnapshot:
    try:
        workflow = respond_to_interaction(
            session,
            _interaction_or_404(session, interaction_id),
            payload,
            idempotency_key,
        )
    except WorkflowConflict as error:
        session.rollback()
        _raise_conflict(error)
    return _agent_snapshot_or_conflict(session, workflow)
