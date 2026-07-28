from __future__ import annotations

import sqlite3
from collections.abc import Generator
from typing import Literal, Never

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..db import database_session
from ..models import ReportDraftRecord, WorkflowRecord
from ..workflow.report_draft_schemas import (
    CreateReportDraftIn,
    ExportReportDraftIn,
    ReportDraftExportOut,
    ReportDraftOut,
    ReviewReportDraftIn,
    SaveReportDraftIn,
)
from ..workflow.report_drafts import (
    create_report_draft,
    export_report_draft,
    refresh_report_draft_status,
    replay_report_draft_mutation,
    review_report_draft,
    save_report_draft,
)
from ..workflow.state import WorkflowFailure

router = APIRouter(tags=["report-drafts"])


def get_report_draft_session() -> Generator[Session, None, None]:
    yield from database_session()


def _begin_immediate(session: Session) -> None:
    try:
        session.execute(text("BEGIN IMMEDIATE"))
    except OperationalError as error:
        session.rollback()
        sqlite_error_code = getattr(error.orig, "sqlite_errorcode", None)
        base_error_code = (
            sqlite_error_code & 0xFF if isinstance(sqlite_error_code, int) else None
        )
        if base_error_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "report-draft-busy",
                    "userMessage": "The report draft is being updated. Reload and retry.",
                    "retryable": True,
                },
            ) from error
        raise


def _raise_conflict(error: WorkflowFailure) -> Never:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": error.code,
            "userMessage": error.user_message,
            "retryable": error.retryable,
        },
    ) from error


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


def _draft_or_404(
    session: Session,
    project_id: str,
    workflow_id: str,
    draft_id: str | None = None,
) -> ReportDraftRecord:
    query = select(ReportDraftRecord).where(
        ReportDraftRecord.project_id == project_id,
        ReportDraftRecord.workflow_id == workflow_id,
    )
    if draft_id is not None:
        query = query.where(ReportDraftRecord.id == draft_id)
    record = session.scalar(query)
    if record is None:
        raise HTTPException(status_code=404, detail="Report draft not found in workflow")
    return record


def _recover_concurrent_replay(
    session: Session,
    *,
    project_id: str,
    workflow_id: str,
    draft_id: str | None,
    operation: Literal["create", "save", "review"],
    payload: object,
    idempotency_key: str,
) -> ReportDraftRecord:
    session.rollback()
    _begin_immediate(session)
    _workflow_or_404(session, project_id, workflow_id)
    record = _draft_or_404(session, project_id, workflow_id, draft_id)
    try:
        replay_report_draft_mutation(
            session,
            record,
            operation=operation,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        session.commit()
        session.refresh(record)
        return record
    except WorkflowFailure as error:
        session.rollback()
        _raise_conflict(error)


@router.get(
    "/v1/projects/{project_id}/workflows/{workflow_id}/report-draft",
    response_model=ReportDraftOut,
)
def get_report_draft(
    project_id: str,
    workflow_id: str,
    session: Session = Depends(get_report_draft_session),
) -> ReportDraftRecord:
    _begin_immediate(session)
    workflow = _workflow_or_404(session, project_id, workflow_id)
    record = _draft_or_404(session, project_id, workflow_id)
    refresh_report_draft_status(session, workflow, record)
    session.commit()
    session.refresh(record)
    return record


@router.post(
    "/v1/projects/{project_id}/workflows/{workflow_id}/report-draft",
    response_model=ReportDraftOut,
    status_code=status.HTTP_201_CREATED,
)
def create_workflow_report_draft(
    project_id: str,
    workflow_id: str,
    payload: CreateReportDraftIn,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    session: Session = Depends(get_report_draft_session),
) -> ReportDraftRecord:
    _begin_immediate(session)
    workflow = _workflow_or_404(session, project_id, workflow_id)
    try:
        record = create_report_draft(session, workflow, payload, idempotency_key)
        session.commit()
        session.refresh(record)
        return record
    except WorkflowFailure as error:
        session.rollback()
        _raise_conflict(error)
    except IntegrityError:
        return _recover_concurrent_replay(
            session,
            project_id=project_id,
            workflow_id=workflow_id,
            draft_id=None,
            operation="create",
            payload=payload,
            idempotency_key=idempotency_key,
        )


@router.put(
    "/v1/projects/{project_id}/workflows/{workflow_id}/report-drafts/{draft_id}",
    response_model=ReportDraftOut,
)
def save_workflow_report_draft(
    project_id: str,
    workflow_id: str,
    draft_id: str,
    payload: SaveReportDraftIn,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    session: Session = Depends(get_report_draft_session),
) -> ReportDraftRecord:
    _begin_immediate(session)
    workflow = _workflow_or_404(session, project_id, workflow_id)
    record = _draft_or_404(session, project_id, workflow_id, draft_id)
    try:
        record = save_report_draft(session, workflow, record, payload, idempotency_key)
        session.commit()
        session.refresh(record)
        return record
    except WorkflowFailure as error:
        session.rollback()
        _raise_conflict(error)
    except IntegrityError:
        return _recover_concurrent_replay(
            session,
            project_id=project_id,
            workflow_id=workflow_id,
            draft_id=draft_id,
            operation="save",
            payload=payload,
            idempotency_key=idempotency_key,
        )


@router.post(
    "/v1/projects/{project_id}/workflows/{workflow_id}/report-drafts/{draft_id}/review",
    response_model=ReportDraftOut,
)
def review_workflow_report_draft(
    project_id: str,
    workflow_id: str,
    draft_id: str,
    payload: ReviewReportDraftIn,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    session: Session = Depends(get_report_draft_session),
) -> ReportDraftRecord:
    _begin_immediate(session)
    workflow = _workflow_or_404(session, project_id, workflow_id)
    record = _draft_or_404(session, project_id, workflow_id, draft_id)
    try:
        record = review_report_draft(session, workflow, record, payload, idempotency_key)
        session.commit()
        session.refresh(record)
        return record
    except WorkflowFailure as error:
        if error.code in {
            "report-draft-base-stale",
            "report-draft-rebase-invalid",
            "report-draft-rebase-required",
        }:
            session.commit()
        else:
            session.rollback()
        _raise_conflict(error)
    except IntegrityError:
        return _recover_concurrent_replay(
            session,
            project_id=project_id,
            workflow_id=workflow_id,
            draft_id=draft_id,
            operation="review",
            payload=payload,
            idempotency_key=idempotency_key,
        )


@router.post(
    "/v1/projects/{project_id}/workflows/{workflow_id}/report-drafts/{draft_id}/export",
    response_model=ReportDraftExportOut,
)
def export_workflow_report_draft(
    project_id: str,
    workflow_id: str,
    draft_id: str,
    payload: ExportReportDraftIn,
    session: Session = Depends(get_report_draft_session),
) -> ReportDraftExportOut:
    _begin_immediate(session)
    workflow = _workflow_or_404(session, project_id, workflow_id)
    record = _draft_or_404(session, project_id, workflow_id, draft_id)
    try:
        result = export_report_draft(session, workflow, record, payload)
        session.commit()
        return result
    except WorkflowFailure as error:
        session.commit()
        _raise_conflict(error)
