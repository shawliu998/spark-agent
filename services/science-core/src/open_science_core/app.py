from __future__ import annotations

import hashlib
import logging
import secrets
import shutil
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session, selectinload

from . import __version__
from .analysis import (
    RuntimeServiceError,
    canonical_analysis_payload,
    collect_runtime_artifacts,
    execute_in_runtime,
    read_text_file,
    sha256_file,
    validate_csv,
    validate_python_code,
)
from .api.workflows import router as workflow_router
from .config import settings
from .db import SessionLocal, database_session, engine, initialize_database
from .literature import paper_qa, paper_qa_available
from .models import (
    AnalysisIntentRecord,
    AnswerRecord,
    ApprovalRecord,
    ArtifactRecord,
    ClaimEvidenceRecord,
    ClaimRecord,
    EvidenceSpanRecord,
    EventRecord,
    ProjectRecord,
    RunRecord,
    SourcePageRecord,
    SourceRecord,
    TaskRecord,
    utc_now,
)
from .pdf import LocatedQuote, PdfPage, extract_pdf, locate_quote
from .schemas import (
    AnalysisArtifactOut,
    AnalysisDecisionIn,
    AnalysisIntentCreate,
    AnalysisIntentOut,
    AnalysisRunOut,
    AnswerOut,
    ClaimOut,
    EvidenceOut,
    HealthOut,
    ProjectCreate,
    ProjectOut,
    QuestionIn,
    SourceOut,
)
from .workflow.worker import WorkflowWorker


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if not settings.bearer_token:
        raise RuntimeError(
            "SPARK_AGENT_CORE_TOKEN is required; science-core will not start without authentication"
        )
    initialize_database()
    with SessionLocal() as session:
        _recover_interrupted_analysis_state(session)
    _cleanup_stale_exchange_entries(reject_recent=False)
    workflow_worker = WorkflowWorker()
    await workflow_worker.start()
    try:
        yield
    finally:
        await workflow_worker.stop()


app = FastAPI(title="Spark Agent Core", version=__version__, lifespan=lifespan)
_analysis_execution_slot = threading.Lock()
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
    allow_methods=["GET", "POST", "OPTIONS"],
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid science-core token")


def get_session() -> Generator[Session, None, None]:
    yield from database_session()


app.include_router(workflow_router, dependencies=[Depends(require_token)])


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
    return HealthOut(
        status="ok" if database == "ok" and runtime == "ready" else "degraded",
        version=__version__,
        database=database,
        paper_qa="available" if paper_qa_available() else "unavailable",
        model_gateway="configured" if settings.model_gateway_configured else "unconfigured",
        runtime=runtime,
    )


@app.get("/v1/projects", response_model=list[ProjectOut], dependencies=[Depends(require_token)])
def list_projects(session: Session = Depends(get_session)) -> list[ProjectRecord]:
    return list(session.scalars(select(ProjectRecord).order_by(ProjectRecord.updated_at.desc())))


@app.post("/v1/projects", response_model=ProjectOut, dependencies=[Depends(require_token)])
def create_project(payload: ProjectCreate, session: Session = Depends(get_session)) -> ProjectRecord:
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
    )
    session.add(record)
    # Flush the parent first. EventRecord deliberately has no ORM relationship,
    # so SQLAlchemy cannot infer insert ordering from in-memory object links.
    session.flush()
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


@app.get(
    "/v1/projects/{project_id}/sources",
    response_model=list[SourceOut],
    dependencies=[Depends(require_token)],
)
def list_sources(project_id: str, session: Session = Depends(get_session)) -> list[SourceRecord]:
    _project_or_404(session, project_id)
    return list(
        session.scalars(
            select(SourceRecord)
            .where(SourceRecord.project_id == project_id)
            .order_by(SourceRecord.created_at.desc())
        )
    )


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
        raise HTTPException(status_code=409, detail="Content hash already belongs to another source")

    data_dir = _child_path(Path(project.project_path), "data/raw")
    target = _child_path(data_dir, f"{content_hash}.csv")
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
    session: Session = Depends(get_session),
) -> SourceRecord:
    project = _project_or_404(session, project_id)
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
            return existing
        # A failed parse has no managed file. Remove the tombstone so the same
        # corrected/retried upload can go through the parser again.
        session.delete(existing)
        session.commit()

    papers_dir = _child_path(Path(project.project_path), "papers")
    target = _child_path(papers_dir, f"{content_hash}.pdf")
    target.write_bytes(content)
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
        return record
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
    _project_or_404(session, project_id)
    dataset = session.get(SourceRecord, payload.dataset_source_id)
    if dataset is None or dataset.project_id != project_id:
        raise HTTPException(status_code=404, detail="Dataset source not found in this project")
    if dataset.source_kind != "dataset" or dataset.ingestion_status != "ready":
        raise HTTPException(status_code=409, detail="Analysis requires a ready CSV dataset source")
    try:
        validate_python_code(payload.code)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    _canonical, payload_sha256 = canonical_analysis_payload(
        dataset.id, payload.objective, payload.code
    )
    task_id = str(uuid.uuid4())
    intent_id = str(uuid.uuid4())
    task = TaskRecord(
        id=task_id,
        project_id=project_id,
        objective=payload.objective,
        task_type="python-data-analysis",
        inputs={
            "datasetSourceId": dataset.id,
            "objective": payload.objective,
            "code": payload.code,
            "payloadSha256": payload_sha256,
        },
        expected_outputs=["executed-notebook", "stdout", "stderr", "log", "artifacts"],
        acceptance_criteria=[
            "approved payload hash must exactly match executed payload",
            "runtime output hashes must be independently verified",
        ],
        permissions=["dataset:read", "python:execute", "run-artifacts:write"],
        status="waiting-execution-approval",
        timeout_seconds=settings.execution_timeout_seconds,
    )
    session.add(task)
    session.flush()
    intent = AnalysisIntentRecord(
        id=intent_id,
        task_id=task_id,
        project_id=project_id,
        dataset_source_id=dataset.id,
        objective=payload.objective,
        code=payload.code,
        payload_sha256=payload_sha256,
        status="waiting-approval",
    )
    session.add(intent)
    session.add(
        ApprovalRecord(
            id=str(uuid.uuid4()),
            task_id=task_id,
            intent_hash=payload_sha256,
            requested_action="execute-python-data-analysis",
            risk_level="high",
            reason="Execute the displayed Python code against the selected CSV dataset",
            affected_resources=[dataset.id, "runs/<run-id>"],
        )
    )
    session.add(
        EventRecord(
            id=str(uuid.uuid4()),
            project_id=project_id,
            event_type="analysis.intent.created",
            payload={
                "analysisIntentId": intent_id,
                "taskId": task_id,
                "datasetSourceId": dataset.id,
                "payloadSha256": payload_sha256,
            },
        )
    )
    session.commit()
    return _analysis_intent_out(intent)


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
    decided_at = utc_now()
    decision_result = session.execute(
        update(AnalysisIntentRecord)
        .where(
            AnalysisIntentRecord.id == intent_id,
            AnalysisIntentRecord.status == "waiting-approval",
            AnalysisIntentRecord.decision.is_(None),
        )
        .values(
            decision=payload.decision,
            status=payload.decision,
            updated_at=decided_at,
        )
    )
    if decision_result.rowcount != 1:
        session.rollback()
        current = _analysis_intent_or_404(session, intent_id)
        if current.decision == payload.decision:
            return _analysis_intent_out(current)
        raise HTTPException(status_code=409, detail="Analysis intent already has a final decision")

    intent = _analysis_intent_or_404(session, intent_id)
    approval = session.scalar(
        select(ApprovalRecord).where(ApprovalRecord.task_id == intent.task_id)
    )
    task = session.get(TaskRecord, intent.task_id)
    if approval is None or task is None:
        session.rollback()
        raise HTTPException(status_code=500, detail="Analysis approval audit record is missing")
    if approval.intent_hash != intent.payload_sha256:
        session.rollback()
        raise HTTPException(status_code=409, detail="Stored approval hash does not match the intent")
    approval.user_decision = payload.decision
    approval.decided_at = decided_at
    task.status = "waiting-execution" if payload.decision == "approved" else "rejected"
    session.add(
        EventRecord(
            id=str(uuid.uuid4()),
            project_id=intent.project_id,
            event_type=f"analysis.intent.{payload.decision}",
            payload={
                "analysisIntentId": intent.id,
                "taskId": intent.task_id,
                "payloadSha256": intent.payload_sha256,
            },
        )
    )
    session.commit()
    return _analysis_intent_out(intent)


@app.post(
    "/v1/analysis-intents/{intent_id}/execute",
    response_model=AnalysisRunOut,
    dependencies=[Depends(require_token)],
)
async def execute_analysis_intent(
    intent_id: str,
    session: Session = Depends(get_session),
) -> AnalysisRunOut:
    if not _analysis_execution_slot.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Another analysis execution is already active")
    try:
        _prepare_exchange_for_execution()
        return await _execute_analysis_intent_locked(intent_id, session)
    finally:
        _analysis_execution_slot.release()


async def _execute_analysis_intent_locked(
    intent_id: str,
    session: Session,
) -> AnalysisRunOut:
    intent = _analysis_intent_or_404(session, intent_id)
    project = _project_or_404(session, intent.project_id)
    dataset = session.get(SourceRecord, intent.dataset_source_id)
    task = session.get(TaskRecord, intent.task_id)
    approval = session.scalar(
        select(ApprovalRecord).where(ApprovalRecord.task_id == intent.task_id)
    )
    if dataset is None or task is None or approval is None:
        raise HTTPException(status_code=409, detail="Analysis execution records are incomplete")
    if dataset.source_kind != "dataset" or dataset.ingestion_status != "ready":
        raise HTTPException(status_code=409, detail="Selected dataset is not ready")

    _canonical, current_hash = canonical_analysis_payload(
        intent.dataset_source_id, intent.objective, intent.code
    )
    if current_hash != intent.payload_sha256 or approval.intent_hash != current_hash:
        raise HTTPException(status_code=409, detail="Approved payload hash no longer matches the intent")
    if intent.decision != "approved" or approval.user_decision != "approved":
        raise HTTPException(status_code=409, detail="Analysis intent must be explicitly approved")
    if intent.status != "approved":
        raise HTTPException(status_code=409, detail=f"Analysis intent cannot execute from {intent.status}")

    dataset_path = Path(dataset.local_path).resolve()
    _assert_beneath(Path(project.project_path), dataset_path)
    if not dataset_path.is_file():
        raise HTTPException(status_code=409, detail="Dataset file is missing")
    if sha256_file(dataset_path) != dataset.content_hash:
        raise HTTPException(status_code=409, detail="Dataset content hash no longer matches its source")

    claimed_at = utc_now()
    claim = session.execute(
        update(AnalysisIntentRecord)
        .where(
            AnalysisIntentRecord.id == intent.id,
            AnalysisIntentRecord.status == "approved",
        )
        .values(status="executing", updated_at=claimed_at)
    )
    if claim.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="Analysis intent was already claimed")

    run_id = str(uuid.uuid4())
    run = RunRecord(
        id=run_id,
        task_id=task.id,
        environment_hash=None,
        input_artifacts=[dataset.id],
        output_artifacts=[],
        status="running",
    )
    task.status = "running"
    session.add(run)
    session.flush()
    session.add(
        EventRecord(
            id=str(uuid.uuid4()),
            project_id=project.id,
            event_type="analysis.run.started",
            payload={
                "analysisIntentId": intent.id,
                "runId": run_id,
                "payloadSha256": current_hash,
            },
        )
    )
    session.commit()

    run_dir = _child_path(Path(project.project_path), f"runs/{run_id}")
    project_dataset_path = _child_path(run_dir, "input.csv")
    exchange_runs_dir = _child_path(settings.runtime_exchange_dir, "runs")
    exchange_runs_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    exchange_run_dir = _child_path(exchange_runs_dir, run_id)
    runtime_dataset_path = _child_path(exchange_run_dir, "input.csv")
    try:
        run_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
        shutil.copyfile(dataset_path, project_dataset_path)
        project_dataset_path.chmod(0o444)
        if sha256_file(project_dataset_path) != dataset.content_hash:
            raise RuntimeServiceError("Project run input copy failed integrity verification")

        exchange_run_dir.mkdir(mode=0o1777, parents=False, exist_ok=False)
        exchange_run_dir.chmod(0o1777)
        shutil.copyfile(dataset_path, runtime_dataset_path)
        runtime_dataset_path.chmod(0o444)
        if sha256_file(runtime_dataset_path) != dataset.content_hash:
            raise RuntimeServiceError("Read-only runtime input copy failed integrity verification")

        runtime_result = await execute_in_runtime(
            run_id=run_id,
            run_dir=exchange_run_dir,
            dataset_path=runtime_dataset_path,
            objective=intent.objective,
            code=intent.code,
            payload_sha256=current_hash,
        )
        if (
            runtime_dataset_path.is_symlink()
            or not runtime_dataset_path.is_file()
            or sha256_file(runtime_dataset_path) != dataset.content_hash
        ):
            raise RuntimeServiceError("Runtime input.csv changed during execution")
        collected = collect_runtime_artifacts(
            runtime_result=runtime_result,
            exchange_run_dir=exchange_run_dir,
            final_run_dir=run_dir,
            project_dir=Path(project.project_path),
        )
    except (OSError, RuntimeServiceError) as error:
        _clear_run_outputs(run_dir)
        _remove_exchange_run(exchange_run_dir)
        _record_execution_failure(
            session=session,
            project=project,
            intent=intent,
            task=task,
            run=run,
            run_dir=run_dir,
            error=error,
        )
        raise HTTPException(status_code=502, detail=str(error)) from error
    _remove_exchange_run(exchange_run_dir)

    artifact_records: list[ArtifactRecord] = []
    for item in collected:
        artifact = ArtifactRecord(
            id=str(uuid.uuid4()),
            run_id=run.id,
            artifact_type=item.artifact_type,
            path=item.project_relative_path,
            mime_type=item.mime_type,
            content_hash=item.content_hash,
            parent_artifacts=[dataset.id],
            metadata_json={
                "sizeBytes": item.size_bytes,
                "payloadSha256": current_hash,
            },
        )
        artifact_records.append(artifact)
        session.add(artifact)

    run.environment_hash = runtime_result.environment_hash
    run.output_artifacts = [artifact.path for artifact in artifact_records]
    log_artifact = next(
        (artifact for artifact in artifact_records if Path(artifact.path).name == "execution.log"),
        None,
    )
    run.logs_path = log_artifact.path if log_artifact is not None else None
    run.status = runtime_result.status
    run.finished_at = utc_now()
    intent.status = runtime_result.status
    task.status = runtime_result.status
    session.add(
        EventRecord(
            id=str(uuid.uuid4()),
            project_id=project.id,
            event_type=f"analysis.run.{runtime_result.status}",
            payload={
                "analysisIntentId": intent.id,
                "runId": run.id,
                "payloadSha256": current_hash,
                "environmentHash": runtime_result.environment_hash,
                "artifactCount": len(artifact_records),
            },
        )
    )
    session.commit()
    return _analysis_run_out(session, run, intent, project)


@app.get(
    "/v1/projects/{project_id}/analysis-runs",
    response_model=list[AnalysisRunOut],
    dependencies=[Depends(require_token)],
)
def list_analysis_runs(
    project_id: str,
    session: Session = Depends(get_session),
) -> list[AnalysisRunOut]:
    project = _project_or_404(session, project_id)
    runs = list(
        session.scalars(
            select(RunRecord)
            .join(TaskRecord, RunRecord.task_id == TaskRecord.id)
            .where(
                TaskRecord.project_id == project_id,
                TaskRecord.task_type == "python-data-analysis",
            )
            .order_by(RunRecord.created_at.desc())
        )
    )
    response: list[AnalysisRunOut] = []
    for run in runs:
        intent = session.scalar(
            select(AnalysisIntentRecord).where(AnalysisIntentRecord.task_id == run.task_id)
        )
        if intent is not None:
            response.append(_analysis_run_out(session, run, intent, project))
    return response


@app.get("/v1/sources/{source_id}/file", dependencies=[Depends(require_token)])
def source_file(source_id: str, session: Session = Depends(get_session)) -> FileResponse:
    source = session.get(SourceRecord, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    raw_path = Path(source.local_path)
    if raw_path.is_symlink():
        raise HTTPException(status_code=409, detail="Source path may not be a symbolic link")
    path = raw_path.resolve()
    project = _project_or_404(session, source.project_id)
    _assert_beneath(Path(project.project_path), path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Source file is missing")
    if sha256_file(path) != source.content_hash:
        raise HTTPException(status_code=409, detail="Source content hash no longer matches its record")
    is_dataset = source.source_kind == "dataset"
    return FileResponse(
        path,
        media_type="text/csv" if is_dataset else "application/pdf",
        filename=f"{source.title}.csv" if is_dataset else f"{source.title}.pdf",
        content_disposition_type="attachment" if is_dataset else "inline",
    )


@app.get("/v1/artifacts/{artifact_id}/file", dependencies=[Depends(require_token)])
def artifact_file(artifact_id: str, session: Session = Depends(get_session)) -> FileResponse:
    artifact = session.get(ArtifactRecord, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    run = session.get(RunRecord, artifact.run_id)
    task = session.get(TaskRecord, run.task_id) if run is not None else None
    project = session.get(ProjectRecord, task.project_id) if task is not None else None
    if run is None or task is None or project is None:
        raise HTTPException(status_code=409, detail="Artifact provenance records are incomplete")

    path = (Path(project.project_path) / artifact.path).resolve()
    _assert_beneath(Path(project.project_path), path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact file is missing")
    if sha256_file(path) != artifact.content_hash:
        raise HTTPException(status_code=409, detail="Artifact content hash no longer matches its run")
    inline = artifact.mime_type.startswith(("image/", "text/")) or artifact.mime_type in {
        "application/json",
        "application/pdf",
    }
    return FileResponse(
        path,
        media_type=artifact.mime_type,
        filename=path.name,
        content_disposition_type="inline" if inline else "attachment",
    )


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
                "PaperQA model gateway is not configured. Set OPENAI_API_KEY "
                "for the internal MVP science-core process."
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
        ) from error

    answer_id = str(uuid.uuid4())
    answer_record = AnswerRecord(
        id=answer_id,
        project_id=project_id,
        question=payload.question,
        answer=result.answer,
        unresolved_questions=[],
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
    claim.confidence = max((evidence.confidence for evidence in verified), default=0.0)
    claim.review_status = "verified" if verified else "unreviewed"
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
                claim_type=claim.claim_type,
                confidence=claim.confidence,
                review_status=claim.review_status,
                evidence=[EvidenceOut.model_validate(item) for item in evidence_records],
            )
        ],
        unresolved_questions=answer_record.unresolved_questions,
        created_at=answer_record.created_at,
    )


def _analysis_intent_or_404(session: Session, intent_id: str) -> AnalysisIntentRecord:
    intent = session.get(AnalysisIntentRecord, intent_id)
    if intent is None:
        raise HTTPException(status_code=404, detail="Analysis intent not found")
    return intent


def _recover_interrupted_analysis_state(session: Session) -> None:
    recovered_at = utc_now()
    running_runs = list(
        session.scalars(select(RunRecord).where(RunRecord.status == "running"))
    )
    for run in running_runs:
        task = session.get(TaskRecord, run.task_id)
        if task is None or task.task_type != "python-data-analysis":
            continue
        intent = session.scalar(
            select(AnalysisIntentRecord).where(AnalysisIntentRecord.task_id == task.id)
        )
        run.status = "failed"
        run.finished_at = recovered_at
        task.status = "failed"
        if intent is not None and intent.status == "executing":
            intent.status = "failed"
        session.add(
            EventRecord(
                id=str(uuid.uuid4()),
                project_id=task.project_id,
                event_type="analysis.run.recovered-after-crash",
                payload={
                    "analysisIntentId": intent.id if intent is not None else None,
                    "runId": run.id,
                    "recoveredAt": recovered_at.isoformat(),
                },
            )
        )

    session.flush()
    orphaned_intents = list(
        session.scalars(
            select(AnalysisIntentRecord).where(AnalysisIntentRecord.status == "executing")
        )
    )
    for intent in orphaned_intents:
        task = session.get(TaskRecord, intent.task_id)
        intent.status = "failed"
        if task is not None:
            task.status = "failed"
            session.add(
                EventRecord(
                    id=str(uuid.uuid4()),
                    project_id=intent.project_id,
                    event_type="analysis.run.recovered-after-crash",
                    payload={
                        "analysisIntentId": intent.id,
                        "runId": None,
                        "recoveredAt": recovered_at.isoformat(),
                    },
                )
            )
    session.commit()


def _analysis_intent_out(intent: AnalysisIntentRecord) -> AnalysisIntentOut:
    return AnalysisIntentOut(
        id=intent.id,
        task_id=intent.task_id,
        project_id=intent.project_id,
        dataset_source_id=intent.dataset_source_id,
        objective=intent.objective,
        code=intent.code,
        payload_sha256=intent.payload_sha256,
        risk_level="high",
        affected_resources=[intent.dataset_source_id, "runs/<run-id>"],
        status=intent.status,
        decision=intent.decision,
        created_at=intent.created_at,
        updated_at=intent.updated_at,
    )


def _prepare_exchange_for_execution() -> None:
    _cleanup_stale_exchange_entries(reject_recent=True)


def _cleanup_stale_exchange_entries(*, reject_recent: bool) -> None:
    exchange_runs_dir = _child_path(settings.runtime_exchange_dir, "runs")
    exchange_runs_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    stale_before = time.time() - (settings.execution_timeout_seconds + 30)
    for entry in exchange_runs_dir.iterdir():
        try:
            modified_at = entry.lstat().st_mtime
        except OSError as error:
            if reject_recent:
                raise HTTPException(
                    status_code=409, detail="Runtime exchange is not inspectable"
                ) from error
            continue
        if modified_at > stale_before:
            if reject_recent:
                raise HTTPException(
                    status_code=409,
                    detail="Runtime exchange contains a recent unclaimed execution",
                )
            continue
        try:
            if entry.is_symlink() or entry.is_file():
                entry.unlink(missing_ok=True)
            elif entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink(missing_ok=True)
        except OSError as error:
            if reject_recent:
                raise HTTPException(
                    status_code=409,
                    detail="Could not remove a stale runtime exchange entry",
                ) from error


def _clear_run_outputs(run_dir: Path) -> None:
    if not run_dir.is_dir():
        return
    for child in run_dir.iterdir():
        if child.name == "input.csv":
            continue
        if child.is_symlink() or child.is_file():
            child.unlink(missing_ok=True)
        elif child.is_dir():
            shutil.rmtree(child)


def _remove_exchange_run(exchange_run_dir: Path) -> None:
    exchange_root = settings.runtime_exchange_dir.resolve()
    candidate = exchange_run_dir.resolve()
    try:
        candidate.relative_to(exchange_root)
    except ValueError:
        return
    if candidate != exchange_root:
        shutil.rmtree(candidate, ignore_errors=True)


def _record_execution_failure(
    *,
    session: Session,
    project: ProjectRecord,
    intent: AnalysisIntentRecord,
    task: TaskRecord,
    run: RunRecord,
    run_dir: Path,
    error: Exception,
) -> None:
    error_message = f"{type(error).__name__}: {error}\n"
    try:
        if not run_dir.exists():
            run_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
        error_path = _child_path(run_dir, "core-execution-error.log")
        error_path.write_text(error_message, encoding="utf-8")
        error_path.chmod(0o444)
        relative_path = error_path.relative_to(Path(project.project_path).resolve()).as_posix()
        artifact = ArtifactRecord(
            id=str(uuid.uuid4()),
            run_id=run.id,
            artifact_type="log",
            path=relative_path,
            mime_type="text/plain",
            content_hash=sha256_file(error_path),
            parent_artifacts=[intent.dataset_source_id],
            metadata_json={
                "sizeBytes": error_path.stat().st_size,
                "payloadSha256": intent.payload_sha256,
                "producer": "science-core",
            },
        )
        session.add(artifact)
        run.logs_path = relative_path
        run.output_artifacts = [relative_path]
    except OSError:
        # The SQLite event still preserves the failure if the project filesystem
        # itself is unavailable.
        run.logs_path = None
        run.output_artifacts = []

    run.status = "failed"
    run.finished_at = utc_now()
    intent.status = "failed"
    task.status = "failed"
    session.add(
        EventRecord(
            id=str(uuid.uuid4()),
            project_id=project.id,
            event_type="analysis.run.failed",
            payload={
                "analysisIntentId": intent.id,
                "runId": run.id,
                "payloadSha256": intent.payload_sha256,
                "error": error_message[:2_000],
            },
        )
    )
    session.commit()


def _analysis_run_out(
    session: Session,
    run: RunRecord,
    intent: AnalysisIntentRecord,
    project: ProjectRecord,
) -> AnalysisRunOut:
    artifacts = list(
        session.scalars(
            select(ArtifactRecord)
            .where(ArtifactRecord.run_id == run.id)
            .order_by(ArtifactRecord.created_at)
        )
    )
    stdout = _read_named_artifact(project, artifacts, ("stdout.txt",))
    stderr = _read_named_artifact(project, artifacts, ("stderr.txt",))
    log = _read_named_artifact(
        project,
        artifacts,
        ("execution.log", "core-execution-error.log"),
    )
    artifact_outputs: list[AnalysisArtifactOut] = []
    for artifact in artifacts:
        raw_size = artifact.metadata_json.get("sizeBytes", 0)
        size_bytes = raw_size if isinstance(raw_size, int) and raw_size >= 0 else 0
        artifact_outputs.append(
            AnalysisArtifactOut(
                id=artifact.id,
                artifact_type=artifact.artifact_type,
                path=artifact.path,
                mime_type=artifact.mime_type,
                content_hash=artifact.content_hash,
                size_bytes=size_bytes,
                created_at=artifact.created_at,
            )
        )
    return AnalysisRunOut(
        id=run.id,
        intent_id=intent.id,
        task_id=run.task_id,
        project_id=intent.project_id,
        dataset_source_id=intent.dataset_source_id,
        objective=intent.objective,
        code=intent.code,
        payload_sha256=intent.payload_sha256,
        status=run.status,
        environment_hash=run.environment_hash,
        input_artifacts=run.input_artifacts,
        output_artifacts=run.output_artifacts,
        stdout=stdout,
        stderr=stderr,
        log=log,
        logs=log,
        error=(stderr.strip() or log.strip() or "Analysis execution failed")
        if run.status == "failed"
        else None,
        artifacts=artifact_outputs,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


def _read_named_artifact(
    project: ProjectRecord,
    artifacts: list[ArtifactRecord],
    preferred_names: tuple[str, ...],
) -> str:
    by_name = {Path(artifact.path).name: artifact for artifact in artifacts}
    for name in preferred_names:
        artifact = by_name.get(name)
        if artifact is None:
            continue
        path = (Path(project.project_path) / artifact.path).resolve()
        _assert_beneath(Path(project.project_path), path)
        if path.is_file():
            return read_text_file(path)
    return ""


def _project_or_404(session: Session, project_id: str) -> ProjectRecord:
    project = session.get(ProjectRecord, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _child_path(parent: Path, child: str) -> Path:
    parent = parent.resolve()
    candidate = (parent / child).resolve()
    _assert_beneath(parent, candidate)
    return candidate


def _assert_beneath(parent: Path, candidate: Path) -> None:
    try:
        candidate.relative_to(parent.resolve())
    except ValueError as error:
        raise HTTPException(status_code=403, detail="Path escapes the project directory") from error
