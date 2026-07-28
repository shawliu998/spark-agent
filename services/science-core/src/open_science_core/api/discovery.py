from __future__ import annotations

from collections.abc import Generator
from typing import Never

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..db import database_session
from ..models import ProjectRecord, WorkflowRecord
from ..workflow._service.integrity import WorkflowConflict
from ..workflow.agent_schemas import AgentRunSnapshot
from ..workflow.agent_service import agent_run_snapshot
from ..workflow.csl_json_import import (
    MAX_CSL_JSON_BYTES,
    import_csl_json_candidates,
)
from ..workflow.discovery_schemas import (
    CslJsonImportOut,
    DiscoveryRunCreateIn,
    WorkflowDiscoverySnapshotOut,
)
from ..workflow.discovery_service import (
    start_discovery_run,
    workflow_discovery_snapshot,
)

router = APIRouter(tags=["paper-discovery"])


def get_discovery_session() -> Generator[Session, None, None]:
    yield from database_session()


def _raise_conflict(error: WorkflowConflict) -> Never:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": error.code,
            "userMessage": error.user_message,
            "retryable": error.retryable,
        },
    ) from error


@router.post(
    "/v1/projects/{project_id}/discovery-runs",
    response_model=AgentRunSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_discovery_run(
    project_id: str,
    payload: DiscoveryRunCreateIn,
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=200,
    ),
    session: Session = Depends(get_discovery_session),
) -> AgentRunSnapshot:
    project = session.get(ProjectRecord, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    try:
        workflow = start_discovery_run(
            session,
            project,
            payload,
            idempotency_key,
        )
        return agent_run_snapshot(session, workflow)
    except WorkflowConflict as error:
        session.rollback()
        _raise_conflict(error)
    except ValidationError:
        session.rollback()
        _raise_conflict(
            WorkflowConflict(
                "discovery-snapshot-integrity-failed",
                "The stored discovery proposal does not satisfy its public snapshot contract.",
            )
        )


@router.get(
    "/v1/workflows/{workflow_id}/discovery",
    response_model=WorkflowDiscoverySnapshotOut,
)
def get_workflow_discovery(
    workflow_id: str,
    offset: int = Query(default=0, ge=0, le=10_000),
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_discovery_session),
) -> WorkflowDiscoverySnapshotOut:
    workflow = session.get(WorkflowRecord, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Research workflow not found")
    try:
        return workflow_discovery_snapshot(
            session,
            workflow,
            offset=offset,
            limit=limit,
        )
    except WorkflowConflict as error:
        _raise_conflict(error)
    except ValidationError:
        _raise_conflict(
            WorkflowConflict(
                "discovery-snapshot-integrity-failed",
                "The stored discovery state does not satisfy its public snapshot contract.",
            )
        )


@router.post(
    "/v1/projects/{project_id}/workflows/{workflow_id}/discovery/csl-json",
    response_model=CslJsonImportOut,
)
async def import_csl_json(
    project_id: str,
    workflow_id: str,
    file: UploadFile = File(...),
    idempotency_key: str = Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=200,
    ),
    session: Session = Depends(get_discovery_session),
) -> CslJsonImportOut:
    workflow = session.get(WorkflowRecord, workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail="Research workflow not found")
    content = await file.read(MAX_CSL_JSON_BYTES + 1)
    try:
        return import_csl_json_candidates(
            session,
            project_id=project_id,
            workflow=workflow,
            filename=file.filename or "citations.json",
            content=content,
            idempotency_key=idempotency_key,
        )
    except WorkflowConflict as error:
        session.rollback()
        _raise_conflict(error)
