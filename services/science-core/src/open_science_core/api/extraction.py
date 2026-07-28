from __future__ import annotations

import hashlib
import sqlite3
import uuid
from collections.abc import Generator, Sequence
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..db import database_session
from ..models import (
    AnswerRecord,
    ClaimEvidenceRecord,
    ClaimRecord,
    EvidenceSpanRecord,
    ExtractionCellEvidenceRecord,
    ExtractionCellRecord,
    ExtractionColumnRecord,
    ProjectRecord,
    ScreeningDecisionRecord,
    SourcePageRecord,
    SourceRecord,
    utc_now,
)
from ..pdf import PdfPage, locate_quote, normalize_text
from ..schemas import (
    BoundingBoxOut,
    EvidenceOut,
    ExactEvidenceSpanCreate,
    ExtractionCellDelete,
    ExtractionCellOut,
    ExtractionCellUpsert,
    ExtractionColumnCreate,
    ExtractionColumnOut,
    ExtractionMatrixOut,
)
from ..workflow._handlers.evidence import evidence_fingerprint
from ..workflow._handlers.sources import source_page_manifest_hash
from ..workflow._handlers.text import normalized_contains
from ..workflow.schemas import EvidenceRelationshipOut, WorkflowClaimOut, WorkflowResultOut
from ..workflow.service import content_sha256

router = APIRouter(tags=["extraction"])

DEFAULT_COLUMNS: tuple[str, ...] = ("Summary", "Population", "Outcome")


def get_session() -> Generator[Session, None, None]:
    yield from database_session()


def seed_default_columns(session: Session, project_id: str) -> None:
    """Seed only an empty matrix shape; values must always come from the user."""
    for order_index, name in enumerate(DEFAULT_COLUMNS):
        session.add(
            ExtractionColumnRecord(
                id=str(uuid.uuid4()),
                project_id=project_id,
                name=name,
                instructions=None,
                order_index=order_index,
                row_version=1,
            )
        )


def _project_or_404(session: Session, project_id: str) -> ProjectRecord:
    project = session.get(ProjectRecord, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _begin_immediate(session: Session) -> None:
    try:
        session.execute(text("BEGIN IMMEDIATE"))
    except OperationalError as error:
        session.rollback()
        code = getattr(error.orig, "sqlite_errorcode", None)
        base = code & 0xFF if isinstance(code, int) else None
        if base in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Extraction matrix is being updated; refresh and retry",
            ) from error
        raise


def _writeable_source_or_404(session: Session, project_id: str, source_id: str) -> SourceRecord:
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
            detail="Only ready PDF sources can have extraction cells",
        )
    decision = session.scalar(
        select(ScreeningDecisionRecord.decision).where(
            ScreeningDecisionRecord.project_id == project_id,
            ScreeningDecisionRecord.source_id == source_id,
        )
    )
    if decision == "exclude":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Excluded sources cannot receive new extraction cells",
        )
    return source


def _column_or_404(session: Session, project_id: str, column_id: str) -> ExtractionColumnRecord:
    column = session.scalar(
        select(ExtractionColumnRecord).where(
            ExtractionColumnRecord.project_id == project_id,
            ExtractionColumnRecord.id == column_id,
        )
    )
    if column is None:
        raise HTTPException(status_code=404, detail="Extraction column not found in project")
    return column


def _cell_out(cell: ExtractionCellRecord, evidence_ids: Sequence[str]) -> ExtractionCellOut:
    return ExtractionCellOut.model_validate(
        {
            "id": cell.id,
            "project_id": cell.project_id,
            "source_id": cell.source_id,
            "column_id": cell.column_id,
            "value": cell.value,
            "review_status": cell.review_status,
            "evidence_ids": list(evidence_ids),
            "row_version": cell.row_version,
            "created_at": cell.created_at,
            "updated_at": cell.updated_at,
        }
    )


def _matrix(session: Session, project_id: str) -> ExtractionMatrixOut:
    columns = list(
        session.scalars(
            select(ExtractionColumnRecord)
            .where(ExtractionColumnRecord.project_id == project_id)
            .order_by(ExtractionColumnRecord.order_index, ExtractionColumnRecord.id)
        )
    )
    cells = list(
        session.scalars(
            select(ExtractionCellRecord)
            .where(ExtractionCellRecord.project_id == project_id)
            .order_by(ExtractionCellRecord.source_id, ExtractionCellRecord.column_id)
        )
    )
    evidence_by_cell: dict[str, list[str]] = {cell.id: [] for cell in cells}
    if cells:
        for cell_id, evidence_id in session.execute(
            select(ExtractionCellEvidenceRecord.cell_id, ExtractionCellEvidenceRecord.evidence_id)
            .where(ExtractionCellEvidenceRecord.project_id == project_id)
            .order_by(
                ExtractionCellEvidenceRecord.cell_id, ExtractionCellEvidenceRecord.evidence_id
            )
        ):
            evidence_by_cell[str(cell_id)].append(str(evidence_id))
    return ExtractionMatrixOut(
        columns=[ExtractionColumnOut.model_validate(column) for column in columns],
        cells=[_cell_out(cell, evidence_by_cell[cell.id]) for cell in cells],
    )


def _validate_evidence_ids(session: Session, source_id: str, evidence_ids: Sequence[str]) -> None:
    if not evidence_ids:
        return
    found = set(
        session.scalars(
            select(EvidenceSpanRecord.id).where(
                EvidenceSpanRecord.source_id == source_id,
                EvidenceSpanRecord.id.in_(evidence_ids),
            )
        )
    )
    if found != set(evidence_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Evidence must belong to the extracted source",
        )


@router.post(
    "/v1/projects/{project_id}/sources/{source_id}/evidence-spans",
    response_model=EvidenceOut,
    status_code=status.HTTP_201_CREATED,
)
def create_exact_evidence_span(
    project_id: str,
    source_id: str,
    payload: ExactEvidenceSpanCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    session: Session = Depends(get_session),
) -> EvidenceSpanRecord:
    del idempotency_key  # Presence is enforced by FastAPI; semantic identity is content-derived.
    _project_or_404(session, project_id)
    source = _writeable_source_or_404(session, project_id, source_id)
    if source.content_hash != payload.expected_source_content_hash:
        raise HTTPException(status_code=409, detail="Source content hash is stale")
    page_manifest = source_page_manifest_hash(session, source_id)
    if page_manifest is None or page_manifest[0] != payload.expected_page_manifest_hash:
        raise HTTPException(status_code=409, detail="Source page manifest is stale")
    page = session.scalar(
        select(SourcePageRecord).where(
            SourcePageRecord.source_id == source_id,
            SourcePageRecord.page_index == payload.page_index,
        )
    )
    if page is None:
        raise HTTPException(status_code=422, detail="Requested source page was not found")
    normalized_quote = normalize_text(payload.quote_text)
    normalized_page = normalize_text(page.text)
    if not normalized_quote or normalized_page.count(normalized_quote) != 1:
        raise HTTPException(
            status_code=422,
            detail="Exact quote was not found exactly once on the requested page",
        )
    located = locate_quote(
        payload.quote_text,
        [
            PdfPage(
                page_index=page.page_index,
                page_label=page.page_label,
                width=page.width,
                height=page.height,
                text=page.text,
                words=page.words,
            )
        ],
    )
    if located is None or not located.verified or located.page_index != payload.page_index:
        raise HTTPException(
            status_code=422,
            detail="Exact quote could not be verified against the requested PDF page",
        )
    quote_hash = hashlib.sha256(located.text.encode("utf-8")).hexdigest()
    identity = "|".join(
        (
            project_id,
            source_id,
            str(payload.page_index),
            quote_hash,
            source.content_hash,
            page_manifest[0],
        )
    )
    evidence_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"spark:exact-evidence:{identity}"))
    existing = session.get(EvidenceSpanRecord, evidence_id)
    if existing is not None:
        return existing
    evidence = EvidenceSpanRecord(
        id=evidence_id,
        source_id=source_id,
        page_index=located.page_index,
        page_label=located.page_label,
        text=located.text,
        bbox=located.bbox,
        coordinate_space="normalized-rotated-top-left-v1",
        quote_hash=quote_hash,
        extraction_method="user-exact-quote+pdf-word-map-v1",
        confidence=located.confidence,
        verified=True,
    )
    session.add(evidence)
    session.commit()
    return evidence


@router.post(
    "/v1/projects/{project_id}/extraction/cited-brief",
    response_model=WorkflowResultOut,
    status_code=status.HTTP_201_CREATED,
)
def create_confirmed_extraction_cited_brief(
    project_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    session: Session = Depends(get_session),
) -> WorkflowResultOut:
    del idempotency_key  # The stable input projection is the semantic idempotency identity.
    _begin_immediate(session)
    _project_or_404(session, project_id)
    rows = list(
        session.execute(
            select(ExtractionCellRecord, ExtractionColumnRecord, SourceRecord)
            .join(
                ExtractionColumnRecord,
                ExtractionColumnRecord.id == ExtractionCellRecord.column_id,
            )
            .join(SourceRecord, SourceRecord.id == ExtractionCellRecord.source_id)
            .where(
                ExtractionCellRecord.project_id == project_id,
                ExtractionColumnRecord.project_id == project_id,
                SourceRecord.project_id == project_id,
                ExtractionCellRecord.review_status == "confirmed",
            )
            .order_by(
                SourceRecord.title,
                SourceRecord.id,
                ExtractionColumnRecord.order_index,
                ExtractionColumnRecord.id,
                ExtractionCellRecord.id,
            )
        )
    )
    cell_ids = [cell.id for cell, _column, _source in rows]
    evidence_ids_by_cell: dict[str, list[str]] = {cell_id: [] for cell_id in cell_ids}
    if cell_ids:
        for cell_id, evidence_id in session.execute(
            select(
                ExtractionCellEvidenceRecord.cell_id,
                ExtractionCellEvidenceRecord.evidence_id,
            )
            .where(
                ExtractionCellEvidenceRecord.project_id == project_id,
                ExtractionCellEvidenceRecord.cell_id.in_(cell_ids),
            )
            .order_by(
                ExtractionCellEvidenceRecord.cell_id,
                ExtractionCellEvidenceRecord.evidence_id,
            )
        ):
            evidence_ids_by_cell[str(cell_id)].append(str(evidence_id))
    eligible_rows = [
        (cell, column, source, evidence_ids_by_cell[cell.id])
        for cell, column, source in rows
        if evidence_ids_by_cell[cell.id]
    ]
    if not eligible_rows:
        session.rollback()
        raise HTTPException(
            status_code=422,
            detail="No confirmed extraction cells with verified evidence are available",
        )

    evidence_records: dict[str, EvidenceSpanRecord] = {}
    source_manifests: dict[str, str] = {}
    projection_cells: list[dict[str, Any]] = []
    for cell, column, source, evidence_ids in eligible_rows:
        if source.ingestion_status != "ready" or source.source_kind != "pdf":
            session.rollback()
            raise HTTPException(status_code=409, detail="A cited source is no longer ready")
        manifest = source_page_manifest_hash(session, source.id)
        if manifest is None:
            session.rollback()
            raise HTTPException(status_code=409, detail="A cited source page manifest is missing")
        source_manifests[source.id] = manifest[0]
        fingerprints: list[dict[str, Any]] = []
        for evidence_id in evidence_ids:
            evidence = session.get(EvidenceSpanRecord, evidence_id)
            page = (
                session.scalar(
                    select(SourcePageRecord).where(
                        SourcePageRecord.source_id == source.id,
                        SourcePageRecord.page_index == evidence.page_index,
                    )
                )
                if evidence is not None
                else None
            )
            if not (
                evidence is not None
                and evidence.source_id == source.id
                and evidence.verified
                and evidence.quote_hash
                == hashlib.sha256(evidence.text.encode("utf-8")).hexdigest()
                and page is not None
                and normalized_contains(page.text, evidence.text)
            ):
                session.rollback()
                raise HTTPException(
                    status_code=409,
                    detail="Confirmed extraction evidence failed project, source, or page verification",
                )
            evidence_records[evidence.id] = evidence
            fingerprints.append(evidence_fingerprint(evidence))
        projection_cells.append(
            {
                "cellId": cell.id,
                "cellRowVersion": cell.row_version,
                "columnId": column.id,
                "columnName": column.name,
                "columnOrder": column.order_index,
                "evidence": fingerprints,
                "sourceContentHash": source.content_hash,
                "sourceId": source.id,
                "sourcePageManifestHash": manifest[0],
                "value": cell.value,
            }
        )

    input_projection = {
        "cells": projection_cells,
        "projectId": project_id,
        "schemaVersion": "confirmed-extraction-cited-brief-input-v1",
    }
    input_sha256 = content_sha256(input_projection)
    answer_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"spark:confirmed-extraction-cited-brief:{project_id}:{input_sha256}",
        )
    )
    existing = session.get(AnswerRecord, answer_id)
    if existing is not None:
        snapshot = existing.metadata_json.get("resultSnapshot")
        snapshot_hash = existing.metadata_json.get("resultSnapshotSha256")
        if (
            existing.metadata_json.get("inputSha256") != input_sha256
            or not isinstance(snapshot, dict)
            or not isinstance(snapshot_hash, str)
            or content_sha256(snapshot) != snapshot_hash
        ):
            session.rollback()
            raise HTTPException(status_code=409, detail="Stored cited brief snapshot is invalid")
        session.rollback()
        return WorkflowResultOut.model_validate(snapshot)

    answer = AnswerRecord(
        id=answer_id,
        project_id=project_id,
        workflow_id=None,
        task_id=None,
        question="Confirmed extraction cited brief",
        answer=(
            f"Confirmed extraction brief: {len(eligible_rows)} finding"
            f"{'s' if len(eligible_rows) != 1 else ''} from "
            f"{len({source.id for _cell, _column, source, _ids in eligible_rows})} "
            "local PDF source"
            f"{'s' if len({source.id for _cell, _column, source, _ids in eligible_rows}) != 1 else ''}."
        ),
        unresolved_questions=[
            "These findings preserve human-confirmed extraction values; broader interpretation requires separate review."
        ],
        generator="confirmed-extraction-cited-brief-v1",
        model=None,
        prompt_version="confirmed-extraction-cited-brief-v1",
        metadata_json={},
    )
    session.add(answer)
    claim_outputs: list[WorkflowClaimOut] = []
    claim_order: list[str] = []
    for cell, column, source, evidence_ids in eligible_rows:
        claim_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"spark:confirmed-extraction-claim:{answer_id}:{cell.id}:{cell.row_version}",
            )
        )
        claim_order.append(claim_id)
        confidence = min(evidence_records[evidence_id].confidence for evidence_id in evidence_ids)
        claim = ClaimRecord(
            id=claim_id,
            answer_id=answer.id,
            statement=f"{column.name}: {cell.value}",
            claim_type="finding",
            confidence=confidence,
            review_status="verified",
        )
        session.add(claim)
        evidence_outputs: list[EvidenceRelationshipOut] = []
        for evidence_id in evidence_ids:
            evidence = evidence_records[evidence_id]
            session.add(
                ClaimEvidenceRecord(
                    claim_id=claim.id,
                    evidence_id=evidence.id,
                    relationship_kind="supporting",
                )
            )
            evidence_outputs.append(
                EvidenceRelationshipOut(
                    evidence_id=evidence.id,
                    source_id=evidence.source_id,
                    source_title=source.title,
                    source_content_hash=source.content_hash,
                    source_page_manifest_hash=source_manifests[source.id],
                    page_index=evidence.page_index,
                    page_label=evidence.page_label,
                    text=evidence.text,
                    bbox=(
                        BoundingBoxOut.model_validate(evidence.bbox)
                        if evidence.bbox is not None
                        else None
                    ),
                    coordinate_space=cast(
                        Literal["normalized-rotated-top-left-v1"],
                        evidence.coordinate_space,
                    ),
                    quote_hash=evidence.quote_hash,
                    extraction_method=evidence.extraction_method,
                    confidence=evidence.confidence,
                    verified=True,
                    relationship="supporting",
                )
            )
        claim_outputs.append(
            WorkflowClaimOut(
                id=claim.id,
                statement=claim.statement,
                support_status="supported",
                confidence=claim.confidence,
                evidence=evidence_outputs,
            )
        )
    result = WorkflowResultOut(
        answer_id=answer.id,
        summary=answer.answer,
        generator=answer.generator,
        model=None,
        prompt_version=answer.prompt_version,
        integrity_status="unfrozen",
        claims=claim_outputs,
        unresolved_questions=answer.unresolved_questions,
    )
    snapshot = result.model_dump(mode="json", by_alias=True, exclude_none=False)
    answer.metadata_json = {
        "claimOrder": claim_order,
        "inputProjection": input_projection,
        "inputSha256": input_sha256,
        "resultSnapshot": snapshot,
        "resultSnapshotSha256": content_sha256(snapshot),
        "schemaVersion": "confirmed-extraction-cited-brief-v1",
    }
    session.commit()
    return result


@router.get("/v1/projects/{project_id}/extraction", response_model=ExtractionMatrixOut)
def get_extraction_matrix(
    project_id: str, session: Session = Depends(get_session)
) -> ExtractionMatrixOut:
    _project_or_404(session, project_id)
    return _matrix(session, project_id)


@router.post(
    "/v1/projects/{project_id}/extraction/columns",
    response_model=ExtractionColumnOut,
    status_code=status.HTTP_201_CREATED,
)
def create_extraction_column(
    project_id: str,
    payload: ExtractionColumnCreate,
    session: Session = Depends(get_session),
) -> ExtractionColumnRecord:
    _begin_immediate(session)
    _project_or_404(session, project_id)
    next_order = int(
        session.scalar(
            select(func.coalesce(func.max(ExtractionColumnRecord.order_index) + 1, 0)).where(
                ExtractionColumnRecord.project_id == project_id
            )
        )
        or 0
    )
    record = ExtractionColumnRecord(
        id=str(uuid.uuid4()),
        project_id=project_id,
        name=payload.name,
        instructions=payload.instructions,
        order_index=next_order,
        row_version=1,
    )
    session.add(record)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            409, detail="Extraction column order is stale; refresh and retry"
        ) from error
    return record


@router.put(
    "/v1/projects/{project_id}/extraction/cells/{source_id}/{column_id}",
    response_model=ExtractionCellOut,
)
def upsert_extraction_cell(
    project_id: str,
    source_id: str,
    column_id: str,
    payload: ExtractionCellUpsert,
    session: Session = Depends(get_session),
) -> ExtractionCellOut:
    _begin_immediate(session)
    _project_or_404(session, project_id)
    _writeable_source_or_404(session, project_id, source_id)
    _column_or_404(session, project_id, column_id)
    _validate_evidence_ids(session, source_id, payload.evidence_ids)
    cell = session.scalar(
        select(ExtractionCellRecord).where(
            ExtractionCellRecord.project_id == project_id,
            ExtractionCellRecord.source_id == source_id,
            ExtractionCellRecord.column_id == column_id,
        )
    )
    if cell is None:
        if payload.expected_version != 0:
            raise HTTPException(409, detail="Extraction cell version is stale")
        cell = ExtractionCellRecord(
            id=str(uuid.uuid4()),
            project_id=project_id,
            source_id=source_id,
            column_id=column_id,
            value=payload.value,
            review_status=payload.review_status,
            row_version=1,
        )
        session.add(cell)
    else:
        # Do not rely on a prior read for CAS: another connection may have
        # committed between it and this write.  Keep the version predicate in
        # the statement that mutates the row.
        updated = cast(
            CursorResult[Any],
            session.execute(
                update(ExtractionCellRecord)
                .where(
                    ExtractionCellRecord.id == cell.id,
                    ExtractionCellRecord.project_id == project_id,
                    ExtractionCellRecord.source_id == source_id,
                    ExtractionCellRecord.column_id == column_id,
                    ExtractionCellRecord.row_version == payload.expected_version,
                )
                .values(
                    value=payload.value,
                    review_status=payload.review_status,
                    row_version=ExtractionCellRecord.row_version + 1,
                    updated_at=utc_now(),
                )
            )
        )
        if updated.rowcount != 1:
            session.rollback()
            raise HTTPException(409, detail="Extraction cell version is stale")
        session.expire(cell)
        session.refresh(cell)
    session.flush()
    session.execute(
        delete(ExtractionCellEvidenceRecord).where(
            ExtractionCellEvidenceRecord.project_id == project_id,
            ExtractionCellEvidenceRecord.cell_id == cell.id,
        )
    )
    for evidence_id in payload.evidence_ids:
        session.add(
            ExtractionCellEvidenceRecord(
                project_id=project_id, cell_id=cell.id, source_id=source_id, evidence_id=evidence_id
            )
        )
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(409, detail="Extraction cell version is stale") from error
    return _cell_out(cell, payload.evidence_ids)


@router.delete(
    "/v1/projects/{project_id}/extraction/cells/{source_id}/{column_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_extraction_cell(
    project_id: str,
    source_id: str,
    column_id: str,
    payload: ExtractionCellDelete,
    session: Session = Depends(get_session),
) -> None:
    _begin_immediate(session)
    _project_or_404(session, project_id)
    _column_or_404(session, project_id, column_id)
    deleted = cast(
        CursorResult[Any],
        session.execute(
            delete(ExtractionCellRecord).where(
                ExtractionCellRecord.project_id == project_id,
                ExtractionCellRecord.source_id == source_id,
                ExtractionCellRecord.column_id == column_id,
                ExtractionCellRecord.row_version == payload.expected_version,
            )
        )
    )
    if deleted.rowcount != 1:
        session.rollback()
        raise HTTPException(409, detail="Extraction cell version is stale")
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(409, detail="Extraction cell version is stale") from error
