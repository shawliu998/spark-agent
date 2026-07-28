from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import EvidenceSpanRecord, SourcePageRecord, SourceRecord, TaskRecord, WorkflowRecord
from ...pdf import PdfPage, locate_quote
from ..schemas import ExtractLocalEvidenceInput
from ..service import content_sha256
from ..state import WorkflowBlockedError, WorkflowFailure
from .lifecycle import previous_task
from .sources import validated_source_descriptors_for_task
from .text import (
    PassagePage,
    normalized_contains,
    rank_passages,
    select_diverse_passages,
)


def extract_local_evidence(
    session: Session,
    workflow: WorkflowRecord,
    task: TaskRecord,
    *,
    legacy_handler: bool,
) -> dict[str, Any]:
    previous = previous_task(session, task)
    descriptors = validated_source_descriptors_for_task(
        session,
        workflow,
        task,
        inspect_task=previous,
        allow_legacy_upgrade=legacy_handler,
    )
    source_ids = [descriptor.source_id for descriptor in descriptors]
    if not source_ids:
        raise WorkflowFailure(
            "source-selection-missing",
            "The source inspection step did not produce a source selection.",
        )
    payload = ExtractLocalEvidenceInput.model_validate(task.inputs)
    pages = list(
        session.scalars(
            select(SourcePageRecord)
            .where(SourcePageRecord.source_id.in_(source_ids))
            .order_by(SourcePageRecord.source_id, SourcePageRecord.page_index)
        )
    )
    candidates = rank_passages(payload.query, cast(list[PassagePage], pages))
    selected = select_diverse_passages(
        candidates,
        max_passages=payload.max_passages,
        max_per_source=payload.max_per_source,
    )
    pages_by_source: dict[str, list[PdfPage]] = defaultdict(list)
    for page in pages:
        pages_by_source[page.source_id].append(
            PdfPage(
                page_index=page.page_index,
                page_label=page.page_label,
                width=page.width,
                height=page.height,
                text=page.text,
                words=page.words,
            )
        )
    evidence_ids: list[str] = []
    evidence_fingerprints: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for candidate in selected:
        located = locate_quote(candidate.text, pages_by_source[candidate.source_id])
        if located is None or not located.verified:
            continue
        quote_hash = hashlib.sha256(located.text.encode("utf-8")).hexdigest()
        identity = (candidate.source_id, located.page_index, quote_hash)
        if identity in seen:
            continue
        seen.add(identity)
        evidence = EvidenceSpanRecord(
            id=str(uuid.uuid4()),
            source_id=candidate.source_id,
            page_index=located.page_index,
            page_label=located.page_label,
            text=located.text,
            bbox=located.bbox,
            coordinate_space="normalized-rotated-top-left-v1",
            quote_hash=quote_hash,
            extraction_method="local-token-overlap-v1",
            confidence=located.confidence,
            verified=True,
        )
        session.add(evidence)
        evidence_ids.append(evidence.id)
        evidence_fingerprints.append(evidence_fingerprint(evidence))
    if not evidence_ids:
        raise WorkflowBlockedError(
            "no-local-evidence",
            "No locally verifiable PDF passage matched the research goal.",
        )
    return {
        "evidenceIds": evidence_ids,
        "evidenceFingerprints": evidence_fingerprints,
        "sourceIds": source_ids,
    }


def evidence_fingerprint(evidence: EvidenceSpanRecord) -> dict[str, Any]:
    return {
        "bboxSha256": content_sha256(evidence.bbox),
        "coordinateSpace": evidence.coordinate_space,
        "evidenceId": evidence.id,
        "extractionMethod": evidence.extraction_method,
        "pageIndex": evidence.page_index,
        "pageLabel": evidence.page_label,
        "quoteHash": evidence.quote_hash,
        "sourceId": evidence.source_id,
        "textSha256": hashlib.sha256(evidence.text.encode("utf-8")).hexdigest(),
        "verified": evidence.verified,
    }


def page_contains_verified_quote(page: SourcePageRecord, quote: str) -> bool:
    if normalized_contains(page.text, quote):
        return True
    if not page.words:
        return False
    located = locate_quote(
        quote,
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
    return bool(
        located is not None
        and located.verified
        and normalized_contains(located.text, quote)
        and normalized_contains(quote, located.text)
    )


def validate_evidence_integrity(
    session: Session,
    workflow: WorkflowRecord,
    evidence_records: list[EvidenceSpanRecord],
) -> None:
    if len({evidence.id for evidence in evidence_records}) != len(evidence_records):
        raise WorkflowFailure(
            "evidence-selection-invalid",
            "The evidence selection contains duplicate records.",
        )
    for evidence in evidence_records:
        source = session.get(SourceRecord, evidence.source_id)
        page = session.scalar(
            select(SourcePageRecord).where(
                SourcePageRecord.source_id == evidence.source_id,
                SourcePageRecord.page_index == evidence.page_index,
            )
        )
        quote_hash_ok = evidence.quote_hash == hashlib.sha256(
            evidence.text.encode("utf-8")
        ).hexdigest()
        page_quote_ok = page is not None and page_contains_verified_quote(
            page, evidence.text
        )
        if not (
            source is not None
            and source.project_id == workflow.project_id
            and evidence.verified
            and quote_hash_ok
            and page is not None
            and page_quote_ok
        ):
            raise WorkflowFailure(
                "evidence-integrity-failed",
                "A selected evidence passage failed project, page, text, or quote-hash "
                "verification.",
            )
