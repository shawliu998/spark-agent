from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import socket
import uuid
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, selectinload

from . import __version__
from .analysis import (
    sha256_file,
    validate_csv,
)
from .analysis_service import (
    AnalysisServiceError,
    analysis_intent_out,
    cleanup_stale_analysis_exchange,
    create_standalone_analysis_intent,
    decide_standalone_analysis_intent,
    execute_standalone_analysis_intent,
    list_project_analysis_runs,
    recover_interrupted_analysis_state,
)
from .api.agent_runs import router as agent_run_router
from .api.candidate_triage import router as candidate_triage_router
from .api.discovery import router as discovery_router
from .api.evidence_directions import router as evidence_direction_router
from .api.extraction import router as extraction_router
from .api.extraction import seed_default_columns
from .api.report_drafts import router as report_draft_router
from .api.research_memory import router as research_memory_router
from .api.screening import router as screening_router
from .api.workflows import router as workflow_router
from .config import canonical_model_api_endpoint, settings
from .db import SessionLocal, database_session, engine, initialize_database
from .literature import paper_qa, paper_qa_available
from .models import (
    AnswerRecord,
    ArtifactRecord,
    CandidateOccurrenceRecord,
    CandidateTriageDecisionRecord,
    ClaimEvidenceRecord,
    ClaimRecord,
    DiscoveryCandidateRecord,
    EventRecord,
    EvidenceSpanRecord,
    ProjectRecord,
    RunRecord,
    SourcePageRecord,
    SourceRecord,
    TaskRecord,
    ToolInvocationRecord,
    WorkflowRecord,
    utc_now,
)
from .pdf import LocatedQuote, PdfPage, extract_pdf, locate_quote
from .schemas import (
    AnalysisDecisionIn,
    AnalysisIntentCreate,
    AnalysisIntentOut,
    AnalysisRunOut,
    AnswerOut,
    ClaimOut,
    DiscoverySourceLineageOut,
    EvidenceOut,
    HealthOut,
    ModelGatewayDestinationOut,
    ProjectCreate,
    ProjectOut,
    ProjectRename,
    ProjectStateMutation,
    QuestionIn,
    SourceOut,
)
from .secure_download import DownloadErrorDetails, SecureDownloadError, secure_download_response
from .workflow._handlers.sources import source_page_manifest_hash
from .workflow.discovery_schemas import DiscoveryCandidate
from .workflow.discovery_service import discovery_candidate_from_record
from .workflow.worker import WorkflowWorker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    if not settings.bearer_token:
        raise RuntimeError(
            "SPARK_AGENT_CORE_TOKEN is required; science-core will not start without authentication"
        )
    initialize_database()
    with SessionLocal() as session:
        recover_interrupted_analysis_state(session)
        session.commit()
    cleanup_stale_analysis_exchange()
    workflow_worker = WorkflowWorker()
    await workflow_worker.start()
    try:
        yield
    finally:
        await workflow_worker.stop()


app = FastAPI(title="Spark Agent Core", version=__version__, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "tauri://localhost",
        "https://tauri.localhost",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = settings.bearer_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Science-core authentication is not configured",
        )
    supplied = authorization.removeprefix("Bearer ") if authorization else ""
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid science-core token"
        )


def get_session() -> Generator[Session, None, None]:
    yield from database_session()


def _analysis_http_exception(error: AnalysisServiceError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.detail)


app.include_router(workflow_router, dependencies=[Depends(require_token)])
app.include_router(agent_run_router, dependencies=[Depends(require_token)])
app.include_router(candidate_triage_router, dependencies=[Depends(require_token)])
app.include_router(discovery_router, dependencies=[Depends(require_token)])
app.include_router(evidence_direction_router, dependencies=[Depends(require_token)])
app.include_router(screening_router, dependencies=[Depends(require_token)])
app.include_router(extraction_router, dependencies=[Depends(require_token)])
app.include_router(research_memory_router, dependencies=[Depends(require_token)])
app.include_router(report_draft_router, dependencies=[Depends(require_token)])


def _runtime_ready() -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            client.connect(str(settings.runtime_socket_path))
            client.sendall(b"GET /health HTTP/1.0\r\nHost: science-runtime\r\n\r\n")
            response = b"".join(iter(lambda: client.recv(4096), b""))
        status_line = response.split(b"\r\n", 1)[0]
        return b" 200 " in status_line and b'"status":"ok"' in response
    except OSError:
        return False


@app.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    database = "ok"
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        database = "error"
    runtime = "ready" if _runtime_ready() else "unavailable"
    model_destination = (
        ModelGatewayDestinationOut(
            provider="openai-compatible",
            endpoint_host=urlsplit(settings.openai_api_base).hostname or "",
            endpoint_identity=_model_endpoint_identity(settings.openai_api_base),
            model=settings.llm_model or "",
        )
        if settings.model_gateway_configured
        else None
    )
    return HealthOut(
        status="ok" if database == "ok" and runtime == "ready" else "degraded",
        version=__version__,
        database=database,
        paper_qa="available" if paper_qa_available() else "unavailable",
        model_gateway="configured" if settings.model_gateway_configured else "unconfigured",
        model_destination=model_destination,
        runtime=runtime,
    )


@app.get("/v1/projects", response_model=list[ProjectOut], dependencies=[Depends(require_token)])
def list_projects(
    include_archived: bool = Query(default=False, alias="includeArchived"),
    session: Session = Depends(get_session),
) -> list[ProjectRecord]:
    query = select(ProjectRecord)
    if not include_archived:
        query = query.where(ProjectRecord.archived_at.is_(None))
    return list(session.scalars(query.order_by(ProjectRecord.updated_at.desc())))


@app.post("/v1/projects", response_model=ProjectOut, dependencies=[Depends(require_token)])
def create_project(
    payload: ProjectCreate, session: Session = Depends(get_session)
) -> ProjectRecord:
    project_id = str(uuid.uuid4())
    project_path = (settings.data_dir / "projects" / project_id).resolve()
    project_path.mkdir(parents=True, exist_ok=False)
    for relative in ("papers", "data/raw", "notebooks", "artifacts", "runs"):
        (project_path / relative).mkdir(parents=True, exist_ok=True)
    record = ProjectRecord(
        id=project_id,
        title=payload.title,
        description=payload.description,
        project_path=str(project_path),
        research_domain=payload.research_domain,
        execution_mode="safe",
        row_version=1,
        archived_at=None,
    )
    session.add(record)
    # Flush the parent first. EventRecord deliberately has no ORM relationship,
    # so SQLAlchemy cannot infer insert ordering from in-memory object links.
    session.flush()
    seed_default_columns(session, project_id)
    session.add(
        EventRecord(
            id=str(uuid.uuid4()),
            project_id=project_id,
            event_type="project.created",
            payload={"title": payload.title},
        )
    )
    session.commit()
    return record


_ACTIVE_PROJECT_WORKFLOW_STATUSES = (
    "routing",
    "waiting-clarification",
    "planning",
    "waiting-plan-approval",
    "running",
    "reviewing",
)


def _project_mutation_event(
    session: Session,
    project_id: str,
    event_type: str,
    idempotency_key: str,
) -> EventRecord | None:
    events = session.scalars(
        select(EventRecord)
        .where(EventRecord.project_id == project_id, EventRecord.event_type == event_type)
        .order_by(EventRecord.created_at.desc())
    )
    for event in events:
        if event.payload.get("idempotencyKey") == idempotency_key:
            return event
    return None


def _project_mutation_request_sha256(
    *,
    action: str,
    project_id: str,
    expected_row_version: int,
    title: str | None = None,
) -> str:
    request = {
        "action": action,
        "expectedRowVersion": expected_row_version,
        "projectId": project_id,
    }
    if title is not None:
        request["title"] = title
    canonical = json.dumps(request, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _project_result(project: ProjectRecord) -> dict[str, object]:
    return {
        "rowVersion": project.row_version,
        "title": project.title,
        "archivedAt": project.archived_at.isoformat() if project.archived_at else None,
    }


def _replay_project_mutation_or_conflict(
    project: ProjectRecord,
    event: EventRecord,
    request_sha256: str,
) -> ProjectRecord:
    payload = event.payload
    if payload.get("requestSha256") != request_sha256:
        raise _project_mutation_conflict(
            code="project-idempotency-key-reused",
            message="This idempotency key was already used for a different project mutation.",
        )
    if payload.get("result") != _project_result(project):
        raise _project_mutation_conflict(
            code="project-idempotency-stale",
            message="The project changed after this mutation. Reload it before retrying.",
        )
    return project


def _add_project_mutation_event(
    session: Session,
    *,
    project_id: str,
    event_type: str,
    idempotency_key: str,
    request_sha256: str,
    project: ProjectRecord,
) -> None:
    session.add(
        EventRecord(
            id=str(uuid.uuid4()),
            project_id=project_id,
            event_type=event_type,
            payload={
                "idempotencyKey": idempotency_key,
                "requestSha256": request_sha256,
                "result": _project_result(project),
            },
        )
    )


def _project_mutation_conflict(
    *, code: str, message: str, details: dict[str, object] | None = None
) -> HTTPException:
    detail: dict[str, object] = {
        "code": code,
        "userMessage": message,
        "retryable": False,
    }
    if details:
        detail["details"] = details
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _project_for_mutation(session: Session, project_id: str) -> ProjectRecord:
    project = session.get(ProjectRecord, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _cas_project_update(
    session: Session,
    project: ProjectRecord,
    *,
    expected_row_version: int,
    values: dict[str, object],
) -> ProjectRecord:
    if project.row_version != expected_row_version:
        raise _project_mutation_conflict(
            code="project-revision-conflict",
            message="The project changed. Reload it before trying again.",
            details={
                "expectedRowVersion": expected_row_version,
                "actualRowVersion": project.row_version,
            },
        )
    result = cast(
        CursorResult[Any],
        session.execute(
            update(ProjectRecord)
            .where(
                ProjectRecord.id == project.id,
                ProjectRecord.row_version == expected_row_version,
            )
            .values(
                row_version=ProjectRecord.row_version + 1,
                updated_at=utc_now(),
                **values,
            )
        ),
    )
    if result.rowcount != 1:
        raise _project_mutation_conflict(
            code="project-revision-conflict",
            message="The project changed. Reload it before trying again.",
        )
    session.flush()
    session.refresh(project)
    return project


@app.patch(
    "/v1/projects/{project_id}",
    response_model=ProjectOut,
    dependencies=[Depends(require_token)],
)
def rename_project(
    project_id: str,
    payload: ProjectRename,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    session: Session = Depends(get_session),
) -> ProjectRecord:
    project = _project_for_mutation(session, project_id)
    request_sha256 = _project_mutation_request_sha256(
        action="rename",
        project_id=project_id,
        expected_row_version=payload.expected_row_version,
        title=payload.title,
    )
    event = _project_mutation_event(session, project_id, "project.renamed", idempotency_key)
    if event is not None:
        return _replay_project_mutation_or_conflict(project, event, request_sha256)
    if project.row_version != payload.expected_row_version:
        _cas_project_update(
            session,
            project,
            expected_row_version=payload.expected_row_version,
            values={},
        )
    if project.title != payload.title:
        project = _cas_project_update(
            session,
            project,
            expected_row_version=payload.expected_row_version,
            values={"title": payload.title},
        )
    _add_project_mutation_event(
        session,
        project_id=project_id,
        event_type="project.renamed",
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        project=project,
    )
    session.commit()
    return project


def _mutate_project_archive_state(
    project_id: str,
    payload: ProjectStateMutation,
    idempotency_key: str,
    *,
    archive: bool,
    session: Session,
) -> ProjectRecord:
    event_type = "project.archived" if archive else "project.restored"
    project = _project_for_mutation(session, project_id)
    request_sha256 = _project_mutation_request_sha256(
        action="archive" if archive else "restore",
        project_id=project_id,
        expected_row_version=payload.expected_row_version,
    )
    event = _project_mutation_event(session, project_id, event_type, idempotency_key)
    if event is not None:
        return _replay_project_mutation_or_conflict(project, event, request_sha256)
    if project.row_version != payload.expected_row_version:
        _cas_project_update(
            session,
            project,
            expected_row_version=payload.expected_row_version,
            values={},
        )
    desired_archived_at = utc_now() if archive else None
    if (archive and project.archived_at is None) or (not archive and project.archived_at is not None):
        if archive:
            active_workflows = list(
                session.scalars(
                    select(WorkflowRecord.id).where(
                        WorkflowRecord.project_id == project_id,
                        WorkflowRecord.status.in_(_ACTIVE_PROJECT_WORKFLOW_STATUSES),
                    )
                )
            )
            if active_workflows:
                raise _project_mutation_conflict(
                    code="project-has-active-workflows",
                    message="This project has a running research task. Cancel or finish it before archiving.",
                    details={"workflowIds": active_workflows},
                )
        project = _cas_project_update(
            session,
            project,
            expected_row_version=payload.expected_row_version,
            values={"archived_at": desired_archived_at},
        )
    _add_project_mutation_event(
        session,
        project_id=project_id,
        event_type=event_type,
        idempotency_key=idempotency_key,
        request_sha256=request_sha256,
        project=project,
    )
    session.commit()
    return project


@app.post(
    "/v1/projects/{project_id}/archive",
    response_model=ProjectOut,
    dependencies=[Depends(require_token)],
)
def archive_project(
    project_id: str,
    payload: ProjectStateMutation,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    session: Session = Depends(get_session),
) -> ProjectRecord:
    return _mutate_project_archive_state(
        project_id, payload, idempotency_key, archive=True, session=session
    )


@app.post(
    "/v1/projects/{project_id}/restore",
    response_model=ProjectOut,
    dependencies=[Depends(require_token)],
)
def restore_project(
    project_id: str,
    payload: ProjectStateMutation,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=200),
    session: Session = Depends(get_session),
) -> ProjectRecord:
    return _mutate_project_archive_state(
        project_id, payload, idempotency_key, archive=False, session=session
    )


def _source_out(session: Session, source: SourceRecord) -> SourceOut:
    lineage: DiscoverySourceLineageOut | None = None
    events = session.scalars(
        select(EventRecord)
        .where(
            EventRecord.project_id == source.project_id,
            EventRecord.event_type == "source.discovery-attached",
        )
        .order_by(EventRecord.created_at.desc())
    )
    for event in events:
        if event.payload.get("sourceId") != source.id:
            continue
        try:
            candidate = DiscoverySourceLineageOut.model_validate(event.payload)
        except ValueError:
            continue
        if candidate.source_content_hash == source.content_hash:
            lineage = candidate
            break
    page_manifest = source_page_manifest_hash(session, source.id)
    return SourceOut.model_validate(
        {
            "id": source.id,
            "projectId": source.project_id,
            "title": source.title,
            "sourceKind": source.source_kind,
            "authors": source.authors,
            "doi": source.doi,
            "arxivId": source.arxiv_id,
            "localPath": source.local_path,
            "publicationDate": source.publication_date,
            "ingestionStatus": source.ingestion_status,
            "contentHash": source.content_hash,
            "pageCount": source.page_count,
            "pageManifestHash": page_manifest[0] if page_manifest is not None else None,
            "discoveryLineage": lineage,
            "createdAt": source.created_at,
        }
    )


def _candidate_attachment(
    session: Session,
    *,
    project_id: str,
    workflow_id: str | None,
    candidate_id: str | None,
    candidate_sha256: str | None,
    occurrence_invocation_id: str | None,
) -> (
    tuple[
        DiscoveryCandidateRecord,
        CandidateOccurrenceRecord,
        ToolInvocationRecord,
        DiscoveryCandidate,
    ]
    | None
):
    values = (
        workflow_id,
        candidate_id,
        candidate_sha256,
        occurrence_invocation_id,
    )
    if all(value is None for value in values):
        return None
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise HTTPException(
            status_code=422,
            detail="Candidate PDF attachment requires the complete discovery identity.",
        )
    candidate = session.scalar(
        select(DiscoveryCandidateRecord).where(
            DiscoveryCandidateRecord.project_id == project_id,
            DiscoveryCandidateRecord.id == candidate_id,
            DiscoveryCandidateRecord.candidate_sha256 == candidate_sha256,
        )
    )
    row = session.execute(
        select(CandidateOccurrenceRecord, ToolInvocationRecord)
        .join(
            ToolInvocationRecord,
            ToolInvocationRecord.id == CandidateOccurrenceRecord.invocation_id,
        )
        .where(
            CandidateOccurrenceRecord.project_id == project_id,
            CandidateOccurrenceRecord.candidate_id == candidate_id,
            CandidateOccurrenceRecord.invocation_id == occurrence_invocation_id,
            ToolInvocationRecord.project_id == project_id,
            ToolInvocationRecord.workflow_id == workflow_id,
        )
    ).one_or_none()
    if candidate is None or row is None:
        raise HTTPException(status_code=404, detail="Discovery candidate occurrence not found")
    triage = session.scalar(
        select(CandidateTriageDecisionRecord).where(
            CandidateTriageDecisionRecord.project_id == project_id,
            CandidateTriageDecisionRecord.candidate_id == candidate_id,
        )
    )
    if triage is None or triage.decision != "keep":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Keep this discovery candidate before attaching a local PDF.",
        )
    try:
        metadata = discovery_candidate_from_record(candidate)
    except ValueError as error:
        raise HTTPException(
            status_code=409, detail="Discovery candidate metadata is invalid"
        ) from error
    occurrence, invocation = row
    if metadata.provider != invocation.provider:
        raise HTTPException(status_code=409, detail="Discovery candidate provider changed")
    return candidate, occurrence, invocation, metadata


def _titles_obviously_mismatch(candidate_title: str, pdf_title: str | None) -> bool:
    if not pdf_title:
        return False

    def tokens(value: str) -> set[str]:
        return {token for token in re.findall(r"[a-z0-9]+", value.casefold()) if len(token) > 2}

    candidate_tokens = tokens(candidate_title)
    pdf_tokens = tokens(pdf_title)
    if not candidate_tokens or not pdf_tokens:
        return False
    overlap = len(candidate_tokens & pdf_tokens)
    return overlap == 0 or overlap / min(len(candidate_tokens), len(pdf_tokens)) < 0.2


def _attach_source_to_candidate(
    session: Session,
    *,
    source: SourceRecord,
    attachment: tuple[
        DiscoveryCandidateRecord,
        CandidateOccurrenceRecord,
        ToolInvocationRecord,
        DiscoveryCandidate,
    ],
) -> None:
    candidate, occurrence, invocation, _metadata = attachment
    existing = next(
        (
            event
            for event in session.scalars(
                select(EventRecord).where(
                    EventRecord.project_id == source.project_id,
                    EventRecord.workflow_id == invocation.workflow_id,
                    EventRecord.event_type == "source.discovery-attached",
                )
            )
            if event.payload.get("candidateId") == candidate.id
            or event.payload.get("sourceId") == source.id
        ),
        None,
    )
    if existing is not None:
        if (
            existing.payload.get("candidateId") == candidate.id
            and existing.payload.get("sourceId") == source.id
            and existing.payload.get("candidateSha256") == candidate.candidate_sha256
            and existing.payload.get("sourceContentHash") == source.content_hash
        ):
            return
        raise HTTPException(
            status_code=409,
            detail="The candidate or local PDF is already attached to another source.",
        )
    session.add(
        EventRecord(
            id=str(uuid.uuid4()),
            project_id=source.project_id,
            workflow_id=invocation.workflow_id,
            event_type="source.discovery-attached",
            payload={
                "schemaVersion": "1",
                "sourceId": source.id,
                "workflowId": invocation.workflow_id,
                "candidateId": candidate.id,
                "candidateSha256": candidate.candidate_sha256,
                "occurrenceInvocationId": occurrence.invocation_id,
                "queryId": invocation.query_id,
                "provider": invocation.provider,
                "rawItemSha256": occurrence.raw_item_sha256,
                "sourceContentHash": source.content_hash,
            },
        )
    )


@app.get(
    "/v1/projects/{project_id}/sources",
    response_model=list[SourceOut],
    dependencies=[Depends(require_token)],
)
def list_sources(project_id: str, session: Session = Depends(get_session)) -> list[SourceOut]:
    _project_or_404(session, project_id)
    return [
        _source_out(session, source)
        for source in session.scalars(
            select(SourceRecord)
            .where(SourceRecord.project_id == project_id)
            .order_by(SourceRecord.created_at.desc())
        )
    ]


@app.post(
    "/v1/projects/{project_id}/datasets",
    response_model=SourceOut,
    dependencies=[Depends(require_token)],
)
async def import_dataset(
    project_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> SourceRecord:
    project = _project_or_404(session, project_id)
    filename = Path(file.filename or "dataset.csv").name
    if Path(filename).suffix.lower() != ".csv":
        raise HTTPException(status_code=415, detail="Dataset upload must be a CSV file")
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="CSV exceeds the configured upload limit")
    try:
        validate_csv(content)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    content_hash = hashlib.sha256(content).hexdigest()
    existing = session.scalar(
        select(SourceRecord).where(
            SourceRecord.project_id == project_id,
            SourceRecord.content_hash == content_hash,
        )
    )
    if existing is not None:
        if existing.source_kind == "dataset" and existing.ingestion_status == "ready":
            return existing
        raise HTTPException(
            status_code=409, detail="Content hash already belongs to another source"
        )

    data_dir = child_path(Path(project.project_path), "data/raw")
    target = child_path(data_dir, f"{content_hash}.csv")
    target.write_bytes(content)
    target.chmod(0o444)
    record = SourceRecord(
        id=str(uuid.uuid4()),
        project_id=project_id,
        title=Path(filename).stem,
        source_kind="dataset",
        authors=[],
        local_path=str(target),
        ingestion_status="ready",
        content_hash=content_hash,
    )
    session.add(record)
    session.flush()
    session.add(
        EventRecord(
            id=str(uuid.uuid4()),
            project_id=project_id,
            event_type="dataset.ingested",
            payload={
                "sourceId": record.id,
                "contentHash": content_hash,
                "filename": filename,
            },
        )
    )
    session.commit()
    return record


@app.post(
    "/v1/projects/{project_id}/sources",
    response_model=SourceOut,
    dependencies=[Depends(require_token)],
)
async def import_pdf(
    project_id: str,
    file: UploadFile = File(...),
    workflow_id: str | None = Form(default=None, alias="workflowId"),
    candidate_id: str | None = Form(default=None, alias="candidateId"),
    candidate_sha256: str | None = Form(default=None, alias="candidateSha256"),
    occurrence_invocation_id: str | None = Form(
        default=None,
        alias="occurrenceInvocationId",
    ),
    confirm_identity_mismatch: bool = Form(
        default=False,
        alias="confirmIdentityMismatch",
    ),
    session: Session = Depends(get_session),
) -> SourceOut:
    project = _project_or_404(session, project_id)
    attachment = _candidate_attachment(
        session,
        project_id=project_id,
        workflow_id=workflow_id,
        candidate_id=candidate_id,
        candidate_sha256=candidate_sha256,
        occurrence_invocation_id=occurrence_invocation_id,
    )
    filename = Path(file.filename or "paper.pdf").name
    if Path(filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=415, detail="The first MVP accepts PDF sources only")
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="PDF exceeds the configured upload limit")
    if not content.startswith(b"%PDF"):
        raise HTTPException(status_code=415, detail="Uploaded file is not a PDF")
    content_hash = hashlib.sha256(content).hexdigest()
    existing = session.scalar(
        select(SourceRecord).where(
            SourceRecord.project_id == project_id,
            SourceRecord.content_hash == content_hash,
        )
    )
    if existing is not None:
        if existing.ingestion_status != "failed":
            if attachment is not None:
                metadata = attachment[3]
                if (
                    _titles_obviously_mismatch(metadata.title, existing.title)
                    and not confirm_identity_mismatch
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "candidate-pdf-identity-mismatch",
                            "userMessage": (
                                "The PDF title does not clearly match the selected candidate. "
                                "Confirm the attachment to continue."
                            ),
                            "retryable": False,
                        },
                    )
                _attach_source_to_candidate(
                    session,
                    source=existing,
                    attachment=attachment,
                )
                session.commit()
            return _source_out(session, existing)
        # A failed parse has no managed file. Remove the tombstone so the same
        # corrected/retried upload can go through the parser again.
        session.delete(existing)
        session.commit()

    papers_dir = child_path(Path(project.project_path), "papers")
    target = child_path(papers_dir, f"{content_hash}.pdf")
    target.write_bytes(content)
    if attachment is not None:
        try:
            extraction = await run_in_threadpool(extract_pdf, target)
            metadata = attachment[3]
            if (
                _titles_obviously_mismatch(metadata.title, extraction.title)
                and not confirm_identity_mismatch
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "candidate-pdf-identity-mismatch",
                        "userMessage": (
                            "The PDF title does not clearly match the selected candidate. "
                            "Confirm the attachment to continue."
                        ),
                        "retryable": False,
                    },
                )
            record = SourceRecord(
                id=str(uuid.uuid4()),
                project_id=project_id,
                title=extraction.title or Path(filename).stem,
                source_kind="pdf",
                authors=extraction.authors,
                local_path=str(target),
                ingestion_status="ready",
                content_hash=content_hash,
                page_count=len(extraction.pages),
            )
            session.add(record)
            session.flush()
            for page in extraction.pages:
                session.add(
                    SourcePageRecord(
                        source_id=record.id,
                        page_index=page.page_index,
                        page_label=page.page_label,
                        width=page.width,
                        height=page.height,
                        text=page.text,
                        words=page.words,
                    )
                )
            target.chmod(0o444)
            session.add(
                EventRecord(
                    id=str(uuid.uuid4()),
                    project_id=project_id,
                    event_type="source.ingested",
                    payload={"sourceId": record.id, "contentHash": content_hash},
                )
            )
            _attach_source_to_candidate(
                session,
                source=record,
                attachment=attachment,
            )
            session.commit()
            return _source_out(session, record)
        except HTTPException:
            session.rollback()
            target.unlink(missing_ok=True)
            raise
        except Exception as error:
            session.rollback()
            target.unlink(missing_ok=True)
            raise HTTPException(
                status_code=422,
                detail=f"Could not parse PDF: {error}",
            ) from error

    record = SourceRecord(
        id=str(uuid.uuid4()),
        project_id=project_id,
        title=Path(filename).stem,
        source_kind="pdf",
        authors=[],
        local_path=str(target),
        ingestion_status="processing",
        content_hash=content_hash,
    )
    session.add(record)
    session.commit()
    try:
        extraction = await run_in_threadpool(extract_pdf, target)
        record.title = extraction.title or record.title
        record.authors = extraction.authors
        record.page_count = len(extraction.pages)
        record.ingestion_status = "ready"
        for page in extraction.pages:
            session.add(
                SourcePageRecord(
                    source_id=record.id,
                    page_index=page.page_index,
                    page_label=page.page_label,
                    width=page.width,
                    height=page.height,
                    text=page.text,
                    words=page.words,
                )
            )
        target.chmod(0o444)
        session.add(
            EventRecord(
                id=str(uuid.uuid4()),
                project_id=project_id,
                event_type="source.ingested",
                payload={"sourceId": record.id, "contentHash": content_hash},
            )
        )
        session.commit()
        return _source_out(session, record)
    except Exception as error:
        record.ingestion_status = "failed"
        session.commit()
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=f"Could not parse PDF: {error}") from error


@app.post(
    "/v1/projects/{project_id}/analysis-intents",
    response_model=AnalysisIntentOut,
    dependencies=[Depends(require_token)],
)
def create_analysis_intent(
    project_id: str,
    payload: AnalysisIntentCreate,
    session: Session = Depends(get_session),
) -> AnalysisIntentOut:
    try:
        intent = create_standalone_analysis_intent(session, project_id, payload)
    except AnalysisServiceError as error:
        raise _analysis_http_exception(error) from error
    session.commit()
    return analysis_intent_out(intent)


@app.post(
    "/v1/analysis-intents/{intent_id}/decision",
    response_model=AnalysisIntentOut,
    dependencies=[Depends(require_token)],
)
def decide_analysis_intent(
    intent_id: str,
    payload: AnalysisDecisionIn,
    session: Session = Depends(get_session),
) -> AnalysisIntentOut:
    try:
        intent = decide_standalone_analysis_intent(session, intent_id, payload.decision)
    except AnalysisServiceError as error:
        session.rollback()
        raise _analysis_http_exception(error) from error
    session.commit()
    return analysis_intent_out(intent)


@app.post(
    "/v1/analysis-intents/{intent_id}/execute",
    response_model=AnalysisRunOut,
    dependencies=[Depends(require_token)],
)
async def execute_analysis_intent(
    intent_id: str,
) -> AnalysisRunOut:
    try:
        return await execute_standalone_analysis_intent(
            intent_id,
            session_factory=SessionLocal,
        )
    except AnalysisServiceError as error:
        raise _analysis_http_exception(error) from error


@app.get(
    "/v1/projects/{project_id}/analysis-runs",
    response_model=list[AnalysisRunOut],
    dependencies=[Depends(require_token)],
)
def list_analysis_runs(
    project_id: str,
    session: Session = Depends(get_session),
) -> list[AnalysisRunOut]:
    try:
        return list_project_analysis_runs(session, project_id)
    except AnalysisServiceError as error:
        raise _analysis_http_exception(error) from error


@app.get("/v1/sources/{source_id}/file", dependencies=[Depends(require_token)])
def source_file(source_id: str, session: Session = Depends(get_session)) -> StreamingResponse:
    source = session.get(SourceRecord, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    project = _project_or_404(session, source.project_id)
    is_dataset = source.source_kind == "dataset"
    try:
        return secure_download_response(
            project_root=Path(project.project_path),
            source_path=Path(source.local_path),
            expected_sha256=source.content_hash,
            media_type="text/csv" if is_dataset else "application/pdf",
            filename=f"{source.title}.csv" if is_dataset else f"{source.title}.pdf",
            content_disposition_type="attachment" if is_dataset else "inline",
            errors=DownloadErrorDetails(
                missing="Source file is missing",
                unsafe="Source path may not be a symbolic link",
                changed="Source file changed while preparing the download",
                hash_mismatch="Source content hash no longer matches its record",
            ),
        )
    except SecureDownloadError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


@app.get("/v1/artifacts/{artifact_id}/file", dependencies=[Depends(require_token)])
def artifact_file(artifact_id: str, session: Session = Depends(get_session)) -> StreamingResponse:
    artifact = session.get(ArtifactRecord, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    run = session.get(RunRecord, artifact.run_id)
    task = session.get(TaskRecord, run.task_id) if run is not None else None
    project = session.get(ProjectRecord, task.project_id) if task is not None else None
    if run is None or task is None or project is None:
        raise HTTPException(status_code=409, detail="Artifact provenance records are incomplete")
    artifact_path = Path(artifact.path)
    run_prefix = Path("runs") / run.id
    reserved_names = {
        "input.ipynb",
        "executed.ipynb",
        "environment.json",
        "stdout.txt",
        "stderr.txt",
        "execution.log",
    }
    if (
        not artifact.path
        or artifact_path.is_absolute()
        or ".." in artifact_path.parts
        or "\\" in artifact.path
        or run_prefix not in artifact_path.parents
        or artifact.path not in run.output_artifacts
        or artifact_path.name in reserved_names
        and artifact_path.parent != run_prefix
    ):
        raise HTTPException(status_code=409, detail="Artifact provenance path is invalid")

    inline = artifact.mime_type.startswith(("image/", "text/")) or artifact.mime_type in {
        "application/json",
        "application/pdf",
    }
    path = Path(project.project_path) / artifact_path
    try:
        return secure_download_response(
            project_root=Path(project.project_path),
            source_path=path,
            expected_sha256=artifact.content_hash,
            media_type=artifact.mime_type,
            filename=path.name,
            content_disposition_type="inline" if inline else "attachment",
            errors=DownloadErrorDetails(
                missing="Artifact file is missing",
                unsafe="Artifact path is unsafe",
                changed="Artifact file changed while preparing the download",
                hash_mismatch="Artifact content hash no longer matches its run",
            ),
        )
    except SecureDownloadError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from error


def _model_endpoint_identity(api_base: str) -> str:
    endpoint = canonical_model_api_endpoint(api_base)
    if endpoint is None:
        return ""
    return f"sha256:{hashlib.sha256(endpoint.encode('utf-8')).hexdigest()}"


@app.post(
    "/v1/projects/{project_id}/questions",
    response_model=AnswerOut,
    dependencies=[Depends(require_token)],
)
async def ask_question(
    project_id: str,
    payload: QuestionIn,
    session: Session = Depends(get_session),
) -> AnswerOut:
    project = _project_or_404(session, project_id)
    sources = list(
        session.scalars(
            select(SourceRecord)
            .where(
                SourceRecord.project_id == project_id,
                SourceRecord.source_kind == "pdf",
                SourceRecord.ingestion_status == "ready",
            )
            .options(selectinload(SourceRecord.pages))
        )
    )
    if not sources:
        raise HTTPException(status_code=409, detail="Import at least one parsed PDF first")
    if not settings.model_gateway_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "PaperQA model gateway is not configured. Store the credential "
                "with 'pnpm model-key:set', configure SPARK_AGENT_LLM_MODEL, and "
                "start the service through 'pnpm mvp:dev'."
            ),
        )
    if not payload.remote_data_approved:
        raise HTTPException(
            status_code=403,
            detail=(
                "This request requires explicit approval to send the question and PDF text "
                "needed for indexing and answering to the configured remote model gateway."
            ),
        )
    source_paths: list[Path] = []
    for source in sources:
        raw_source_path = Path(source.local_path)
        if raw_source_path.is_symlink():
            raise HTTPException(status_code=409, detail="A selected PDF source is unavailable")
        source_path = raw_source_path.resolve()
        _assert_beneath(Path(project.project_path), source_path)
        if not source_path.is_file():
            raise HTTPException(status_code=409, detail="A selected PDF source is unavailable")
        if sha256_file(source_path) != source.content_hash:
            raise HTTPException(
                status_code=409,
                detail="A selected PDF source no longer matches its recorded content hash",
            )
        source_paths.append(source_path)
    session.add(
        EventRecord(
            id=str(uuid.uuid4()),
            project_id=project_id,
            event_type="literature.remote-data.approved",
            payload={
                "questionHash": hashlib.sha256(payload.question.encode("utf-8")).hexdigest(),
                "sourceIds": [source.id for source in sources],
                "gateway": "openai-compatible",
                "endpointHost": urlsplit(settings.openai_api_base).hostname or "",
                "endpointIdentity": _model_endpoint_identity(settings.openai_api_base),
                "model": payload.model or settings.llm_model,
                "dataCategories": ["user-question", "selected-pdf-text"],
            },
        )
    )
    session.commit()
    try:
        result = await paper_qa.ask(
            project_id,
            source_paths,
            payload.question,
            payload.model,
        )
    except Exception as error:
        logger.warning("PaperQA2 request failed (%s)", type(error).__name__)
        raise HTTPException(
            status_code=502,
            detail=(
                "PaperQA2 could not answer this question. Check the model gateway "
                "configuration and try again."
            ),
        ) from None

    answer_id = str(uuid.uuid4())
    answer_record = AnswerRecord(
        id=answer_id,
        project_id=project_id,
        question=payload.question,
        answer=result.answer,
        unresolved_questions=[],
        generator="paperqa2-remote-v1",
        model=payload.model or settings.llm_model,
        prompt_version=None,
        metadata_json={
            "generationMode": "remote-model-assisted",
            "provider": "openai-compatible",
            "endpointHost": urlsplit(settings.openai_api_base).hostname or "",
            "endpointIdentity": _model_endpoint_identity(settings.openai_api_base),
        },
    )
    claim = ClaimRecord(
        id=str(uuid.uuid4()),
        answer_id=answer_id,
        statement=result.answer,
        claim_type="answer",
        confidence=0.0,
        review_status="unreviewed",
    )
    session.add_all([answer_record, claim])

    evidence_records: list[EvidenceSpanRecord] = []
    page_sets = {
        source.id: [
            PdfPage(
                page_index=page.page_index,
                page_label=page.page_label,
                width=page.width,
                height=page.height,
                text=page.text,
                words=page.words,
            )
            for page in source.pages
        ]
        for source in sources
    }
    for quote in result.evidence_candidates:
        best: tuple[SourceRecord, LocatedQuote] | None = None
        for source in sources:
            located = locate_quote(quote, page_sets[source.id])
            if located is not None and (best is None or located.confidence > best[1].confidence):
                best = (source, located)
        if best is None:
            continue
        source, located = best
        evidence = EvidenceSpanRecord(
            id=str(uuid.uuid4()),
            source_id=source.id,
            page_index=located.page_index,
            page_label=located.page_label,
            text=located.text,
            bbox=located.bbox,
            coordinate_space="normalized-rotated-top-left-v1",
            quote_hash=hashlib.sha256(located.text.encode("utf-8")).hexdigest(),
            extraction_method="paperqa2+pdf-word-map",
            confidence=located.confidence,
            verified=located.verified,
        )
        evidence_records.append(evidence)
        session.add(evidence)
        session.add(
            ClaimEvidenceRecord(
                claim_id=claim.id,
                evidence_id=evidence.id,
                relationship_kind="supporting",
            )
        )
    verified = [evidence for evidence in evidence_records if evidence.verified]
    # Local quote location proves only that a passage occurs in a PDF. It does
    # not review the generated PaperQA answer for entailment or correctness.
    claim.confidence = 0.0
    claim.review_status = "unreviewed"
    if not verified:
        answer_record.unresolved_questions = [
            "PaperQA2 produced an answer, but no evidence passage could be verified against the local PDF text."
        ]
    session.add(
        EventRecord(
            id=str(uuid.uuid4()),
            project_id=project_id,
            event_type="answer.created",
            payload={"answerId": answer_id, "verifiedEvidence": len(verified)},
        )
    )
    session.commit()
    return AnswerOut(
        id=answer_record.id,
        project_id=answer_record.project_id,
        question=answer_record.question,
        answer=answer_record.answer,
        claims=[
            ClaimOut(
                id=claim.id,
                statement=claim.statement,
                claim_type=cast(
                    Literal["answer", "finding", "limitation", "contradiction"],
                    claim.claim_type,
                ),
                confidence=claim.confidence,
                review_status=cast(
                    Literal["unreviewed", "verified", "rejected"],
                    claim.review_status,
                ),
                evidence=[EvidenceOut.model_validate(item) for item in evidence_records],
            )
        ],
        unresolved_questions=answer_record.unresolved_questions,
        generator=answer_record.generator,
        model=answer_record.model,
        prompt_version=answer_record.prompt_version,
        metadata=answer_record.metadata_json,
        created_at=answer_record.created_at,
    )


def _project_or_404(session: Session, project_id: str) -> ProjectRecord:
    project = session.get(ProjectRecord, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def child_path(parent: Path, child: str) -> Path:
    parent = parent.resolve()
    candidate = (parent / child).resolve()
    _assert_beneath(parent, candidate)
    return candidate


def _assert_beneath(parent: Path, candidate: Path) -> None:
    try:
        candidate.relative_to(parent.resolve())
    except ValueError as error:
        raise HTTPException(status_code=403, detail="Path escapes the project directory") from error
