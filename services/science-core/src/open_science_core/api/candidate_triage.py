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
from ..models import (
    CandidateTriageDecisionRecord,
    DiscoveryCandidateRecord,
    ProjectRecord,
    utc_now,
)
from ..schemas import (
    CandidateTriageDecisionOut,
    CandidateTriageDecisionUpsert,
)

router = APIRouter(tags=["candidate-triage"])


def get_candidate_triage_session() -> Generator[Session, None, None]:
    yield from database_session()


def _project_or_404(session: Session, project_id: str) -> ProjectRecord:
    project = session.get(ProjectRecord, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _candidate_or_404(
    session: Session,
    project_id: str,
    candidate_id: str,
) -> DiscoveryCandidateRecord:
    candidate = session.scalar(
        select(DiscoveryCandidateRecord).where(
            DiscoveryCandidateRecord.id == candidate_id,
            DiscoveryCandidateRecord.project_id == project_id,
        )
    )
    if candidate is None:
        raise HTTPException(
            status_code=404,
            detail="Discovery candidate not found in project",
        )
    return candidate


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
                detail="Candidate triage decision is being updated; refresh and retry",
            ) from error
        raise


@router.get(
    "/v1/projects/{project_id}/candidate-triage-decisions",
    response_model=list[CandidateTriageDecisionOut],
)
def list_candidate_triage_decisions(
    project_id: str,
    session: Session = Depends(get_candidate_triage_session),
) -> list[CandidateTriageDecisionRecord]:
    _project_or_404(session, project_id)
    return list(
        session.scalars(
            select(CandidateTriageDecisionRecord)
            .where(CandidateTriageDecisionRecord.project_id == project_id)
            .order_by(
                CandidateTriageDecisionRecord.updated_at.desc(),
                CandidateTriageDecisionRecord.candidate_id.asc(),
            )
        )
    )


@router.put(
    "/v1/projects/{project_id}/candidate-triage-decisions/{candidate_id}",
    response_model=CandidateTriageDecisionOut,
)
def upsert_candidate_triage_decision(
    project_id: str,
    candidate_id: str,
    payload: CandidateTriageDecisionUpsert,
    session: Session = Depends(get_candidate_triage_session),
) -> CandidateTriageDecisionRecord:
    _begin_immediate(session)
    _project_or_404(session, project_id)
    _candidate_or_404(session, project_id, candidate_id)
    current = session.scalar(
        select(CandidateTriageDecisionRecord).where(
            CandidateTriageDecisionRecord.project_id == project_id,
            CandidateTriageDecisionRecord.candidate_id == candidate_id,
        )
    )
    if current is None:
        if payload.expected_version != 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Candidate triage decision version is stale",
            )
        current = CandidateTriageDecisionRecord(
            id=str(uuid.uuid4()),
            project_id=project_id,
            candidate_id=candidate_id,
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
                detail="Candidate triage decision version is stale",
            ) from error
        session.refresh(current)
        return current

    if current.row_version != payload.expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Candidate triage decision version is stale",
        )
    result = cast(
        CursorResult[Any],
        session.execute(
            update(CandidateTriageDecisionRecord)
            .where(
                CandidateTriageDecisionRecord.id == current.id,
                CandidateTriageDecisionRecord.row_version
                == payload.expected_version,
            )
            .values(
                decision=payload.decision,
                reason=payload.reason,
                criteria_version=payload.criteria_version,
                row_version=payload.expected_version + 1,
                updated_at=utc_now(),
            )
        ),
    )
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Candidate triage decision version is stale",
        )
    session.commit()
    updated = session.get(CandidateTriageDecisionRecord, current.id)
    if updated is None:
        raise HTTPException(
            status_code=409,
            detail="Candidate triage decision no longer exists",
        )
    return updated
