from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Generator
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..db import database_session
from ..models import ProjectRecord, ScreeningDecisionRecord, SourceRecord, utc_now
from ..schemas import ScreeningDecisionOut, ScreeningDecisionUpsert

router = APIRouter(tags=["screening"])


def get_session() -> Generator[Session, None, None]:
    yield from database_session()


def _project_or_404(session: Session, project_id: str) -> ProjectRecord:
    project = session.get(ProjectRecord, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _screenable_source_or_404(
    session: Session, project_id: str, source_id: str
) -> SourceRecord:
    source = session.scalar(
        select(SourceRecord).where(
            SourceRecord.id == source_id,
            SourceRecord.project_id == project_id,
        )
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found in project")
    if source.source_kind != "pdf":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only PDF sources can have screening decisions",
        )
    if source.ingestion_status != "ready":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only ready PDF sources can have screening decisions",
        )
    return source


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
                detail="Screening decision is being updated; refresh and retry",
            ) from error
        raise


@router.get(
    "/v1/projects/{project_id}/screening-decisions",
    response_model=list[ScreeningDecisionOut],
)
def list_screening_decisions(
    project_id: str, session: Session = Depends(get_session)
) -> list[ScreeningDecisionRecord]:
    _project_or_404(session, project_id)
    return list(
        session.scalars(
            select(ScreeningDecisionRecord)
            .where(ScreeningDecisionRecord.project_id == project_id)
            .order_by(
                ScreeningDecisionRecord.updated_at.desc(),
                ScreeningDecisionRecord.source_id.asc(),
            )
        )
    )


@router.put(
    "/v1/projects/{project_id}/screening-decisions/{source_id}",
    response_model=ScreeningDecisionOut,
)
def upsert_screening_decision(
    project_id: str,
    source_id: str,
    payload: ScreeningDecisionUpsert,
    session: Session = Depends(get_session),
) -> ScreeningDecisionRecord:
    _begin_immediate(session)
    _project_or_404(session, project_id)
    _screenable_source_or_404(session, project_id, source_id)
    current = session.scalar(
        select(ScreeningDecisionRecord).where(
            ScreeningDecisionRecord.project_id == project_id,
            ScreeningDecisionRecord.source_id == source_id,
        )
    )
    if current is None:
        if payload.expected_version != 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Screening decision version is stale",
            )
        current = ScreeningDecisionRecord(
            id=str(uuid.uuid4()),
            project_id=project_id,
            source_id=source_id,
            decision=payload.decision,
            reason=payload.reason,
            criteria_version=payload.criteria_version,
            row_version=1,
        )
        session.add(current)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Screening decision version is stale",
            ) from error
        session.refresh(current)
        return current

    if current.row_version != payload.expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Screening decision version is stale",
        )
    result = cast(
        CursorResult[Any],
        session.execute(
            update(ScreeningDecisionRecord)
            .where(
                ScreeningDecisionRecord.id == current.id,
                ScreeningDecisionRecord.row_version == payload.expected_version,
            )
            .values(
                decision=payload.decision,
                reason=payload.reason,
                criteria_version=payload.criteria_version,
                row_version=payload.expected_version + 1,
                updated_at=utc_now(),
            )
        )
    )
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Screening decision version is stale",
        )
    session.commit()
    updated = session.get(ScreeningDecisionRecord, current.id)
    if updated is None:
        raise HTTPException(status_code=409, detail="Screening decision no longer exists")
    return updated
