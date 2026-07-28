"""Atomic local CSL-JSON import into the existing untrusted Candidate ledger."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    CandidateOccurrenceRecord,
    DiscoverySpecRecord,
    EventRecord,
    JobRecord,
    ToolInvocationRecord,
    WorkflowRecord,
    utc_now,
)
from ._service.integrity import WorkflowConflict, content_sha256
from .discovery_adapter import persist_discovery_candidates
from .discovery_schemas import (
    CslJsonImportOut,
    DiscoveryCandidate,
    discovery_candidate_sha256,
)

CSL_JSON_PARSER_VERSION = "csl-json-file-v1"
MAX_CSL_JSON_ITEMS = 500
MAX_CSL_JSON_BYTES = 2 * 1024 * 1024


def import_csl_json_candidates(
    session: Session,
    *,
    project_id: str,
    workflow: WorkflowRecord,
    filename: str,
    content: bytes,
    idempotency_key: str,
) -> CslJsonImportOut:
    """Parse the whole file first, then persist one all-or-nothing local occurrence."""

    if workflow.project_id != project_id:
        raise WorkflowConflict(
            "csl-json-workflow-project-mismatch",
            "The selected workflow does not belong to this project.",
        )
    if workflow.workflow_type != "literature-synthesis":
        raise WorkflowConflict(
            "csl-json-workflow-mismatch",
            "CSL-JSON metadata can only be added to a literature workflow.",
        )
    if len(content) > MAX_CSL_JSON_BYTES:
        raise WorkflowConflict("csl-json-too-large", "The CSL-JSON file exceeds 2 MB.")
    file_sha256 = hashlib.sha256(content).hexdigest()
    safe_filename = Path(filename or "citations.json").name

    replay = _find_replay(
        session,
        project_id=project_id,
        workflow_id=workflow.id,
        file_sha256=file_sha256,
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        return replay

    spec = session.scalar(
        select(DiscoverySpecRecord).where(
            DiscoverySpecRecord.workflow_id == workflow.id,
            DiscoverySpecRecord.status.in_(["pending-approval", "approved"]),
        )
    )
    if spec is None:
        raise WorkflowConflict(
            "csl-json-discovery-missing",
            "The literature workflow has no current Discovery specification.",
        )

    candidates, raw_hashes = _parse_csl_json(content)
    request_projection = {
        "schemaVersion": "1",
        "parserVersion": CSL_JSON_PARSER_VERSION,
        "projectId": project_id,
        "workflowId": workflow.id,
        "discoverySpecId": spec.id,
        "fileSha256": file_sha256,
        "itemCount": len(candidates),
    }
    request_sha256 = content_sha256(request_projection)
    stable_key = f"csl:{project_id}:{workflow.id}:{file_sha256}"
    now = utc_now()
    job = JobRecord(
        id=str(uuid.uuid4()),
        workflow_id=workflow.id,
        task_id=None,
        kind="execute-task",
        operation_key=stable_key,
        attempt=1,
        input_sha256=request_sha256,
        handler_version=CSL_JSON_PARSER_VERSION,
        status="succeeded",
        request_idempotency_key=stable_key,
        request_payload_sha256=request_sha256,
        finished_at=now,
    )
    session.add(job)
    session.flush()

    invocation = ToolInvocationRecord(
        id=str(uuid.uuid4()),
        project_id=project_id,
        workflow_id=workflow.id,
        discovery_spec_id=spec.id,
        job_id=job.id,
        schema_version="1",
        tool_name="import_csl_json",
        connector_name="local-csl-json",
        connector_version="1",
        query_id="query-csl-json-file",
        provider="csl-json-file",
        operation_key=stable_key,
        attempt=1,
        request_idempotency_key=stable_key,
        request_payload_sha256=request_sha256,
        request_json=request_projection,
        output_sha256=content_sha256(
            {
                "parserVersion": CSL_JSON_PARSER_VERSION,
                "candidateHashes": [
                    discovery_candidate_sha256(candidate) for candidate in candidates
                ],
            }
        ),
        returned_count=len(candidates),
        novel_candidate_count=0,
        duplicate_count=0,
        candidate_set_sha256=content_sha256(
            {
                "candidateHashes": sorted(
                    discovery_candidate_sha256(candidate) for candidate in candidates
                )
            }
        ),
        status="succeeded",
        finished_at=now,
    )
    session.add(invocation)
    session.flush()
    imported_count, unchanged_count = persist_discovery_candidates(
        session,
        invocation=invocation,
        project_id=project_id,
        candidates=candidates,
        raw_hashes=raw_hashes,
    )
    invocation.novel_candidate_count = imported_count
    invocation.duplicate_count = unchanged_count
    session.flush()
    candidate_ids = list(
        session.scalars(
            select(CandidateOccurrenceRecord.candidate_id)
            .where(CandidateOccurrenceRecord.invocation_id == invocation.id)
            .order_by(CandidateOccurrenceRecord.rank)
        )
    )
    event = EventRecord(
        id=str(uuid.uuid4()),
        project_id=project_id,
        workflow_id=workflow.id,
        job_id=job.id,
        event_type="discovery.csl-json-imported",
        payload={
            "schemaVersion": "1",
            "parserVersion": CSL_JSON_PARSER_VERSION,
            "idempotencyKey": idempotency_key,
            "discoverySpecId": spec.id,
            "invocationId": invocation.id,
            "fileSha256": file_sha256,
            "filename": safe_filename,
            "itemCount": len(candidates),
            "importedCount": imported_count,
            "unchangedCount": unchanged_count,
            "candidateIds": candidate_ids,
        },
    )
    session.add(event)
    session.commit()
    return CslJsonImportOut(
        schema_version="1",
        project_id=project_id,
        workflow_id=workflow.id,
        invocation_id=invocation.id,
        file_sha256=cast(Any, file_sha256),
        parser_version=CSL_JSON_PARSER_VERSION,
        imported_count=imported_count,
        unchanged_count=unchanged_count,
        candidate_ids=candidate_ids,
        replayed=False,
    )


def _find_replay(
    session: Session,
    *,
    project_id: str,
    workflow_id: str,
    file_sha256: str,
    idempotency_key: str,
) -> CslJsonImportOut | None:
    for event in session.scalars(
        select(EventRecord).where(
            EventRecord.project_id == project_id,
            EventRecord.workflow_id == workflow_id,
            EventRecord.event_type == "discovery.csl-json-imported",
        )
    ):
        payload = event.payload
        if payload.get("idempotencyKey") == idempotency_key and payload.get(
            "fileSha256"
        ) != file_sha256:
            raise WorkflowConflict(
                "idempotency-key-reused",
                "This Idempotency-Key belongs to a different CSL-JSON file.",
            )
        if payload.get("fileSha256") != file_sha256:
            continue
        candidate_ids_value: object = payload.get("candidateIds")
        invocation_id = payload.get("invocationId")
        item_count = payload.get("itemCount")
        if (
            not isinstance(candidate_ids_value, list)
            or not all(
                isinstance(item, str) for item in cast(list[object], candidate_ids_value)
            )
            or not isinstance(invocation_id, str)
            or not isinstance(item_count, int)
            or session.get(ToolInvocationRecord, invocation_id) is None
        ):
            raise WorkflowConflict(
                "csl-json-replay-integrity-failed",
                "The stored CSL-JSON import provenance is incomplete.",
            )
        return CslJsonImportOut(
            schema_version="1",
            project_id=project_id,
            workflow_id=workflow_id,
            invocation_id=invocation_id,
            file_sha256=cast(Any, file_sha256),
            parser_version=CSL_JSON_PARSER_VERSION,
            imported_count=0,
            unchanged_count=item_count,
            candidate_ids=cast(list[str], candidate_ids_value),
            replayed=True,
        )
    return None


def _parse_csl_json(content: bytes) -> tuple[list[DiscoveryCandidate], list[str]]:
    try:
        decoded = content.decode("utf-8")
        value = cast(object, json.loads(decoded))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowConflict("csl-json-invalid", "The file is not valid UTF-8 CSL-JSON.") from error
    if isinstance(value, dict):
        items: list[object] = [cast(dict[str, object], value)]
    elif isinstance(value, list):
        items = cast(list[object], value)
    else:
        items = []
    if not items or len(items) > MAX_CSL_JSON_ITEMS:
        raise WorkflowConflict(
            "csl-json-invalid",
            "CSL-JSON must contain one object or an array of 1 to 500 objects.",
        )
    candidates: list[DiscoveryCandidate] = []
    raw_hashes: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise WorkflowConflict(
                "csl-json-invalid",
                f"CSL-JSON item {index + 1} is not an object.",
            )
        typed_item = cast(dict[str, object], item)
        raw_hash = content_sha256(typed_item)
        try:
            candidates.append(_candidate_from_item(typed_item, raw_hash))
        except (TypeError, ValueError, ValidationError) as error:
            raise WorkflowConflict(
                "csl-json-invalid",
                f"CSL-JSON item {index + 1} is invalid.",
            ) from error
        raw_hashes.append(raw_hash)
    return candidates, raw_hashes


def _candidate_from_item(item: dict[str, object], raw_hash: str) -> DiscoveryCandidate:
    title = _optional_text(item.get("title"), 1_000)
    if title is None:
        raise ValueError("title is required")
    doi = _optional_text(item.get("DOI"), 255)
    if doi is not None:
        doi = doi.removeprefix("https://doi.org/").removeprefix("doi:").strip().lower()
    csl_id = _optional_text(item.get("id"), 300)
    provider_id = csl_id or doi or raw_hash
    authors = _authors(item.get("author"))
    return DiscoveryCandidate(
        provider="csl-json-file",
        provider_id=provider_id,
        title=title,
        authors=authors,
        abstract=_optional_text(item.get("abstract"), 20_000),
        publication_date=_issued(item.get("issued")),
        doi=doi,
        arxiv_id=None,
        pmid=None,
        landing_url=None,
        open_access_pdf_url=None,
    )


def _optional_text(value: object, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, (str, int)):
        raise TypeError("expected text")
    normalized = " ".join(str(value).split())
    if not normalized or len(normalized) > maximum:
        raise ValueError("text is empty or too long")
    return normalized


def _authors(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("author must be a bounded array")
    author_items = cast(list[object], value)
    if len(author_items) > 200:
        raise TypeError("author must be a bounded array")
    result: list[str] = []
    for author in author_items:
        if not isinstance(author, dict):
            raise TypeError("author entry must be an object")
        typed_author = cast(dict[str, object], author)
        literal = _optional_text(typed_author.get("literal"), 300)
        if literal is None:
            given = _optional_text(typed_author.get("given"), 150)
            family = _optional_text(typed_author.get("family"), 150)
            literal = " ".join(part for part in (given, family) if part)
        if not literal:
            raise ValueError("author name is empty")
        if literal not in result:
            result.append(literal)
    return result


def _issued(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError("issued must be an object")
    typed_value = cast(dict[str, object], value)
    date_parts = typed_value.get("date-parts")
    if not isinstance(date_parts, list) or not date_parts or not isinstance(date_parts[0], list):
        raise ValueError("issued.date-parts is invalid")
    parts = cast(list[object], date_parts[0])
    if not 1 <= len(parts) <= 3 or not all(type(part) is int for part in parts):
        raise ValueError("issued.date-parts is invalid")
    year, *tail = cast(list[int], parts)
    if not 1000 <= year <= 9999:
        raise ValueError("issued year is invalid")
    if tail and not 1 <= tail[0] <= 12:
        raise ValueError("issued month is invalid")
    if len(tail) == 2 and not 1 <= tail[1] <= 31:
        raise ValueError("issued day is invalid")
    return "-".join([f"{year:04d}", *(f"{part:02d}" for part in tail)])


__all__ = ("import_csl_json_candidates",)
