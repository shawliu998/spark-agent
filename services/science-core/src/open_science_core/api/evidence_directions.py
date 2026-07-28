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
    AnswerRecord,
    EvidenceDirectionJudgmentRecord,
    ProjectRecord,
    SourceRecord,
    utc_now,
)
from ..schemas import (
    EvidenceDirectionJudgmentOut,
    EvidenceDirectionJudgmentUpsert,
)

router = APIRouter(tags=["evidence-directions"])


def get_session() -> Generator[Session, None, None]:
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
                detail="Evidence direction is being updated; refresh and retry",
            ) from error
        raise


def _answer_or_404(
    session: Session,
    project_id: str,
    answer_id: str,
) -> AnswerRecord:
    project = session.get(ProjectRecord, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    answer = session.scalar(
        select(AnswerRecord).where(
            AnswerRecord.id == answer_id,
            AnswerRecord.project_id == project_id,
        )
    )
    if answer is None:
        raise HTTPException(status_code=404, detail="Answer not found in project")
    return answer


def _source_or_404(
    session: Session,
    project_id: str,
    source_id: str,
) -> SourceRecord:
    source = session.scalar(
        select(SourceRecord).where(
            SourceRecord.id == source_id,
            SourceRecord.project_id == project_id,
        )
    )
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found in project")
    if source.source_kind != "pdf" or source.ingestion_status != "ready":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Evidence direction requires an indexed PDF source",
        )
    return source


@router.get(
    "/v1/projects/{project_id}/answers/{answer_id}/evidence-directions",
    response_model=list[EvidenceDirectionJudgmentOut],
)
def list_evidence_direction_judgments(
    project_id: str,
    answer_id: str,
    session: Session = Depends(get_session),
) -> list[EvidenceDirectionJudgmentRecord]:
    _answer_or_404(session, project_id, answer_id)
    return list(
        session.scalars(
            select(EvidenceDirectionJudgmentRecord)
            .where(
                EvidenceDirectionJudgmentRecord.project_id == project_id,
                EvidenceDirectionJudgmentRecord.answer_id == answer_id,
            )
            .order_by(
                EvidenceDirectionJudgmentRecord.updated_at.desc(),
                EvidenceDirectionJudgmentRecord.source_id.asc(),
            )
        )
    )


@router.put(
    "/v1/projects/{project_id}/answers/{answer_id}/evidence-directions/{source_id}",
    response_model=EvidenceDirectionJudgmentOut,
)
def upsert_evidence_direction_judgment(
    project_id: str,
    answer_id: str,
    source_id: str,
    payload: EvidenceDirectionJudgmentUpsert,
    session: Session = Depends(get_session),
) -> EvidenceDirectionJudgmentRecord:
    _begin_immediate(session)
    _answer_or_404(session, project_id, answer_id)
    _source_or_404(session, project_id, source_id)
    current = session.scalar(
        select(EvidenceDirectionJudgmentRecord).where(
            EvidenceDirectionJudgmentRecord.project_id == project_id,
            EvidenceDirectionJudgmentRecord.answer_id == answer_id,
            EvidenceDirectionJudgmentRecord.source_id == source_id,
        )
    )
    if current is None:
        if payload.expected_version != 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Evidence direction version is stale",
            )
        current = EvidenceDirectionJudgmentRecord(
            id=str(uuid.uuid4()),
            project_id=project_id,
            answer_id=answer_id,
            source_id=source_id,
            direction=payload.direction,
            row_version=1,
        )
        session.add(current)
        try:
            session.commit()
        except IntegrityError as error:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Evidence direction version is stale",
            ) from error
        session.refresh(current)
        return current

    if current.row_version != payload.expected_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Evidence direction version is stale",
        )
    result = cast(
        CursorResult[Any],
        session.execute(
            update(EvidenceDirectionJudgmentRecord)
            .where(
                EvidenceDirectionJudgmentRecord.id == current.id,
                EvidenceDirectionJudgmentRecord.row_version == payload.expected_version,
            )
            .values(
                direction=payload.direction,
                row_version=payload.expected_version + 1,
                updated_at=utc_now(),
            )
        ),
    )
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Evidence direction version is stale",
        )
    session.commit()
    updated = session.get(EvidenceDirectionJudgmentRecord, current.id)
    if updated is None:
        raise HTTPException(status_code=409, detail="Evidence direction no longer exists")
    return updated
