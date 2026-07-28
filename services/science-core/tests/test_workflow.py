from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event, Lock
from typing import Any, Callable, Protocol, cast
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from open_science_core.api.report_drafts import get_report_draft_session
from open_science_core.api.report_drafts import router as report_draft_router
from open_science_core.api.workflows import get_workflow_session, router
from open_science_core.db import Base
from open_science_core.models import (
    AnswerRecord,
    ApprovalRecord,
    ClaimRecord,
    EventRecord,
    EvidenceSpanRecord,
    JobRecord,
    PlanRecord,
    ProjectRecord,
    ReportDraftRecord,
    ReviewRecord,
    SourcePageRecord,
    SourceRecord,
    TaskRecord,
    WorkflowRecord,
    utc_now,
)
from open_science_core.workflow import handlers as workflow_handlers
from open_science_core.workflow import report_drafts as report_draft_service
from open_science_core.workflow import service as workflow_service
from open_science_core.workflow._service import lifecycle as workflow_lifecycle
from open_science_core.workflow.schemas import (
    EvidenceRelationshipOut,
    WorkflowClaimOut,
    WorkflowResultOut,
)
from open_science_core.workflow.service import (
    WorkflowConflict,
    content_sha256,
    current_job_input_hash,
    job_input_hash_for_handler_version,
    resume_workflow,
    retry_workflow,
    task_input_hash,
)
from open_science_core.workflow.state import WorkflowFailure
from open_science_core.workflow.worker import WorkflowWorker

GOAL = "How do brain computer interfaces improve communication?"
PASSAGE = (
    "Brain computer interfaces improve communication for people with severe motor "
    "impairments using verified neural signals."
)


class _RequestClient(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Response: ...


class _Cursor(Protocol):
    def execute(self, statement: str) -> object: ...

    def close(self) -> None: ...


class _DbapiConnection(Protocol):
    def cursor(self) -> _Cursor: ...


_close_test_client = cast(Callable[[TestClient], None], getattr(TestClient, "close"))


class TypedTestClient(TestClient):
    def get(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("PUT", url, **kwargs)

    def close(self) -> None:
        _close_test_client(self)


_markdown_citation_input = cast(
    Callable[[str], report_draft_service.MarkdownCitationInput],
    getattr(report_draft_service, "_markdown_citation_input"),
)
_visible_citation_numbers = cast(
    Callable[[str], frozenset[int]],
    getattr(report_draft_service, "_visible_citation_numbers"),
)
_authoritative_base = cast(
    Callable[[Session, WorkflowRecord], report_draft_service.ReportBase],
    getattr(report_draft_service, "_authoritative_base"),
)
_evidence_snapshot = cast(
    Callable[[WorkflowResultOut], list[dict[str, object]]],
    getattr(report_draft_service, "_evidence_snapshot"),
)


class FakeModelGateway:
    def __init__(
        self,
        *,
        invalid_plan: bool = False,
        use_all_evidence: bool = False,
        synthesis_evidence_id: str | None = None,
    ) -> None:
        self.configured = True
        self.default_model = "test-research-model"
        self.endpoint_host = "models.internal.example"
        self.endpoint_path = "/v1/chat/completions"
        self.invalid_plan = invalid_plan
        self.use_all_evidence = use_all_evidence
        self.synthesis_evidence_id = synthesis_evidence_id
        self.calls: list[dict[str, Any]] = []

    @property
    def endpoint_identity(self) -> str:
        endpoint = f"https://{self.endpoint_host}{self.endpoint_path}"
        return f"sha256:{hashlib.sha256(endpoint.encode('utf-8')).hexdigest()}"

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        payload = json.loads(user_prompt)
        self.calls.append(
            {
                "systemPrompt": system_prompt,
                "payload": payload,
                "model": model,
            }
        )
        if "evidence" not in payload:
            if self.invalid_plan:
                return {
                    "schemaVersion": "1",
                    "steps": [
                        {
                            "type": "synthesize-extractive-claims",
                            "objective": "Skip required evidence validation.",
                            "maxClaims": 8,
                        }
                    ],
                }
            return {
                "schemaVersion": "1",
                "steps": [
                    {
                        "type": "inspect-sources",
                        "objective": "Validate the frozen local PDF source set.",
                    },
                    {
                        "type": "extract-local-evidence",
                        "objective": "Find source-diverse passages for the approved goal.",
                        "query": GOAL,
                        "maxPassages": 12,
                        "maxPerSource": 4,
                    },
                    {
                        "type": "synthesize-extractive-claims",
                        "objective": "Select exact atomic claims from verified passages.",
                        "maxClaims": 8,
                    },
                ],
            }
        evidence_items = (
            payload["evidence"]
            if self.use_all_evidence
            else [payload["evidence"][0]]
        )
        return {
            "schemaVersion": "1",
            "claims": [
                {
                    "statement": evidence["passage"],
                    "evidenceId": self.synthesis_evidence_id or evidence["evidenceId"],
                    "passage": evidence["passage"],
                }
                for evidence in evidence_items
            ],
            "unresolvedQuestions": [
                "Which populations require additional communication studies?"
            ],
        }


class WorkflowApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary_directory.name)
        self.engine = create_engine(
            f"sqlite:///{self.root / 'workflow.sqlite3'}",
            connect_args={"check_same_thread": False},
        )

        def configure_sqlite(dbapi_connection: _DbapiConnection, _record: object) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        event.listen(self.engine, "connect", configure_sqlite)
        with self.engine.connect() as connection:
            self.assertEqual(
                connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar_one(),
                "wal",
            )
        with (
            self.engine.connect() as first_connection,
            self.engine.connect() as second_connection,
        ):
            self.assertEqual(
                first_connection.exec_driver_sql("PRAGMA journal_mode").scalar_one(),
                "wal",
            )
            self.assertEqual(
                second_connection.exec_driver_sql("PRAGMA journal_mode").scalar_one(),
                "wal",
            )

        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.worker = WorkflowWorker(
            self.session_factory,
            poll_interval_seconds=0.01,
            lease_seconds=0.1,
            heartbeat_seconds=0.03,
        )
        self.client = self._new_client()
        self._create_project()

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()
        self._temporary_directory.cleanup()

    def _session_dependency(self) -> Generator[Session, None, None]:
        with self.session_factory() as session:
            yield session

    def _new_client(
        self,
        *,
        request_barrier: Barrier | None = None,
    ) -> TypedTestClient:
        app = FastAPI()
        app.include_router(router)
        app.include_router(report_draft_router)

        def synchronized_session_dependency() -> Generator[Session, None, None]:
            if request_barrier is not None:
                request_barrier.wait(timeout=10)
            yield from self._session_dependency()

        app.dependency_overrides[get_workflow_session] = synchronized_session_dependency
        app.dependency_overrides[get_report_draft_session] = (
            synchronized_session_dependency
        )
        return TypedTestClient(app)

    def _create_project(self) -> None:
        with self.session_factory() as session:
            session.add(
                ProjectRecord(
                    id="project-1",
                    title="Workflow test",
                    description="",
                    project_path=str(self.root),
                    execution_mode="safe",
                )
            )
            session.commit()

    def _add_ready_source(
        self,
        passage: str = PASSAGE,
        *,
        source_id: str = "source-1",
    ) -> None:
        path = self.root / f"{source_id}.pdf"
        path.write_bytes(f"%PDF-local-workflow-test-{source_id}".encode("utf-8"))
        words = [
            {
                "text": word,
                "x0": float(index * 10),
                "y0": 0.0,
                "x1": float(index * 10 + 8),
                "y1": 10.0,
                "block": 0,
                "line": 0,
                "word": index,
            }
            for index, word in enumerate(passage.split())
        ]
        with self.session_factory() as session:
            session.add(
                SourceRecord(
                    id=source_id,
                    project_id="project-1",
                    title=f"Local paper {source_id}",
                    source_kind="pdf",
                    authors=[],
                    local_path=str(path),
                    ingestion_status="ready",
                    content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
                    page_count=1,
                )
            )
            session.add(
                SourcePageRecord(
                    source_id=source_id,
                    page_index=0,
                    page_label="1",
                    width=500.0,
                    height=700.0,
                    text=passage,
                    words=words,
                )
            )
            session.commit()

    def _start(self, *, key: str = "create-workflow-0001") -> dict[str, Any]:
        response = self.client.post(
            "/v1/projects/project-1/workflows",
            headers={"Idempotency-Key": key},
            json={"workflowType": "literature-synthesis", "goal": GOAL},
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def _start_remote(
        self,
        *,
        key: str = "create-remote-workflow-0001",
    ) -> dict[str, Any]:
        response = self.client.post(
            "/v1/projects/project-1/workflows",
            headers={"Idempotency-Key": key},
            json={
                "workflowType": "literature-synthesis",
                "goal": GOAL,
                "generationMode": "remote-model-assisted",
                "remoteDataApproved": True,
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def _run_once(self) -> bool:
        return asyncio.run(self.worker.run_once())

    def _set_queued_job_handler(
        self,
        workflow_id: str,
        *,
        kind: str,
        handler_version: str,
    ) -> str:
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == kind,
                    JobRecord.status == "queued",
                )
            )
            assert job is not None
            assert workflow is not None
            task = session.get(TaskRecord, job.task_id) if job.task_id else None
            job.handler_version = handler_version
            job.input_sha256 = job_input_hash_for_handler_version(
                session,
                workflow,
                kind=kind,
                task=task,
                handler_version=handler_version,
            )
            job_id = job.id
            session.commit()
            return job_id

    def _plan(self, workflow_id: str) -> dict[str, Any]:
        self.assertTrue(self._run_once())
        response = self.client.get(f"/v1/workflows/{workflow_id}")
        self.assertEqual(response.status_code, 200, response.text)
        snapshot = response.json()
        self.assertEqual(snapshot["workflow"]["status"], "waiting-plan-approval")
        return snapshot

    def _approve(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        workflow = snapshot["workflow"]
        plan = snapshot["plan"]
        approval = snapshot["pendingApprovals"][0]
        response = self.client.post(
            f"/v1/workflows/{workflow['id']}/approve-plan",
            json={
                "approvalId": approval["id"],
                "planId": plan["id"],
                "planVersion": plan["version"],
                "planSha256": plan["planSha256"],
                "expectedWorkflowRevision": workflow["revision"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _complete_local_workflow(self, key: str) -> str:
        self._add_ready_source()
        started = self._start(key=key)
        planned = self._plan(started["workflow"]["id"])
        self._approve(planned)
        for _ in range(4):
            self.assertTrue(self._run_once())
        return started["workflow"]["id"]

    def _fail_initial_plan_job(self, workflow_id: str) -> tuple[int, str]:
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "generate-plan",
                    JobRecord.status == "queued",
                )
            )
            assert workflow is not None
            assert job is not None
            workflow.status = "failed"
            workflow.last_error_code = "test-plan-failure"
            workflow.last_error_message = "The deterministic plan failed in the test fixture."
            workflow.finished_at = utc_now()
            job.status = "failed"
            job.error_code = "test-plan-failure"
            job.error_message = "The deterministic plan failed in the test fixture."
            job.finished_at = utc_now()
            revision = workflow.row_version
            job_id = job.id
            session.commit()
            return revision, job_id

    def _prepare_legacy_workflow(self, key: str) -> str:
        started = self._start(key=key)
        workflow_id = started["workflow"]["id"]
        with self.session_factory() as session:
            created_event = session.scalar(
                select(EventRecord).where(
                    EventRecord.workflow_id == workflow_id,
                    EventRecord.event_type == "workflow.created",
                )
            )
            assert created_event is not None
            created_event.payload = {
                name: value
                for name, value in created_event.payload.items()
                if name != "generationMode"
            }
            session.commit()
        self._set_queued_job_handler(
            workflow_id,
            kind="generate-plan",
            handler_version="template-plan-v1",
        )
        self._approve(self._plan(workflow_id))
        return workflow_id

    def _complete_legacy_workflow(self, key: str) -> str:
        self._add_ready_source()
        workflow_id = self._prepare_legacy_workflow(key)
        for _ in range(3):
            self._set_queued_job_handler(
                workflow_id,
                kind="execute-task",
                handler_version="local-literature-v1",
            )
            self.assertTrue(self._run_once())
        self.assertTrue(self._run_once())
        return workflow_id

    def _assert_result_integrity_conflict(self, workflow_id: str) -> None:
        response = self.client.get(f"/v1/workflows/{workflow_id}")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "workflow-result-integrity-failed",
        )

    def _post_twice_at_statement(
        self,
        *,
        statement_fragment: str,
        endpoint: str,
        idempotency_key: str,
        second_idempotency_key: str | None = None,
        payload: dict[str, Any],
    ) -> list[Response]:
        barrier = Barrier(2)
        match_lock = Lock()
        match_count = 0

        def synchronize_competing_statements(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            nonlocal match_count
            if statement_fragment not in statement:
                return
            with match_lock:
                match_count += 1
                should_wait = match_count <= 2
            if should_wait:
                barrier.wait(timeout=10)

        event.listen(
            self.engine,
            "before_cursor_execute",
            synchronize_competing_statements,
        )
        try:
            return self._post_twice(
                endpoint=endpoint,
                idempotency_key=idempotency_key,
                second_idempotency_key=second_idempotency_key,
                payload=payload,
            )
        finally:
            event.remove(
                self.engine,
                "before_cursor_execute",
                synchronize_competing_statements,
            )

    def _post_after_committed_winner(
        self,
        *,
        endpoint: str,
        winner_key: str,
        loser_key: str,
        payload: dict[str, Any],
    ) -> list[Response]:
        enqueue_barrier = Barrier(2)
        winner_committed = Event()
        request_barrier = Barrier(2)
        original_enqueue_job = workflow_lifecycle.enqueue_job

        def ordered_enqueue_job(
            session: Session,
            workflow: WorkflowRecord,
            *,
            kind: str,
            operation_key: str,
            task: TaskRecord | None = None,
            attempt: int = 1,
            previous_job_id: str | None = None,
            request_idempotency_key: str | None = None,
            request_payload_sha256: str | None = None,
            delay_seconds: float = 0,
            handler_version: str | None = None,
        ) -> JobRecord:
            enqueue_barrier.wait(timeout=10)
            if request_idempotency_key == loser_key:
                if not winner_committed.wait(timeout=10):
                    raise AssertionError("winner did not commit before loser enqueue")
            return original_enqueue_job(
                session,
                workflow,
                kind=kind,
                operation_key=operation_key,
                task=task,
                attempt=attempt,
                previous_job_id=previous_job_id,
                request_idempotency_key=request_idempotency_key,
                request_payload_sha256=request_payload_sha256,
                delay_seconds=delay_seconds,
                handler_version=handler_version,
            )

        def post_from_independent_client(request_idempotency_key: str) -> Response:
            client = self._new_client(request_barrier=request_barrier)
            try:
                return client.post(
                    endpoint,
                    headers={"Idempotency-Key": request_idempotency_key},
                    json=payload,
                )
            finally:
                client.close()

        with (
            patch.object(
                workflow_lifecycle,
                "enqueue_job",
                new=ordered_enqueue_job,
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            winner_future = executor.submit(post_from_independent_client, winner_key)
            loser_future = executor.submit(post_from_independent_client, loser_key)
            try:
                winner_response = winner_future.result(timeout=20)
            finally:
                winner_committed.set()
            loser_response = loser_future.result(timeout=20)
        return [winner_response, loser_response]

    def _post_twice(
        self,
        *,
        endpoint: str,
        idempotency_key: str,
        second_idempotency_key: str | None = None,
        payload: dict[str, Any],
    ) -> list[Response]:
        request_barrier = Barrier(2)
        idempotency_keys = (
            idempotency_key,
            second_idempotency_key or idempotency_key,
        )

        def post_from_independent_client(request_idempotency_key: str) -> Response:
            client = self._new_client(request_barrier=request_barrier)
            try:
                return client.post(
                    endpoint,
                    headers={"Idempotency-Key": request_idempotency_key},
                    json=payload,
                )
            finally:
                client.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(post_from_independent_client, request_idempotency_key)
                for request_idempotency_key in idempotency_keys
            ]
            return [future.result(timeout=20) for future in futures]

    def test_concurrent_create_replays_the_single_durable_workflow(self) -> None:
        key = "concurrent-create-workflow-0001"
        responses = self._post_twice_at_statement(
            statement_fragment="INSERT INTO workflows",
            endpoint="/v1/projects/project-1/workflows",
            idempotency_key=key,
            payload={"workflowType": "literature-synthesis", "goal": GOAL},
        )

        self.assertEqual([response.status_code for response in responses], [202, 202])
        snapshots = [response.json() for response in responses]
        workflow_id = snapshots[0]["workflow"]["id"]
        self.assertEqual(snapshots[1], snapshots[0])
        with self.session_factory() as session:
            workflows = list(
                session.scalars(
                    select(WorkflowRecord).where(
                        WorkflowRecord.project_id == "project-1",
                        WorkflowRecord.create_idempotency_key == key,
                    )
                )
            )
            jobs = list(
                session.scalars(
                    select(JobRecord).where(JobRecord.workflow_id == workflow_id)
                )
            )
            self.assertEqual(len(workflows), 1)
            self.assertEqual(len(jobs), 1)

    def test_concurrent_retry_replays_the_single_successor_job(self) -> None:
        started = self._start(key="concurrent-retry-source-0001")
        workflow_id = started["workflow"]["id"]
        failed_revision, failed_job_id = self._fail_initial_plan_job(workflow_id)
        key = "concurrent-retry-mutation-0001"
        responses = self._post_twice_at_statement(
            statement_fragment="INSERT INTO workflow_jobs",
            endpoint=f"/v1/workflows/{workflow_id}/retry",
            idempotency_key=key,
            payload={"expectedWorkflowRevision": failed_revision},
        )

        self.assertEqual([response.status_code for response in responses], [202, 202])
        snapshots = [response.json() for response in responses]
        self.assertEqual(snapshots[1], snapshots[0])
        self.assertEqual(snapshots[0]["workflow"]["status"], "planning")
        self.assertEqual(
            snapshots[0]["workflow"]["revision"],
            failed_revision + 1,
        )
        with self.session_factory() as session:
            successors = list(
                session.scalars(
                    select(JobRecord).where(
                        JobRecord.previous_job_id == failed_job_id,
                        JobRecord.request_idempotency_key == key,
                    )
                )
            )
            self.assertEqual(len(successors), 1)

    def test_concurrent_taskless_retry_with_distinct_keys_returns_conflict(self) -> None:
        started = self._start(key="concurrent-distinct-retry-source-0001")
        workflow_id = started["workflow"]["id"]
        failed_revision, failed_job_id = self._fail_initial_plan_job(workflow_id)
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            assert workflow is not None
            failed_event_sequence = workflow.event_sequence

        first_key = "concurrent-distinct-retry-mutation-0001"
        second_key = "concurrent-distinct-retry-mutation-0002"
        responses = self._post_twice_at_statement(
            statement_fragment="INSERT INTO workflow_jobs",
            endpoint=f"/v1/workflows/{workflow_id}/retry",
            idempotency_key=first_key,
            second_idempotency_key=second_key,
            payload={"expectedWorkflowRevision": failed_revision},
        )

        self.assertEqual(sorted(response.status_code for response in responses), [202, 409])
        accepted = next(response for response in responses if response.status_code == 202)
        conflict = next(response for response in responses if response.status_code == 409)
        self.assertEqual(accepted.json()["workflow"]["status"], "planning")
        self.assertEqual(
            accepted.json()["workflow"]["revision"],
            failed_revision + 1,
        )
        self.assertEqual(
            conflict.json()["detail"]["code"],
            "workflow-revision-conflict",
        )
        self.assertTrue(conflict.json()["detail"]["retryable"])
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            successors = list(
                session.scalars(
                    select(JobRecord).where(JobRecord.previous_job_id == failed_job_id)
                )
            )
            retry_events = list(
                session.scalars(
                    select(EventRecord)
                    .where(
                        EventRecord.workflow_id == workflow_id,
                        EventRecord.sequence > failed_event_sequence,
                    )
                    .order_by(EventRecord.sequence)
                )
            )
            assert workflow is not None
            self.assertEqual(workflow.status, "planning")
            self.assertEqual(workflow.row_version, failed_revision + 1)
            self.assertEqual(workflow.event_sequence, failed_event_sequence + 1)
            self.assertEqual(len(successors), 1)
            self.assertIn(
                successors[0].request_idempotency_key,
                {first_key, second_key},
            )
            self.assertIsNotNone(successors[0].request_payload_sha256)
            self.assertEqual(len(retry_events), 1)
            self.assertEqual(retry_events[0].event_type, "workflow.status-changed")
            self.assertEqual(retry_events[0].payload["previousStatus"], "failed")
            self.assertEqual(retry_events[0].payload["status"], "planning")

    def test_taskless_retry_loser_prequery_after_winner_commit_returns_conflict(
        self,
    ) -> None:
        started = self._start(key="prequery-distinct-retry-source-0001")
        workflow_id = started["workflow"]["id"]
        failed_revision, failed_job_id = self._fail_initial_plan_job(workflow_id)
        winner_key = "prequery-distinct-retry-winner-0001"
        loser_key = "prequery-distinct-retry-loser-0001"

        winner, loser = self._post_after_committed_winner(
            endpoint=f"/v1/workflows/{workflow_id}/retry",
            winner_key=winner_key,
            loser_key=loser_key,
            payload={"expectedWorkflowRevision": failed_revision},
        )

        self.assertEqual(winner.status_code, 202, winner.text)
        self.assertEqual(loser.status_code, 409, loser.text)
        self.assertEqual(
            loser.json()["detail"]["code"],
            "workflow-revision-conflict",
        )
        self.assertTrue(loser.json()["detail"]["retryable"])
        with self.session_factory() as session:
            successors = list(
                session.scalars(select(JobRecord).where(JobRecord.previous_job_id == failed_job_id))
            )
            self.assertEqual(len(successors), 1)
            self.assertEqual(successors[0].request_idempotency_key, winner_key)

    def test_concurrent_execute_task_retry_applies_side_effects_once(self) -> None:
        started = self._start(key="concurrent-task-retry-source-0001")
        workflow_id = started["workflow"]["id"]
        self._approve(self._plan(workflow_id))
        with patch.object(
            workflow_handlers,
            "_handle_task",
            side_effect=WorkflowFailure(
                "test-execute-task-failure",
                "The execute-task failed deterministically in the test fixture.",
            ),
        ):
            self.assertTrue(self._run_once())

        with self.session_factory() as session:
            failed_job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "execute-task",
                    JobRecord.status == "failed",
                )
            )
            assert failed_job is not None
            assert failed_job.task_id is not None
            failed_task = session.get(TaskRecord, failed_job.task_id)
            failed_workflow = session.get(WorkflowRecord, workflow_id)
            assert failed_task is not None
            assert failed_workflow is not None
            self.assertEqual(failed_task.status, "failed")
            self.assertEqual(failed_task.retries, 0)
            self.assertEqual(failed_workflow.status, "failed")
            failed_job_id = failed_job.id
            failed_job_attempt = failed_job.attempt
            failed_task_id = failed_task.id
            failed_task_revision = failed_task.row_version
            failed_workflow_revision = failed_workflow.row_version
            failed_event_sequence = failed_workflow.event_sequence

        transition_barrier = Barrier(2)
        original_transition_task = workflow_lifecycle.transition_task

        def synchronized_transition_task(
            session: Session,
            task: TaskRecord,
            target: str,
        ) -> TaskRecord:
            if task.id == failed_task_id and target == "queued":
                transition_barrier.wait(timeout=10)
            return original_transition_task(session, task, target)

        key = "concurrent-task-retry-mutation-0001"
        with patch.object(
            workflow_lifecycle,
            "transition_task",
            side_effect=synchronized_transition_task,
        ):
            responses = self._post_twice(
                endpoint=f"/v1/workflows/{workflow_id}/retry",
                idempotency_key=key,
                payload={
                    "taskId": failed_task_id,
                    "expectedWorkflowRevision": failed_workflow_revision,
                },
            )

        self.assertEqual([response.status_code for response in responses], [202, 202])
        snapshots = [response.json() for response in responses]
        self.assertEqual(snapshots[1], snapshots[0])
        self.assertEqual(snapshots[0]["workflow"]["status"], "running")
        self.assertEqual(
            snapshots[0]["workflow"]["revision"],
            failed_workflow_revision + 1,
        )
        with self.session_factory() as session:
            task = session.get(TaskRecord, failed_task_id)
            workflow = session.get(WorkflowRecord, workflow_id)
            successors = list(
                session.scalars(
                    select(JobRecord).where(
                        JobRecord.previous_job_id == failed_job_id,
                        JobRecord.request_idempotency_key == key,
                    )
                )
            )
            retry_events = list(
                session.scalars(
                    select(EventRecord)
                    .where(
                        EventRecord.workflow_id == workflow_id,
                        EventRecord.sequence > failed_event_sequence,
                    )
                    .order_by(EventRecord.sequence)
                )
            )
            assert task is not None
            assert workflow is not None
            self.assertEqual(task.status, "queued")
            self.assertEqual(task.retries, 1)
            self.assertEqual(task.row_version, failed_task_revision + 1)
            self.assertIsNone(task.finished_at)
            self.assertEqual(workflow.status, "running")
            self.assertEqual(workflow.row_version, failed_workflow_revision + 1)
            self.assertEqual(workflow.event_sequence, failed_event_sequence + 1)
            self.assertEqual(len(successors), 1)
            self.assertEqual(successors[0].attempt, failed_job_attempt + 1)
            self.assertEqual(successors[0].status, "queued")
            self.assertEqual(successors[0].task_id, failed_task_id)
            self.assertEqual(len(retry_events), 1)
            self.assertEqual(retry_events[0].event_type, "workflow.status-changed")
            self.assertEqual(retry_events[0].payload["previousStatus"], "failed")
            self.assertEqual(retry_events[0].payload["status"], "running")

    def test_concurrent_resume_replays_after_task_cas_conflict(self) -> None:
        started = self._start(key="concurrent-resume-source-0001")
        planned = self._plan(started["workflow"]["id"])
        self._approve(planned)
        self.assertTrue(self._run_once())
        workflow_id = started["workflow"]["id"]
        blocked = self.client.get(f"/v1/workflows/{workflow_id}").json()
        self.assertEqual(blocked["workflow"]["status"], "blocked")
        self._add_ready_source(source_id="concurrent-resume-source")
        key = "concurrent-resume-mutation-0001"
        transition_barrier = Barrier(2)
        original_transition_task = workflow_lifecycle.transition_task

        def synchronized_transition_task(
            session: Session,
            task: TaskRecord,
            target: str,
        ) -> TaskRecord:
            transition_barrier.wait(timeout=10)
            return original_transition_task(session, task, target)

        with patch.object(
            workflow_lifecycle,
            "transition_task",
            side_effect=synchronized_transition_task,
        ):
            responses = self._post_twice(
                endpoint=f"/v1/workflows/{workflow_id}/resume",
                idempotency_key=key,
                payload={
                    "expectedWorkflowRevision": blocked["workflow"]["revision"],
                },
            )

        self.assertEqual([response.status_code for response in responses], [202, 202])
        snapshots = [response.json() for response in responses]
        self.assertEqual(snapshots[1], snapshots[0])
        self.assertEqual(snapshots[0]["workflow"]["status"], "running")
        with self.session_factory() as session:
            successors = list(
                session.scalars(
                    select(JobRecord).where(
                        JobRecord.workflow_id == workflow_id,
                        JobRecord.request_idempotency_key == key,
                    )
                )
            )
            self.assertEqual(len(successors), 1)

    def test_concurrent_analysis_replan_resume_with_distinct_keys_returns_conflict(
        self,
    ) -> None:
        dataset_path = self.root / "concurrent-analysis-replan.csv"
        dataset_bytes = b"value\n1\n"
        dataset_path.write_bytes(dataset_bytes)
        dataset_source_id = "concurrent-analysis-replan-source"
        with self.session_factory() as session:
            session.add(
                SourceRecord(
                    id=dataset_source_id,
                    project_id="project-1",
                    title="Concurrent analysis replan dataset",
                    source_kind="dataset",
                    authors=[],
                    local_path=str(dataset_path),
                    ingestion_status="ready",
                    content_hash=hashlib.sha256(dataset_bytes).hexdigest(),
                    page_count=None,
                )
            )
            session.commit()

        created = self.client.post(
            "/v1/projects/project-1/workflows",
            headers={"Idempotency-Key": "concurrent-analysis-replan-create-0001"},
            json={
                "workflowType": "dataset-analysis",
                "datasetSourceId": dataset_source_id,
                "goal": "Analyze the deterministic concurrent retry fixture.",
            },
        )
        self.assertEqual(created.status_code, 202, created.text)
        workflow_id = created.json()["workflow"]["id"]
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            initial_job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "generate-plan",
                    JobRecord.status == "queued",
                )
            )
            assert workflow is not None
            assert initial_job is not None
            session.delete(initial_job)
            workflow_lifecycle.transition_workflow(
                session,
                workflow,
                "blocked",
                reason_code="analysis-execution-rejected",
                blocking_message="The analysis requires a revised plan.",
            )
            session.commit()
            session.refresh(workflow)
            blocked_revision = workflow.row_version
            blocked_event_sequence = workflow.event_sequence

        first_key = "concurrent-analysis-replan-resume-0001"
        second_key = "concurrent-analysis-replan-resume-0002"
        responses = self._post_twice_at_statement(
            statement_fragment="INSERT INTO workflow_jobs",
            endpoint=f"/v1/workflows/{workflow_id}/resume",
            idempotency_key=first_key,
            second_idempotency_key=second_key,
            payload={"expectedWorkflowRevision": blocked_revision},
        )

        self.assertEqual(sorted(response.status_code for response in responses), [202, 409])
        accepted = next(response for response in responses if response.status_code == 202)
        conflict = next(response for response in responses if response.status_code == 409)
        self.assertEqual(accepted.json()["workflow"]["status"], "planning")
        self.assertEqual(
            accepted.json()["workflow"]["revision"],
            blocked_revision + 1,
        )
        self.assertEqual(
            conflict.json()["detail"]["code"],
            "workflow-revision-conflict",
        )
        self.assertTrue(conflict.json()["detail"]["retryable"])
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            jobs = list(
                session.scalars(
                    select(JobRecord).where(JobRecord.workflow_id == workflow_id)
                )
            )
            resume_events = list(
                session.scalars(
                    select(EventRecord)
                    .where(
                        EventRecord.workflow_id == workflow_id,
                        EventRecord.sequence > blocked_event_sequence,
                    )
                    .order_by(EventRecord.sequence)
                )
            )
            assert workflow is not None
            self.assertEqual(workflow.status, "planning")
            self.assertEqual(workflow.row_version, blocked_revision + 1)
            self.assertEqual(workflow.event_sequence, blocked_event_sequence + 1)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].kind, "generate-plan")
            self.assertEqual(jobs[0].status, "queued")
            self.assertIn(jobs[0].request_idempotency_key, {first_key, second_key})
            self.assertIsNotNone(jobs[0].request_payload_sha256)
            self.assertEqual(len(resume_events), 1)
            self.assertEqual(resume_events[0].event_type, "workflow.status-changed")
            self.assertEqual(resume_events[0].payload["previousStatus"], "blocked")
            self.assertEqual(resume_events[0].payload["status"], "planning")

    def test_analysis_replan_resume_loser_prequery_after_winner_commit_conflicts(
        self,
    ) -> None:
        dataset_path = self.root / "prequery-analysis-replan.csv"
        dataset_bytes = b"value\n1\n"
        dataset_path.write_bytes(dataset_bytes)
        dataset_source_id = "prequery-analysis-replan-source"
        with self.session_factory() as session:
            session.add(
                SourceRecord(
                    id=dataset_source_id,
                    project_id="project-1",
                    title="Prequery analysis replan dataset",
                    source_kind="dataset",
                    authors=[],
                    local_path=str(dataset_path),
                    ingestion_status="ready",
                    content_hash=hashlib.sha256(dataset_bytes).hexdigest(),
                    page_count=None,
                )
            )
            session.commit()

        created = self.client.post(
            "/v1/projects/project-1/workflows",
            headers={"Idempotency-Key": "prequery-analysis-replan-create-0001"},
            json={
                "workflowType": "dataset-analysis",
                "datasetSourceId": dataset_source_id,
                "goal": "Analyze the deterministic committed-winner fixture.",
            },
        )
        self.assertEqual(created.status_code, 202, created.text)
        workflow_id = created.json()["workflow"]["id"]
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            initial_job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "generate-plan",
                    JobRecord.status == "queued",
                )
            )
            assert workflow is not None
            assert initial_job is not None
            session.delete(initial_job)
            workflow_lifecycle.transition_workflow(
                session,
                workflow,
                "blocked",
                reason_code="analysis-execution-rejected",
                blocking_message="The analysis requires a revised plan.",
            )
            session.commit()
            session.refresh(workflow)
            blocked_revision = workflow.row_version

        winner_key = "prequery-analysis-replan-winner-0001"
        loser_key = "prequery-analysis-replan-loser-0001"
        winner, loser = self._post_after_committed_winner(
            endpoint=f"/v1/workflows/{workflow_id}/resume",
            winner_key=winner_key,
            loser_key=loser_key,
            payload={"expectedWorkflowRevision": blocked_revision},
        )

        self.assertEqual(winner.status_code, 202, winner.text)
        self.assertEqual(loser.status_code, 409, loser.text)
        self.assertEqual(
            loser.json()["detail"]["code"],
            "workflow-revision-conflict",
        )
        self.assertTrue(loser.json()["detail"]["retryable"])
        with self.session_factory() as session:
            jobs = list(
                session.scalars(select(JobRecord).where(JobRecord.workflow_id == workflow_id))
            )
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].request_idempotency_key, winner_key)

    def test_non_idempotency_integrity_error_is_preserved(self) -> None:
        started = self._start(key="unrelated-integrity-source-0001")
        workflow_id = started["workflow"]["id"]
        failed_revision, _failed_job_id = self._fail_initial_plan_job(workflow_id)
        forced_error = IntegrityError(
            "forced unrelated failure",
            {},
            RuntimeError("not an idempotency collision"),
        )
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            assert workflow is not None
            with patch(
                "open_science_core.workflow._service.lifecycle.enqueue_job",
                side_effect=forced_error,
            ):
                with self.assertRaises(IntegrityError) as raised:
                    retry_workflow(
                        session,
                        workflow,
                        task_id=None,
                        expected_revision=failed_revision,
                        idempotency_key="unrelated-integrity-mutation-0001",
                    )
        self.assertIs(raised.exception, forced_error)

    def test_resume_non_idempotency_integrity_error_is_preserved(self) -> None:
        started = self._start(key="unrelated-resume-integrity-source-0001")
        planned = self._plan(started["workflow"]["id"])
        self._approve(planned)
        self.assertTrue(self._run_once())
        workflow_id = started["workflow"]["id"]
        blocked = self.client.get(f"/v1/workflows/{workflow_id}").json()
        self.assertEqual(blocked["workflow"]["status"], "blocked")
        self._add_ready_source(source_id="unrelated-resume-integrity-source")
        forced_error = IntegrityError(
            "forced unrelated resume failure",
            {},
            RuntimeError("not an idempotency collision"),
        )
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            assert workflow is not None
            with patch(
                "open_science_core.workflow._service.lifecycle.enqueue_job",
                side_effect=forced_error,
            ):
                with self.assertRaises(IntegrityError) as raised:
                    resume_workflow(
                        session,
                        workflow,
                        expected_revision=blocked["workflow"]["revision"],
                        idempotency_key="unrelated-resume-integrity-mutation-0001",
                    )
        self.assertIs(raised.exception, forced_error)
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            blocked_task = session.scalar(
                select(TaskRecord).where(
                    TaskRecord.workflow_id == workflow_id,
                    TaskRecord.status == "blocked",
                )
            )
            assert workflow is not None
            assert blocked_task is not None
            self.assertEqual(workflow.row_version, blocked["workflow"]["revision"])
            self.assertEqual(blocked_task.retries, 0)

    def test_create_idempotency_and_payload_conflict(self) -> None:
        first = self._start()
        repeated = self._start()
        self.assertEqual(first["workflow"]["id"], repeated["workflow"]["id"])

        conflict = self.client.post(
            "/v1/projects/project-1/workflows",
            headers={"Idempotency-Key": "create-workflow-0001"},
            json={
                "workflowType": "literature-synthesis",
                "goal": "A different research goal",
            },
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"]["code"], "idempotency-key-reused")
        self.assertFalse(conflict.json()["detail"]["retryable"])

    def test_retry_idempotency_replays_exact_request_and_rejects_key_reuse(self) -> None:
        started = self._start(key="retry-source-workflow-0001")
        workflow_id = started["workflow"]["id"]
        failed_revision, failed_job_id = self._fail_initial_plan_job(workflow_id)
        endpoint = f"/v1/workflows/{workflow_id}/retry"
        key = "retry-mutation-0001"
        payload = {"expectedWorkflowRevision": failed_revision}

        first = self.client.post(
            endpoint,
            headers={"Idempotency-Key": key},
            json=payload,
        )
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(first.json()["workflow"]["status"], "planning")
        immediate_replay = self.client.post(
            endpoint,
            headers={"Idempotency-Key": key},
            json=payload,
        )
        self.assertEqual(immediate_replay.status_code, 202, immediate_replay.text)
        self.assertEqual(immediate_replay.json(), first.json())

        with self.session_factory() as session:
            successor_jobs = list(
                session.scalars(
                    select(JobRecord).where(
                        JobRecord.previous_job_id == failed_job_id,
                        JobRecord.request_idempotency_key == key,
                    )
                )
            )
            self.assertEqual(len(successor_jobs), 1)
            self.assertIsNotNone(successor_jobs[0].request_payload_sha256)
            successor_job_id = successor_jobs[0].id

        self.assertTrue(self._run_once())
        current = self.client.get(f"/v1/workflows/{workflow_id}")
        self.assertEqual(current.status_code, 200, current.text)
        completed_job_replay = self.client.post(
            endpoint,
            headers={"Idempotency-Key": key},
            json=payload,
        )
        self.assertEqual(completed_job_replay.status_code, 202, completed_job_replay.text)
        self.assertEqual(completed_job_replay.json(), current.json())
        unrelated_key = self.client.post(
            endpoint,
            headers={"Idempotency-Key": "retry-mutation-unrelated-0001"},
            json=payload,
        )
        self.assertEqual(unrelated_key.status_code, 409, unrelated_key.text)
        self.assertEqual(
            unrelated_key.json()["detail"]["code"],
            "workflow-not-retryable",
        )

        changed_revision = self.client.post(
            endpoint,
            headers={"Idempotency-Key": key},
            json={"expectedWorkflowRevision": failed_revision + 1},
        )
        self.assertEqual(changed_revision.status_code, 409, changed_revision.text)
        self.assertEqual(
            changed_revision.json()["detail"]["code"],
            "idempotency-key-reused",
        )
        changed_task = self.client.post(
            endpoint,
            headers={"Idempotency-Key": key},
            json={**payload, "taskId": "different-task"},
        )
        self.assertEqual(changed_task.status_code, 409, changed_task.text)
        self.assertEqual(
            changed_task.json()["detail"]["code"],
            "idempotency-key-reused",
        )
        cross_action = self.client.post(
            f"/v1/workflows/{workflow_id}/resume",
            headers={"Idempotency-Key": key},
            json=payload,
        )
        self.assertEqual(cross_action.status_code, 409, cross_action.text)
        self.assertEqual(
            cross_action.json()["detail"]["code"],
            "idempotency-key-reused",
        )

        other = self._start(key="retry-source-workflow-0002")
        other_id = other["workflow"]["id"]
        other_revision, _other_failed_job_id = self._fail_initial_plan_job(other_id)
        cross_workflow = self.client.post(
            f"/v1/workflows/{other_id}/retry",
            headers={"Idempotency-Key": key},
            json={"expectedWorkflowRevision": other_revision},
        )
        self.assertEqual(cross_workflow.status_code, 409, cross_workflow.text)
        self.assertEqual(
            cross_workflow.json()["detail"]["code"],
            "idempotency-key-reused",
        )
        stale_new_request = self.client.post(
            f"/v1/workflows/{other_id}/retry",
            headers={"Idempotency-Key": "retry-mutation-stale-0001"},
            json={"expectedWorkflowRevision": other_revision + 1},
        )
        self.assertEqual(stale_new_request.status_code, 409, stale_new_request.text)
        self.assertEqual(
            stale_new_request.json()["detail"]["code"],
            "workflow-revision-conflict",
        )
        with self.session_factory() as session:
            self.assertEqual(
                session.scalar(
                    select(JobRecord.id).where(JobRecord.id == successor_job_id)
                ),
                successor_job_id,
            )
            self.assertEqual(
                len(
                    list(
                        session.scalars(
                            select(JobRecord).where(
                                JobRecord.request_idempotency_key == key
                            )
                        )
                    )
                ),
                1,
            )
            self.assertIsNone(
                session.scalar(
                    select(JobRecord.id).where(
                        JobRecord.request_idempotency_key
                        == "retry-mutation-stale-0001"
                    )
                )
            )

    def test_retry_service_replays_before_status_gate(self) -> None:
        started = self._start(key="retry-service-source-0001")
        workflow_id = started["workflow"]["id"]
        revision, failed_job_id = self._fail_initial_plan_job(workflow_id)
        key = "retry-service-mutation-0001"

        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            assert workflow is not None
            first = retry_workflow(
                session,
                workflow,
                task_id=None,
                expected_revision=revision,
                idempotency_key=key,
            )
            first_revision = first.row_version
            replay = retry_workflow(
                session,
                first,
                task_id=None,
                expected_revision=revision,
                idempotency_key=key,
            )
            self.assertEqual(replay.id, workflow_id)
            self.assertEqual(replay.row_version, first_revision)
            self.assertEqual(
                len(
                    list(
                        session.scalars(
                            select(JobRecord).where(
                                JobRecord.previous_job_id == failed_job_id,
                                JobRecord.request_idempotency_key == key,
                            )
                        )
                    )
                ),
                1,
            )
            with self.assertRaisesRegex(
                WorkflowConflict,
                "already used with a different workflow request",
            ):
                retry_workflow(
                    session,
                    replay,
                    task_id=None,
                    expected_revision=revision + 1,
                    idempotency_key=key,
                )

    def test_retry_rejects_legacy_key_without_request_binding(self) -> None:
        started = self._start(key="retry-legacy-binding-source-0001")
        workflow_id = started["workflow"]["id"]
        revision, failed_job_id = self._fail_initial_plan_job(workflow_id)
        key = "retry-legacy-unbound-key-0001"
        with self.session_factory() as session:
            failed_job = session.get(JobRecord, failed_job_id)
            assert failed_job is not None
            failed_job.request_idempotency_key = key
            failed_job.request_payload_sha256 = None
            session.commit()

        response = self.client.post(
            f"/v1/workflows/{workflow_id}/retry",
            headers={"Idempotency-Key": key},
            json={"expectedWorkflowRevision": revision},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "idempotency-key-reused")
        with self.session_factory() as session:
            self.assertEqual(
                len(
                    list(
                        session.scalars(
                            select(JobRecord).where(
                                JobRecord.workflow_id == workflow_id
                            )
                        )
                    )
                ),
                1,
            )

    def test_resume_service_replays_before_status_gate(self) -> None:
        started = self._start(key="resume-service-source-0001")
        planned = self._plan(started["workflow"]["id"])
        self._approve(planned)
        self.assertTrue(self._run_once())
        blocked = self.client.get(
            f"/v1/workflows/{started['workflow']['id']}"
        ).json()
        self.assertEqual(blocked["workflow"]["status"], "blocked")
        self._add_ready_source(source_id="resume-service-source")
        workflow_id = started["workflow"]["id"]
        revision = blocked["workflow"]["revision"]
        key = "resume-service-mutation-0001"

        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            assert workflow is not None
            first = resume_workflow(
                session,
                workflow,
                expected_revision=revision,
                idempotency_key=key,
            )
            first_revision = first.row_version
            replay = resume_workflow(
                session,
                first,
                expected_revision=revision,
                idempotency_key=key,
            )
            self.assertEqual(replay.id, workflow_id)
            self.assertEqual(replay.row_version, first_revision)
            self.assertEqual(
                len(
                    list(
                        session.scalars(
                            select(JobRecord).where(
                                JobRecord.workflow_id == workflow_id,
                                JobRecord.request_idempotency_key == key,
                            )
                        )
                    )
                ),
                1,
            )
            with self.assertRaisesRegex(
                WorkflowConflict,
                "already used with a different workflow request",
            ):
                resume_workflow(
                    session,
                    replay,
                    expected_revision=revision + 1,
                    idempotency_key=key,
                )

    def test_legacy_created_event_defaults_to_local_generation_mode(self) -> None:
        started = self._start(key="legacy-created-event-0001")
        workflow_id = started["workflow"]["id"]
        with self.session_factory() as session:
            event = session.scalar(
                select(EventRecord).where(
                    EventRecord.workflow_id == workflow_id,
                    EventRecord.event_type == "workflow.created",
                )
            )
            assert event is not None
            event.payload = {
                "workflowType": "literature-synthesis",
                "goalSha256": hashlib.sha256(GOAL.encode("utf-8")).hexdigest(),
            }
            session.commit()
        response = self.client.get(
            f"/v1/workflows/{workflow_id}/events?after=0&limit=20"
        )
        self.assertEqual(response.status_code, 200, response.text)
        created = response.json()["events"][0]
        self.assertEqual(created["data"]["generationMode"], "local-deterministic")

    def test_queued_legacy_plan_handler_resumes_only_for_local_workflow(self) -> None:
        started = self._start(key="legacy-plan-local-0001")
        workflow_id = started["workflow"]["id"]
        legacy_job_id = self._set_queued_job_handler(
            workflow_id,
            kind="generate-plan",
            handler_version="template-plan-v1",
        )
        self.assertTrue(self._run_once())
        planned = self.client.get(f"/v1/workflows/{workflow_id}").json()
        self.assertEqual(planned["workflow"]["status"], "waiting-plan-approval")
        self.assertEqual(planned["plan"]["generator"], "template-v1")
        with self.session_factory() as session:
            legacy_job = session.get(JobRecord, legacy_job_id)
            assert legacy_job is not None
            self.assertEqual(legacy_job.status, "succeeded")
            self.assertEqual(legacy_job.handler_version, "template-plan-v1")
            approval = session.scalar(
                select(ApprovalRecord).where(
                    ApprovalRecord.workflow_id == workflow_id
                )
            )
            assert approval is not None
            self.assertEqual(
                approval.payload_schema_version,
                "workflow-plan-approval-v1",
            )
            approval_event = session.scalar(
                select(EventRecord).where(
                    EventRecord.workflow_id == workflow_id,
                    EventRecord.event_type == "approval.requested",
                )
            )
            assert approval_event is not None
            self.assertNotIn("riskLevel", approval_event.payload)

    def test_remote_workflow_rejects_legacy_handler_even_with_matching_legacy_hash(self) -> None:
        self._add_ready_source()
        gateway = FakeModelGateway()
        with (
            patch.object(workflow_service, "model_gateway", gateway),
            patch.object(workflow_handlers, "model_gateway", gateway),
        ):
            started = self._start_remote(key="remote-legacy-handler-0001")
            workflow_id = started["workflow"]["id"]
            self._set_queued_job_handler(
                workflow_id,
                kind="generate-plan",
                handler_version="template-plan-v1",
            )
            self.assertTrue(self._run_once())
        self.assertEqual(len(gateway.calls), 0)
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            failed_job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.status == "failed",
                )
            )
            assert workflow is not None
            assert failed_job is not None
            self.assertEqual(workflow.status, "failed")
            self.assertEqual(failed_job.error_code, "job-input-changed")

    def test_plan_approval_rejects_hash_stale_revision_and_corruption(self) -> None:
        started = self._start()
        planned = self._plan(started["workflow"]["id"])
        workflow = planned["workflow"]
        plan = planned["plan"]
        approval = planned["pendingApprovals"][0]
        endpoint = f"/v1/workflows/{workflow['id']}/approve-plan"
        payload = {
            "approvalId": approval["id"],
            "planId": plan["id"],
            "planVersion": plan["version"],
            "planSha256": "0" * 64,
            "expectedWorkflowRevision": workflow["revision"],
        }

        hash_conflict = self.client.post(endpoint, json=payload)
        self.assertEqual(hash_conflict.status_code, 409)
        self.assertEqual(hash_conflict.json()["detail"]["code"], "plan-hash-mismatch")

        payload["planSha256"] = plan["planSha256"]
        payload["expectedWorkflowRevision"] = workflow["revision"] - 1
        stale = self.client.post(endpoint, json=payload)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["detail"]["code"], "workflow-revision-conflict")

        with self.session_factory() as session:
            stored_plan = session.get(type(self)._plan_record_type(), plan["id"])
            assert stored_plan is not None
            stored_plan.spec_json = {**stored_plan.spec_json, "goal": "tampered"}
            session.commit()
        payload["expectedWorkflowRevision"] = workflow["revision"]
        corrupt = self.client.post(endpoint, json=payload)
        self.assertEqual(corrupt.status_code, 409)
        self.assertEqual(corrupt.json()["detail"]["code"], "plan-content-corrupt")

    def test_legacy_task_rejects_post_approval_plan_tamper(self) -> None:
        self._add_ready_source()
        started = self._start(key="legacy-plan-tamper-0001")
        workflow_id = started["workflow"]["id"]
        planned = self._plan(workflow_id)
        self._approve(planned)
        self._set_queued_job_handler(
            workflow_id,
            kind="execute-task",
            handler_version="local-literature-v1",
        )
        with self.session_factory() as session:
            plan = session.get(PlanRecord, planned["plan"]["id"])
            assert plan is not None
            plan.spec_json = {**plan.spec_json, "goal": "Post-approval tamper"}
            session.commit()

        self.assertTrue(self._run_once())

        with self.session_factory() as session:
            failed_job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.status == "failed",
                )
            )
            assert failed_job is not None
            self.assertEqual(failed_job.error_code, "plan-content-corrupt")
            failed_workflow = session.get(WorkflowRecord, workflow_id)
            assert failed_workflow is not None
            self.assertEqual(failed_workflow.status, "failed")
            self.assertIsNone(
                session.scalar(
                    select(AnswerRecord).where(AnswerRecord.workflow_id == workflow_id)
                )
            )
            self.assertIsNone(
                session.scalar(
                    select(ReviewRecord).where(ReviewRecord.workflow_id == workflow_id)
                )
            )

    def test_pending_approval_metadata_tamper_is_not_displayed_or_approved(self) -> None:
        started = self._start(key="approval-metadata-tamper-0001")
        planned = self._plan(started["workflow"]["id"])
        with self.session_factory() as session:
            approval = session.get(
                ApprovalRecord,
                planned["pendingApprovals"][0]["id"],
            )
            assert approval is not None
            approval.reason = "A substituted consent reason."
            session.commit()
        response = self.client.get(
            f"/v1/workflows/{started['workflow']['id']}"
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "plan-approval-semantics-invalid",
        )
        workflow = planned["workflow"]
        plan = planned["plan"]
        approval = planned["pendingApprovals"][0]
        approve_response = self.client.post(
            f"/v1/workflows/{workflow['id']}/approve-plan",
            json={
                "approvalId": approval["id"],
                "planId": plan["id"],
                "planVersion": plan["version"],
                "planSha256": plan["planSha256"],
                "expectedWorkflowRevision": workflow["revision"],
            },
        )
        self.assertEqual(approve_response.status_code, 409, approve_response.text)
        self.assertEqual(
            approve_response.json()["detail"]["code"],
            "plan-approval-semantics-invalid",
        )

    @staticmethod
    def _plan_record_type() -> type[PlanRecord]:
        return PlanRecord

    def test_three_steps_review_and_event_cursor_complete(self) -> None:
        self._add_ready_source()
        started = self._start()
        planned = self._plan(started["workflow"]["id"])
        step_types = [step["type"] for step in planned["plan"]["spec"]["steps"]]
        self.assertEqual(
            step_types,
            [
                "inspect-sources",
                "extract-local-evidence",
                "synthesize-extractive-claims",
            ],
        )
        approved = self._approve(planned)
        self.assertEqual(approved["workflow"]["status"], "running")
        for _ in range(4):
            self.assertTrue(self._run_once())

        final = self.client.get(
            f"/v1/workflows/{started['workflow']['id']}"
        ).json()
        self.assertEqual(final["workflow"]["status"], "completed")
        self.assertEqual([step["status"] for step in final["plan"]["steps"]], [
            "completed",
            "completed",
            "completed",
        ])
        self.assertEqual(final["latestReview"]["verdict"], "passed")
        self.assertTrue(final["result"]["claims"])
        self.assertTrue(
            all(claim["supportStatus"] == "supported" for claim in final["result"]["claims"])
        )
        self.assertEqual(final["allowedActions"], [])

        first_page = self.client.get(
            f"/v1/workflows/{started['workflow']['id']}/events?after=0&limit=4"
        ).json()
        self.assertEqual([event["sequence"] for event in first_page["events"]], [1, 2, 3, 4])
        self.assertTrue(first_page["hasMore"])
        remainder = self.client.get(
            f"/v1/workflows/{started['workflow']['id']}/events"
            f"?after={first_page['nextAfter']}&limit=100"
        ).json()
        sequences = [event["sequence"] for event in first_page["events"] + remainder["events"]]
        self.assertEqual(sequences, list(range(1, final["eventCursor"] + 1)))

    def test_report_draft_inline_boundaries_cannot_form_citations(self) -> None:
        for content in (
            r"[1\_2]",
            "[1`code`, 2]",
            "[1<!-- hidden -->, 2]",
            "[1![plot](plot.png), 2]",
        ):
            with self.subTest(content=content):
                markdown = _markdown_citation_input(content)
                with self.assertRaises(WorkflowFailure):
                    for visible_block in markdown.visible_blocks:
                        _visible_citation_numbers(visible_block)

        for content in (r"\[999\]", "`values[999]`"):
            with self.subTest(exempt_content=content):
                markdown = _markdown_citation_input(content)
                numbers = {
                    number
                    for visible_block in markdown.visible_blocks
                    for number in _visible_citation_numbers(visible_block)
                }
                self.assertEqual(numbers, set())

        link_label = _markdown_citation_input(
            "[See citation [1]](https://example.test)"
        )
        self.assertEqual(
            {
                number
                for visible_block in link_label.visible_blocks
                for number in _visible_citation_numbers(visible_block)
            },
            {1},
        )

    def test_persistent_report_draft_cas_restart_and_stale_export_gate(self) -> None:
        self._add_ready_source()
        self._add_ready_source(
            passage=(
                "Brain computer interfaces improve communication using reproducible "
                "neural decoding for people with severe motor impairments."
            ),
            source_id="source-2",
        )
        started = self._start(key="report-draft-workflow-0001")
        workflow_id = started["workflow"]["id"]
        planned = self._plan(workflow_id)
        self._approve(planned)
        for _ in range(4):
            self.assertTrue(self._run_once())
        final = self.client.get(f"/v1/workflows/{workflow_id}").json()
        self.assertEqual(final["workflow"]["status"], "completed")
        self.assertEqual(final["result"]["integrityStatus"], "verified-frozen-v2")

        draft_url = f"/v1/projects/project-1/workflows/{workflow_id}/report-draft"
        created_response = self.client.post(
            draft_url,
            headers={"Idempotency-Key": "report-draft-create-0001"},
            json={"schemaVersion": "1"},
        )
        self.assertEqual(created_response.status_code, 201, created_response.text)
        created = created_response.json()
        self.assertEqual(created["revision"], 1)
        self.assertEqual(created["status"], "draft")
        self.assertIn("[@evidence:", created["contentMarkdown"])
        self.assertIn("[1]", created["contentMarkdown"])
        self.assertIn("[2]", created["contentMarkdown"])
        for key in (
            "contentSha256",
            "baseWorkflowSha256",
            "baseResultSha256",
            "baseEvidenceSha256",
        ):
            self.assertRegex(created[key], r"^[0-9a-f]{64}$")

        replay = self.client.post(
            draft_url,
            headers={"Idempotency-Key": "report-draft-create-0001"},
            json={"schemaVersion": "1"},
        )
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.json(), created)

        saved_content = (
            created["contentMarkdown"]
            + "\nResearcher note.\n"
            + "\nSupported together [1, 2].\n"
            + "\n```python\nvalues[999]\n"
            + "999. Hidden <!-- "
            + "[@evidence:fake-fenced:"
            + ("0" * 64)
            + "] -->\n```\n"
            + "\nInline code `arr[999]` is not a citation.\n"
            + "\n    values[999]\n"
            + "    998. Hidden <!-- "
            + "[@evidence:fake-indented:"
            + ("1" * 64)
            + "] -->\n"
            + "\n- Nested example:\n\n"
            + "  ```text\n"
            + "  997. Hidden <!-- "
            + "[@evidence:fake-list-fence:"
            + ("2" * 64)
            + "] -->\n"
            + "  ```\n"
            + "\n[Source link](https://example.test/items/[999])\n"
            + "\n![Plot [999]](https://example.test/images/[999].png)\n"
            + "\nEscaped brackets \\[999\\] remain literal text.\n"
        )
        save_url = (
            f"/v1/projects/project-1/workflows/{workflow_id}"
            f"/report-drafts/{created['id']}"
        )
        save_payload = {
            "expectedRevision": created["revision"],
            "expectedContentSha256": created["contentSha256"],
            "contentMarkdown": saved_content,
        }
        saved_response = self.client.put(
            save_url,
            headers={"Idempotency-Key": "report-draft-save-0001"},
            json=save_payload,
        )
        self.assertEqual(saved_response.status_code, 200, saved_response.text)
        saved = saved_response.json()
        self.assertEqual(saved["revision"], 2)
        self.assertEqual(saved["contentMarkdown"], saved_content)
        self.assertEqual(saved["status"], "draft")

        save_replay = self.client.put(
            save_url,
            headers={"Idempotency-Key": "report-draft-save-0001"},
            json=save_payload,
        )
        self.assertEqual(save_replay.status_code, 200, save_replay.text)
        self.assertEqual(save_replay.json(), saved)
        stale_create_replay = self.client.post(
            draft_url,
            headers={"Idempotency-Key": "report-draft-create-0001"},
            json={"schemaVersion": "1"},
        )
        self.assertEqual(stale_create_replay.status_code, 409, stale_create_replay.text)
        self.assertEqual(
            stale_create_replay.json()["detail"]["code"],
            "report-draft-idempotency-stale",
        )
        reused_save_key = self.client.put(
            save_url,
            headers={"Idempotency-Key": "report-draft-save-0001"},
            json={
                "expectedRevision": saved["revision"],
                "expectedContentSha256": saved["contentSha256"],
                "contentMarkdown": f"{saved_content}\nDifferent request.\n",
            },
        )
        self.assertEqual(reused_save_key.status_code, 409, reused_save_key.text)
        self.assertEqual(
            reused_save_key.json()["detail"]["code"],
            "report-draft-idempotency-conflict",
        )
        invalid_citations = (
            "Unsupported claim [999].",
            "Unsupported claim [ 999 ].",
            "Mixed support [1, 999].",
            "Claim.[999]",
            "claim[999]",
            "Bare code index values[999].",
            "Malformed group [1; 2].",
            "Indented fake reference is not a binding [998].",
            "List-fenced fake reference is not a binding [997].",
            "Unsupported claim\n    [999]",
        )
        for index, invalid_citation in enumerate(invalid_citations, start=1):
            with self.subTest(invalid_citation=invalid_citation):
                unsupported_visible_citation = self.client.put(
                    save_url,
                    headers={
                        "Idempotency-Key": (
                            f"report-draft-save-invalid-citation-{index:04d}"
                        )
                    },
                    json={
                        "expectedRevision": saved["revision"],
                        "expectedContentSha256": saved["contentSha256"],
                        "contentMarkdown": (
                            f"{saved_content}\n{invalid_citation}\n"
                        ),
                    },
                )
                self.assertEqual(
                    unsupported_visible_citation.status_code,
                    409,
                    unsupported_visible_citation.text,
                )
                self.assertEqual(
                    unsupported_visible_citation.json()["detail"]["code"],
                    "report-draft-citation-invalid",
                )
        stale_save = self.client.put(
            save_url,
            headers={"Idempotency-Key": "report-draft-save-stale-0001"},
            json={**save_payload, "contentMarkdown": f"{saved_content}\nStale edit.\n"},
        )
        self.assertEqual(stale_save.status_code, 409, stale_save.text)
        self.assertEqual(stale_save.json()["detail"]["code"], "report-draft-conflict")

        with self.session_factory() as session:
            session.add(
                ProjectRecord(
                    id="project-2",
                    title="Other project",
                    description="",
                    project_path=str(self.root / "project-2"),
                    execution_mode="safe",
                )
            )
            session.commit()
        foreign = self.client.get(
            f"/v1/projects/project-2/workflows/{workflow_id}/report-draft"
        )
        self.assertEqual(foreign.status_code, 404, foreign.text)

        self.client.close()
        self.client = self._new_client()
        restarted = self.client.get(draft_url)
        self.assertEqual(restarted.status_code, 200, restarted.text)
        self.assertEqual(restarted.json()["contentMarkdown"], saved_content)
        self.assertEqual(restarted.json()["contentSha256"], saved["contentSha256"])

        source_path = self.root / "source-1.pdf"
        original_source_bytes = source_path.read_bytes()
        source_path.write_bytes(original_source_bytes + b"-tampered")
        export_url = f"{save_url}/export"
        stale_export = self.client.post(
            export_url,
            json={
                "expectedRevision": saved["revision"],
                "expectedContentSha256": saved["contentSha256"],
            },
        )
        self.assertEqual(stale_export.status_code, 409, stale_export.text)
        self.assertEqual(stale_export.json()["detail"]["code"], "report-draft-base-stale")
        blocked = self.client.get(draft_url).json()
        self.assertEqual(blocked["status"], "needs-review")
        self.assertEqual(blocked["contentMarkdown"], saved_content)
        self.assertEqual(blocked["contentSha256"], saved["contentSha256"])

        source_path.write_bytes(original_source_bytes)
        reviewed_response = self.client.post(
            f"{save_url}/review",
            headers={"Idempotency-Key": "report-draft-review-0001"},
            json={
                "expectedRevision": blocked["revision"],
                "expectedContentSha256": blocked["contentSha256"],
            },
        )
        self.assertEqual(reviewed_response.status_code, 200, reviewed_response.text)
        reviewed = reviewed_response.json()
        self.assertEqual(reviewed["status"], "reviewed")
        review_replay = self.client.post(
            f"{save_url}/review",
            headers={"Idempotency-Key": "report-draft-review-0001"},
            json={
                "expectedRevision": blocked["revision"],
                "expectedContentSha256": blocked["contentSha256"],
            },
        )
        self.assertEqual(review_replay.status_code, 200, review_replay.text)
        self.assertEqual(review_replay.json(), reviewed)
        stale_save_replay = self.client.put(
            save_url,
            headers={"Idempotency-Key": "report-draft-save-0001"},
            json=save_payload,
        )
        self.assertEqual(stale_save_replay.status_code, 409, stale_save_replay.text)
        self.assertEqual(
            stale_save_replay.json()["detail"]["code"],
            "report-draft-idempotency-stale",
        )
        exported_response = self.client.post(
            export_url,
            json={
                "expectedRevision": reviewed["revision"],
                "expectedContentSha256": reviewed["contentSha256"],
            },
        )
        self.assertEqual(exported_response.status_code, 200, exported_response.text)
        self.assertEqual(exported_response.json()["contentMarkdown"], saved_content)

        with self.session_factory() as session:
            evidence = session.scalar(
                select(EvidenceSpanRecord).where(EvidenceSpanRecord.source_id == "source-1")
            )
            assert evidence is not None
            original_evidence_text = evidence.text
            evidence.text = f"{original_evidence_text} tampered"
            session.commit()
        evidence_stale = self.client.post(
            export_url,
            json={
                "expectedRevision": reviewed["revision"],
                "expectedContentSha256": reviewed["contentSha256"],
            },
        )
        self.assertEqual(evidence_stale.status_code, 409, evidence_stale.text)
        self.assertEqual(evidence_stale.json()["detail"]["code"], "report-draft-base-stale")
        evidence_blocked = self.client.get(draft_url).json()
        self.assertEqual(evidence_blocked["status"], "needs-review")
        stale_review_replay = self.client.post(
            f"{save_url}/review",
            headers={"Idempotency-Key": "report-draft-review-0001"},
            json={
                "expectedRevision": blocked["revision"],
                "expectedContentSha256": blocked["contentSha256"],
            },
        )
        self.assertEqual(stale_review_replay.status_code, 409, stale_review_replay.text)
        self.assertEqual(
            stale_review_replay.json()["detail"]["code"],
            "report-draft-idempotency-stale",
        )
        with self.session_factory() as session:
            evidence = session.scalar(
                select(EvidenceSpanRecord).where(EvidenceSpanRecord.source_id == "source-1")
            )
            assert evidence is not None
            evidence.text = original_evidence_text
            session.commit()
        evidence_rebased = self.client.post(
            f"{save_url}/review",
            headers={"Idempotency-Key": "report-draft-review-0002"},
            json={
                "expectedRevision": evidence_blocked["revision"],
                "expectedContentSha256": evidence_blocked["contentSha256"],
            },
        )
        self.assertEqual(evidence_rebased.status_code, 200, evidence_rebased.text)
        current = evidence_rebased.json()
        self.assertEqual(current["status"], "reviewed")

        source_path.write_bytes(original_source_bytes + b"-review-stale")
        stale_review = self.client.post(
            f"{save_url}/review",
            headers={"Idempotency-Key": "report-draft-review-stale-0001"},
            json={
                "expectedRevision": current["revision"],
                "expectedContentSha256": current["contentSha256"],
            },
        )
        self.assertEqual(stale_review.status_code, 409, stale_review.text)
        self.assertEqual(
            stale_review.json()["detail"]["code"],
            "report-draft-base-stale",
        )
        review_blocked = self.client.get(draft_url).json()
        self.assertEqual(review_blocked["status"], "needs-review")
        self.assertEqual(review_blocked["revision"], current["revision"] + 1)
        source_path.write_bytes(original_source_bytes)
        recovered_review = self.client.post(
            f"{save_url}/review",
            headers={"Idempotency-Key": "report-draft-review-recover-0001"},
            json={
                "expectedRevision": review_blocked["revision"],
                "expectedContentSha256": review_blocked["contentSha256"],
            },
        )
        self.assertEqual(recovered_review.status_code, 200, recovered_review.text)
        current = recovered_review.json()
        self.assertEqual(current["status"], "reviewed")

        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            assert workflow is not None
            old_base = _authoritative_base(session, workflow)
        replacements: dict[str, Any] = {}
        citation_rebases: list[dict[str, str]] = []
        new_claims: list[WorkflowClaimOut] = []
        for claim in old_base.result.claims:
            new_evidence: list[EvidenceRelationshipOut] = []
            for evidence in claim.evidence:
                old_token = f"{evidence.evidence_id}:{evidence.quote_hash}"
                replacement = replacements.get(old_token)
                if replacement is None:
                    replacement_text = f"{evidence.text} Authoritative replacement."
                    replacement = evidence.model_copy(
                        update={
                            "evidence_id": f"rebased-evidence-{len(replacements) + 1}",
                            "text": replacement_text,
                            "quote_hash": hashlib.sha256(
                                replacement_text.encode("utf-8")
                            ).hexdigest(),
                        }
                    )
                    replacements[old_token] = replacement
                    citation_rebases.append(
                        {
                            "previousEvidenceId": evidence.evidence_id,
                            "previousQuoteHash": evidence.quote_hash,
                            "currentEvidenceId": replacement.evidence_id,
                            "currentQuoteHash": replacement.quote_hash,
                        }
                    )
                new_evidence.append(replacement)
            new_claims.append(claim.model_copy(update={"evidence": new_evidence}))
        new_result = old_base.result.model_copy(update={"claims": new_claims})
        new_result_sha256 = content_sha256(
            new_result.model_dump(mode="json", by_alias=True, exclude_none=False)
        )
        new_base = report_draft_service.ReportBase(
            workflow_sha256=content_sha256(
                {
                    "previousWorkflowSha256": old_base.workflow_sha256,
                    "resultSha256": new_result_sha256,
                }
            ),
            result_sha256=new_result_sha256,
            evidence_sha256=content_sha256(
                _evidence_snapshot(new_result)
            ),
            result=new_result,
        )
        with patch.object(
            report_draft_service,
            "_authoritative_base",
            return_value=new_base,
        ):
            authoritative_changed = self.client.get(draft_url).json()
            self.assertEqual(authoritative_changed["status"], "needs-review")
            gated_export = self.client.post(
                export_url,
                json={
                    "expectedRevision": authoritative_changed["revision"],
                    "expectedContentSha256": authoritative_changed["contentSha256"],
                },
            )
            self.assertEqual(gated_export.status_code, 409, gated_export.text)
            self.assertEqual(
                gated_export.json()["detail"]["code"],
                "report-draft-needs-review",
            )
            missing_rebase = self.client.post(
                f"{save_url}/review",
                headers={"Idempotency-Key": "report-draft-review-new-base-missing-0001"},
                json={
                    "expectedRevision": authoritative_changed["revision"],
                    "expectedContentSha256": authoritative_changed["contentSha256"],
                },
            )
            self.assertEqual(missing_rebase.status_code, 409, missing_rebase.text)
            self.assertEqual(
                missing_rebase.json()["detail"]["code"],
                "report-draft-rebase-required",
            )
            persisted_block = self.client.get(draft_url).json()
            self.assertEqual(persisted_block["status"], "needs-review")
            new_evidence_review = self.client.post(
                f"{save_url}/review",
                headers={"Idempotency-Key": "report-draft-review-new-base-0001"},
                json={
                    "expectedRevision": persisted_block["revision"],
                    "expectedContentSha256": persisted_block["contentSha256"],
                    "citationRebases": citation_rebases,
                },
            )
            self.assertEqual(
                new_evidence_review.status_code,
                200,
                new_evidence_review.text,
            )
            rebased = new_evidence_review.json()
            self.assertEqual(rebased["status"], "reviewed")
            self.assertIn("Researcher note.", rebased["contentMarkdown"])
            for item in citation_rebases:
                self.assertNotIn(
                    f"[@evidence:{item['previousEvidenceId']}:"
                    f"{item['previousQuoteHash']}]",
                    rebased["contentMarkdown"],
                )
                self.assertIn(
                    f"[@evidence:{item['currentEvidenceId']}:"
                    f"{item['currentQuoteHash']}]",
                    rebased["contentMarkdown"],
                )
            rebased_export = self.client.post(
                export_url,
                json={
                    "expectedRevision": rebased["revision"],
                    "expectedContentSha256": rebased["contentSha256"],
                },
            )
            self.assertEqual(rebased_export.status_code, 200, rebased_export.text)
            self.assertEqual(
                rebased_export.json()["contentMarkdown"],
                rebased["contentMarkdown"],
            )

        with self.session_factory() as session:
            stored = session.scalar(
                select(ReportDraftRecord).where(
                    ReportDraftRecord.workflow_id == workflow_id
                )
            )
            assert stored is not None
            self.assertIn("Researcher note.", stored.content_markdown)

    def test_local_claim_preserves_complete_sentence_above_800_characters(self) -> None:
        long_passage = (
            "Brain computer interfaces improve communication using verified neural signals "
            + "across carefully documented participant sessions " * 18
            + "without adding an unsupported causal interpretation."
        )
        self.assertGreater(len(long_passage), 800)
        self.assertLessEqual(len(long_passage), 1_200)
        self._add_ready_source(long_passage)
        started = self._start(key="long-extractive-sentence-0001")
        planned = self._plan(started["workflow"]["id"])
        self._approve(planned)
        for _ in range(4):
            self.assertTrue(self._run_once())
        final = self.client.get(
            f"/v1/workflows/{started['workflow']['id']}"
        ).json()
        self.assertEqual(final["workflow"]["status"], "completed")
        self.assertEqual(final["result"]["claims"][0]["statement"], long_passage)
        self.assertEqual(final["latestReview"]["verdict"], "passed")

    def test_legacy_synthesis_enqueues_and_retries_legacy_review_to_completion(self) -> None:
        self._add_ready_source()
        workflow_id = self._prepare_legacy_workflow(
            "legacy-synthesis-review-0001"
        )
        self._set_queued_job_handler(
            workflow_id,
            kind="execute-task",
            handler_version="local-literature-v1",
        )
        self.assertTrue(self._run_once())
        with self.session_factory() as session:
            inspect_task = session.scalar(
                select(TaskRecord).where(
                    TaskRecord.workflow_id == workflow_id,
                    TaskRecord.task_type == "inspect-sources",
                )
            )
            assert inspect_task is not None
            inspect_task.outputs = {
                key: value
                for key, value in inspect_task.outputs.items()
                if key not in {"sourceDescriptors", "sourcePageManifestHashes"}
            }
            session.commit()
        self._set_queued_job_handler(
            workflow_id,
            kind="execute-task",
            handler_version="local-literature-v1",
        )
        self.assertTrue(self._run_once())
        with self.session_factory() as session:
            inspect_task = session.scalar(
                select(TaskRecord).where(
                    TaskRecord.workflow_id == workflow_id,
                    TaskRecord.task_type == "inspect-sources",
                )
            )
            evidence_task = session.scalar(
                select(TaskRecord).where(
                    TaskRecord.workflow_id == workflow_id,
                    TaskRecord.task_type == "extract-local-evidence",
                )
            )
            assert inspect_task is not None
            assert evidence_task is not None
            inspect_task.outputs = {
                key: value
                for key, value in inspect_task.outputs.items()
                if key not in {"sourceDescriptors", "sourcePageManifestHashes"}
            }
            evidence_task.outputs = {
                key: value
                for key, value in evidence_task.outputs.items()
                if key != "evidenceFingerprints"
            }
            session.commit()
        self._set_queued_job_handler(
            workflow_id,
            kind="execute-task",
            handler_version="local-literature-v1",
        )
        self.assertTrue(self._run_once())

        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            answer = session.scalar(
                select(AnswerRecord).where(AnswerRecord.workflow_id == workflow_id)
            )
            review_job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "review-workflow",
                    JobRecord.status == "queued",
                )
            )
            assert workflow is not None
            assert answer is not None
            assert review_job is not None
            self.assertEqual(workflow.status, "reviewing")
            self.assertTrue(answer.answer.startswith("Evidence map:"))
            self.assertEqual(answer.metadata_json, {})
            self.assertIsNone(answer.prompt_version)
            self.assertEqual(review_job.handler_version, "deterministic-claims-v1")
            evidence_task = session.scalar(
                select(TaskRecord).where(
                    TaskRecord.workflow_id == workflow_id,
                    TaskRecord.task_type == "extract-local-evidence",
                )
            )
            assert evidence_task is not None
            self.assertIn("evidenceFingerprints", evidence_task.outputs)
            inspect_task = session.scalar(
                select(TaskRecord).where(
                    TaskRecord.workflow_id == workflow_id,
                    TaskRecord.task_type == "inspect-sources",
                )
            )
            assert inspect_task is not None
            inspect_task.outputs = {
                key: value
                for key, value in inspect_task.outputs.items()
                if key not in {"sourceDescriptors", "sourcePageManifestHashes"}
            }
            evidence_task.outputs = {
                key: value
                for key, value in evidence_task.outputs.items()
                if key != "evidenceFingerprints"
            }
            session.commit()

        with patch.object(
            workflow_handlers,
            "handle_review",
            side_effect=WorkflowFailure(
                "legacy-review-transient",
                "The legacy deterministic review should retry.",
                retryable=True,
            ),
        ):
            self.assertTrue(self._run_once())
        with self.session_factory() as session:
            retry_job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "review-workflow",
                    JobRecord.status == "queued",
                )
            )
            assert retry_job is not None
            self.assertEqual(retry_job.handler_version, "deterministic-claims-v1")
            retry_job.available_at = utc_now()
            session.commit()
        self.assertTrue(self._run_once())
        final = self.client.get(f"/v1/workflows/{workflow_id}").json()
        self.assertEqual(final["workflow"]["status"], "completed")
        self.assertEqual(final["latestReview"]["reviewType"], "deterministic-claims-v1")
        self.assertEqual(final["latestReview"]["verdict"], "passed")
        self.assertEqual(final["result"]["generator"], "local-extractive-v1")
        self.assertIsNone(final["result"]["promptVersion"])
        self.assertEqual(final["result"]["integrityStatus"], "unfrozen")
        with self.session_factory() as session:
            review = session.scalar(
                select(ReviewRecord).where(ReviewRecord.workflow_id == workflow_id)
            )
            assert review is not None
            self.assertNotIn("schemaVersion", review.result_json)
            self.assertNotIn("resultSnapshot", review.result_json)
            self.assertNotIn("resultSnapshotSha256", review.result_json)

    def test_blocked_legacy_inspect_resume_upgrades_before_any_answer_is_published(self) -> None:
        started = self._start(key="legacy-blocked-inspect-0001")
        workflow_id = started["workflow"]["id"]
        planned = self._plan(workflow_id)
        self._approve(planned)
        self._set_queued_job_handler(
            workflow_id,
            kind="execute-task",
            handler_version="local-literature-v1",
        )
        self.assertTrue(self._run_once())
        blocked = self.client.get(f"/v1/workflows/{workflow_id}").json()
        self.assertEqual(blocked["workflow"]["status"], "blocked")
        self.assertIsNone(blocked["result"])

        self._add_ready_source()
        resumed_response = self.client.post(
            f"/v1/workflows/{workflow_id}/resume",
            headers={"Idempotency-Key": "legacy-inspect-resume-0001"},
            json={
                "expectedWorkflowRevision": blocked["workflow"]["revision"],
            },
        )
        self.assertEqual(resumed_response.status_code, 202, resumed_response.text)
        with self.session_factory() as session:
            resumed_job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.status == "queued",
                )
            )
            assert resumed_job is not None
            self.assertEqual(resumed_job.handler_version, "literature-synthesis-v2")
        for _ in range(4):
            self.assertTrue(self._run_once())
        final = self.client.get(f"/v1/workflows/{workflow_id}").json()
        self.assertEqual(final["workflow"]["status"], "completed")
        self.assertEqual(final["latestReview"]["reviewType"], "deterministic-claims-v2")

    def test_remote_mode_requires_explicit_approval_before_enqueue(self) -> None:
        response = self.client.post(
            "/v1/projects/project-1/workflows",
            headers={"Idempotency-Key": "remote-without-approval-0001"},
            json={
                "workflowType": "literature-synthesis",
                "goal": GOAL,
                "generationMode": "remote-model-assisted",
                "remoteDataApproved": False,
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        for index, coerced_value in enumerate(("true", "yes", 1), start=1):
            with self.subTest(remote_data_approved=coerced_value):
                coerced = self.client.post(
                    "/v1/projects/project-1/workflows",
                    headers={
                        "Idempotency-Key": f"remote-coerced-approval-000{index}"
                    },
                    json={
                        "workflowType": "literature-synthesis",
                        "goal": GOAL,
                        "generationMode": "remote-model-assisted",
                        "remoteDataApproved": coerced_value,
                    },
                )
                self.assertEqual(coerced.status_code, 422, coerced.text)
        with self.session_factory() as session:
            self.assertIsNone(session.scalar(select(WorkflowRecord)))

    def test_remote_create_idempotency_replays_after_gateway_configuration_changes(self) -> None:
        gateway = FakeModelGateway()
        with patch.object(workflow_service, "model_gateway", gateway):
            first = self._start_remote(key="remote-idempotency-replay-0001")
            gateway.configured = False
            replayed = self._start_remote(key="remote-idempotency-replay-0001")
        self.assertEqual(first["workflow"]["id"], replayed["workflow"]["id"])

    def test_remote_plan_and_synthesis_are_approved_frozen_and_reviewed(self) -> None:
        self._add_ready_source()
        gateway = FakeModelGateway()
        with (
            patch.object(workflow_service, "model_gateway", gateway),
            patch.object(workflow_handlers, "model_gateway", gateway),
        ):
            started = self._start_remote()
            workflow_id = started["workflow"]["id"]
            self.assertEqual(
                started["workflow"]["generationMode"],
                "remote-model-assisted",
            )
            self.assertEqual(started["eventCursor"], 2)

            planned = self._plan(workflow_id)
            self.assertEqual(planned["plan"]["generator"], "remote-model-assisted-v1")
            self.assertEqual(planned["plan"]["model"], gateway.default_model)
            self.assertEqual(planned["plan"]["promptVersion"], "remote-plan-v1")
            inspect_inputs = planned["plan"]["spec"]["steps"][0]["inputs"]
            self.assertIsNone(inspect_inputs["sourceIds"])
            frozen_source = inspect_inputs["frozenSources"][0]
            self.assertEqual(frozen_source["sourceId"], "source-1")
            self.assertEqual(frozen_source["title"], "Local paper source-1")
            self.assertEqual(len(frozen_source["contentHash"]), 64)
            self.assertEqual(len(frozen_source["pageManifestHash"]), 64)
            approval = planned["pendingApprovals"][0]
            self.assertEqual(approval["riskLevel"], "medium")
            self.assertEqual(
                approval["affectedResources"],
                [
                    "project:project-1",
                    f"remote-endpoint-host:{gateway.endpoint_host}",
                    f"remote-endpoint-identity:{gateway.endpoint_identity}",
                    f"remote-model:{gateway.default_model}",
                    f"source:source-1:sha256:{frozen_source['contentHash']}:"
                    "verified-passages:remote",
                ],
            )
            self.assertIn("selected-source-passages", approval["reason"])

            audit_events = self.client.get(
                f"/v1/workflows/{workflow_id}/events?after=0&limit=20"
            ).json()["events"]
            remote_event = next(
                event for event in audit_events if event["type"] == "remote-data.approved"
            )
            self.assertEqual(
                remote_event["data"],
                {
                    "provider": "openai-compatible",
                    "endpointHost": gateway.endpoint_host,
                    "endpointIdentity": gateway.endpoint_identity,
                    "model": gateway.default_model,
                    "dataCategories": ["user-goal"],
                },
            )
            self.assertNotIn(GOAL, json.dumps(audit_events))

            self._approve(planned)
            for _ in range(4):
                self.assertTrue(self._run_once())
            final = self.client.get(f"/v1/workflows/{workflow_id}").json()
            self.assertEqual(final["workflow"]["status"], "completed")
            self.assertEqual(final["latestReview"]["verdict"], "passed")
            self.assertEqual(
                final["latestReview"]["reviewType"],
                "deterministic-claims-v2",
            )
            self.assertIn(PASSAGE, final["result"]["summary"])
            self.assertNotIn("broader semantic synthesis", final["result"]["summary"])
            self.assertEqual(final["result"]["generator"], "remote-model-assisted-v1")
            self.assertEqual(final["result"]["model"], gateway.default_model)
            self.assertEqual(
                final["result"]["promptVersion"],
                "remote-extractive-synthesis-v1",
            )
            self.assertEqual(
                final["result"]["unresolvedQuestions"],
                ["Which populations require additional communication studies?"],
            )
            self.assertEqual(len(gateway.calls), 2)
            synthesis_payload = gateway.calls[1]["payload"]
            self.assertEqual(synthesis_payload["evidence"][0]["passage"], PASSAGE)
            self.assertNotIn("goal", synthesis_payload)
            self.assertNotIn(GOAL, json.dumps(synthesis_payload))

            with self.session_factory() as session:
                answer = session.scalar(
                    select(AnswerRecord).where(AnswerRecord.workflow_id == workflow_id)
                )
                assert answer is not None
                self.assertEqual(answer.generator, "remote-model-assisted-v1")
                self.assertEqual(answer.model, gateway.default_model)
                self.assertEqual(answer.prompt_version, "remote-extractive-synthesis-v1")
                self.assertEqual(
                    answer.metadata_json["endpointHost"], gateway.endpoint_host
                )
                self.assertEqual(
                    answer.metadata_json["endpointIdentity"], gateway.endpoint_identity
                )

    def test_remote_task_rejects_rehashed_input_that_differs_from_approved_plan(self) -> None:
        self._add_ready_source()
        gateway = FakeModelGateway()
        with (
            patch.object(workflow_service, "model_gateway", gateway),
            patch.object(workflow_handlers, "model_gateway", gateway),
        ):
            started = self._start_remote(key="remote-task-tamper-0001")
            workflow_id = started["workflow"]["id"]
            planned = self._plan(workflow_id)
            self._approve(planned)
            self.assertTrue(self._run_once())
            with self.session_factory() as session:
                workflow = session.get(WorkflowRecord, workflow_id)
                task = session.scalar(
                    select(TaskRecord).where(
                        TaskRecord.workflow_id == workflow_id,
                        TaskRecord.task_type == "extract-local-evidence",
                    )
                )
                assert workflow is not None
                assert task is not None
                job = session.scalar(
                    select(JobRecord).where(
                        JobRecord.task_id == task.id,
                        JobRecord.status == "queued",
                    )
                )
                assert job is not None
                task.inputs = {**task.inputs, "query": "Unapproved quasar evidence"}
                task.input_sha256 = task_input_hash(task)
                job.input_sha256 = current_job_input_hash(
                    session,
                    workflow,
                    kind="execute-task",
                    task=task,
                )
                session.commit()

            self.assertTrue(self._run_once())
            self.assertEqual(len(gateway.calls), 1)
            with self.session_factory() as session:
                failed_job = session.scalar(
                    select(JobRecord).where(
                        JobRecord.workflow_id == workflow_id,
                        JobRecord.status == "failed",
                        JobRecord.error_code == "task-plan-mismatch",
                    )
                )
                assert failed_job is not None
                self.assertIsNone(
                    session.scalar(
                        select(AnswerRecord).where(
                            AnswerRecord.workflow_id == workflow_id
                        )
                    )
                )

    def test_remote_plan_rejects_source_substitution_before_inspection(self) -> None:
        self._add_ready_source()
        gateway = FakeModelGateway()
        with (
            patch.object(workflow_service, "model_gateway", gateway),
            patch.object(workflow_handlers, "model_gateway", gateway),
        ):
            started = self._start_remote(key="remote-source-substitution-0001")
            workflow_id = started["workflow"]["id"]
            planned = self._plan(workflow_id)
            self._approve(planned)
            replacement = (
                "Quasar observations replace the approved local source with different "
                "scientific content."
            )
            source_path = self.root / "source-1.pdf"
            source_path.write_bytes(b"%PDF-substituted-after-approval")
            with self.session_factory() as session:
                source = session.get(SourceRecord, "source-1")
                page = session.scalar(
                    select(SourcePageRecord).where(
                        SourcePageRecord.source_id == "source-1"
                    )
                )
                assert source is not None
                assert page is not None
                source.title = "Substituted paper"
                source.content_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
                page.text = replacement
                page.words = []
                session.commit()

            self.assertTrue(self._run_once())
            self.assertEqual(len(gateway.calls), 1)
            with self.session_factory() as session:
                failed_job = session.scalar(
                    select(JobRecord).where(
                        JobRecord.workflow_id == workflow_id,
                        JobRecord.error_code == "source-reproducibility-failed",
                    )
                )
                assert failed_job is not None
                self.assertIsNone(
                    session.scalar(
                        select(AnswerRecord).where(
                            AnswerRecord.workflow_id == workflow_id
                        )
                    )
                )

    def test_remote_extract_rejects_symlink_substitution_after_inspection(self) -> None:
        self._add_ready_source()
        gateway = FakeModelGateway()
        outside_path = self.root.parent / f"{self.root.name}-outside.pdf"
        outside_path.write_bytes(b"%PDF-outside-approved-workspace")
        try:
            with (
                patch.object(workflow_service, "model_gateway", gateway),
                patch.object(workflow_handlers, "model_gateway", gateway),
            ):
                started = self._start_remote(key="remote-source-symlink-0001")
                workflow_id = started["workflow"]["id"]
                planned = self._plan(workflow_id)
                self._approve(planned)
                self.assertTrue(self._run_once())
                source_path = self.root / "source-1.pdf"
                source_path.unlink()
                source_path.symlink_to(outside_path)

                self.assertTrue(self._run_once())
                self.assertEqual(len(gateway.calls), 1)
                with self.session_factory() as session:
                    failed_job = session.scalar(
                        select(JobRecord).where(
                            JobRecord.workflow_id == workflow_id,
                            JobRecord.error_code == "source-reproducibility-failed",
                        )
                    )
                    assert failed_job is not None
                    self.assertIsNone(
                        session.scalar(
                            select(AnswerRecord).where(
                                AnswerRecord.workflow_id == workflow_id
                            )
                        )
                    )
        finally:
            outside_path.unlink(missing_ok=True)

    def test_remote_synthesis_rejects_tampered_plan_provenance_before_model_call(self) -> None:
        self._add_ready_source()
        gateway = FakeModelGateway()
        with (
            patch.object(workflow_service, "model_gateway", gateway),
            patch.object(workflow_handlers, "model_gateway", gateway),
        ):
            started = self._start_remote(key="remote-plan-provenance-tamper-0001")
            workflow_id = started["workflow"]["id"]
            planned = self._plan(workflow_id)
            self._approve(planned)
            self.assertTrue(self._run_once())
            self.assertTrue(self._run_once())
            with self.session_factory() as session:
                plan = session.get(PlanRecord, planned["plan"]["id"])
                assert plan is not None
                plan.prompt_version = "tampered-remote-plan-prompt"
                session.commit()

            self.assertTrue(self._run_once())
            self.assertEqual(len(gateway.calls), 1)
            with self.session_factory() as session:
                failed_job = session.scalar(
                    select(JobRecord).where(
                        JobRecord.workflow_id == workflow_id,
                        JobRecord.status == "failed",
                        JobRecord.error_code == "job-input-changed",
                    )
                )
                assert failed_job is not None
                self.assertIsNone(
                    session.scalar(
                        select(AnswerRecord).where(
                            AnswerRecord.workflow_id == workflow_id
                        )
                    )
                )

    def test_invalid_remote_plan_never_falls_back_to_template(self) -> None:
        self._add_ready_source()
        gateway = FakeModelGateway(invalid_plan=True)
        with (
            patch.object(workflow_service, "model_gateway", gateway),
            patch.object(workflow_handlers, "model_gateway", gateway),
        ):
            started = self._start_remote(key="remote-invalid-plan-0001")
            self.assertTrue(self._run_once())
            workflow_id = started["workflow"]["id"]
            with self.session_factory() as session:
                self.assertIsNone(
                    session.scalar(
                        select(PlanRecord).where(PlanRecord.workflow_id == workflow_id)
                    )
                )
                failed_job = session.scalar(
                    select(JobRecord).where(
                        JobRecord.workflow_id == workflow_id,
                        JobRecord.status == "failed",
                        JobRecord.error_code == "model-plan-invalid",
                    )
                )
                assert failed_job is not None
                retry_job = session.scalar(
                    select(JobRecord).where(
                        JobRecord.workflow_id == workflow_id,
                        JobRecord.status == "queued",
                        JobRecord.previous_job_id == failed_job.id,
                    )
                )
                assert retry_job is not None
                workflow = session.get(WorkflowRecord, workflow_id)
                assert workflow is not None
                self.assertEqual(
                    workflow.status,
                    "planning",
                )

    def test_result_claims_preserve_retrieved_evidence_order(self) -> None:
        second_passage = (
            "Brain computer interfaces improve communication through a second verified "
            "neural signal protocol."
        )
        self._add_ready_source()
        self._add_ready_source(second_passage, source_id="source-2")
        gateway = FakeModelGateway(use_all_evidence=True)
        with (
            patch.object(workflow_service, "model_gateway", gateway),
            patch.object(workflow_handlers, "model_gateway", gateway),
        ):
            started = self._start_remote(key="remote-claim-order-0001")
            planned = self._plan(started["workflow"]["id"])
            self._approve(planned)
            for _ in range(4):
                self.assertTrue(self._run_once())
            final = self.client.get(
                f"/v1/workflows/{started['workflow']['id']}"
            ).json()
            synthesis_evidence = gateway.calls[1]["payload"]["evidence"]
            expected_evidence_order = [
                item["evidenceId"] for item in synthesis_evidence
            ]
            result_evidence_order = [
                claim["evidence"][0]["evidenceId"]
                for claim in final["result"]["claims"]
            ]
            self.assertEqual(result_evidence_order, expected_evidence_order)
            for index, claim in enumerate(final["result"]["claims"], start=1):
                self.assertIn(
                    f"{index}. {claim['statement']}",
                    final["result"]["summary"],
                )

    def test_remote_plan_fails_closed_on_same_host_endpoint_path_drift(self) -> None:
        self._add_ready_source()
        gateway = FakeModelGateway()
        with (
            patch.object(workflow_service, "model_gateway", gateway),
            patch.object(workflow_handlers, "model_gateway", gateway),
        ):
            started = self._start_remote(key="remote-plan-endpoint-drift-0001")
            original_host = gateway.endpoint_host
            gateway.endpoint_path = "/compatible/v2/chat/completions"
            self.assertEqual(gateway.endpoint_host, original_host)
            self.assertTrue(self._run_once())

            workflow_id = started["workflow"]["id"]
            self.assertEqual(len(gateway.calls), 0)
            with self.session_factory() as session:
                self.assertIsNone(
                    session.scalar(
                        select(PlanRecord).where(PlanRecord.workflow_id == workflow_id)
                    )
                )
                failed_job = session.scalar(
                    select(JobRecord).where(
                        JobRecord.workflow_id == workflow_id,
                        JobRecord.status == "failed",
                    )
                )
                assert failed_job is not None
                workflow = session.get(WorkflowRecord, workflow_id)
                assert workflow is not None
                self.assertEqual(
                    failed_job.error_code,
                    "remote-gateway-approval-mismatch",
                )
                self.assertEqual(
                    workflow.status,
                    "failed",
                )

    def test_remote_synthesis_fails_closed_on_same_host_endpoint_path_drift(self) -> None:
        self._add_ready_source()
        gateway = FakeModelGateway()
        with (
            patch.object(workflow_service, "model_gateway", gateway),
            patch.object(workflow_handlers, "model_gateway", gateway),
        ):
            started = self._start_remote(key="remote-gateway-drift-0001")
            planned = self._plan(started["workflow"]["id"])
            self._approve(planned)
            self.assertTrue(self._run_once())
            self.assertTrue(self._run_once())
            original_host = gateway.endpoint_host
            gateway.endpoint_path = "/compatible/v2/chat/completions"
            self.assertEqual(gateway.endpoint_host, original_host)
            self.assertTrue(self._run_once())

            workflow_id = started["workflow"]["id"]
            final = self.client.get(f"/v1/workflows/{workflow_id}").json()
            self.assertEqual(final["workflow"]["status"], "failed")
            self.assertEqual(len(gateway.calls), 1)
            with self.session_factory() as session:
                failed_job = session.scalar(
                    select(JobRecord).where(
                        JobRecord.workflow_id == workflow_id,
                        JobRecord.status == "failed",
                        JobRecord.kind == "execute-task",
                    )
                )
                assert failed_job is not None
                workflow = session.get(WorkflowRecord, workflow_id)
                assert workflow is not None
                self.assertEqual(
                    failed_job.error_code,
                    "remote-gateway-approval-mismatch",
                )
                self.assertEqual(
                    workflow.status,
                    "failed",
                )
                self.assertIsNone(
                    session.scalar(
                        select(AnswerRecord).where(
                            AnswerRecord.workflow_id == workflow_id
                        )
                    )
                )

    def test_remote_synthesis_rejects_unknown_evidence_without_partial_answer(self) -> None:
        self._add_ready_source()
        gateway = FakeModelGateway(synthesis_evidence_id="unknown-evidence")
        with (
            patch.object(workflow_service, "model_gateway", gateway),
            patch.object(workflow_handlers, "model_gateway", gateway),
        ):
            started = self._start_remote(key="remote-unknown-evidence-0001")
            planned = self._plan(started["workflow"]["id"])
            self._approve(planned)
            for _ in range(3):
                self.assertTrue(self._run_once())

            workflow_id = started["workflow"]["id"]
            with self.session_factory() as session:
                failed_job = session.scalar(
                    select(JobRecord).where(
                        JobRecord.workflow_id == workflow_id,
                        JobRecord.status == "failed",
                        JobRecord.error_code == "model-evidence-reference-invalid",
                    )
                )
                assert failed_job is not None
                self.assertIsNone(
                    session.scalar(
                        select(AnswerRecord).where(
                            AnswerRecord.workflow_id == workflow_id
                        )
                    )
                )
                workflow = session.get(WorkflowRecord, workflow_id)
                assert workflow is not None
                self.assertEqual(workflow.status, "running")
                retry_job = session.scalar(
                    select(JobRecord).where(
                        JobRecord.workflow_id == workflow_id,
                        JobRecord.status == "queued",
                        JobRecord.previous_job_id == failed_job.id,
                    )
                )
                assert retry_job is not None

    def test_no_ready_pdf_resume_reuses_plan_and_blocked_task(self) -> None:
        started = self._start()
        planned = self._plan(started["workflow"]["id"])
        approved = self._approve(planned)
        plan_id = approved["plan"]["id"]
        inspect_task_id = approved["plan"]["steps"][0]["id"]
        self.assertTrue(self._run_once())

        blocked = self.client.get(
            f"/v1/workflows/{started['workflow']['id']}"
        ).json()
        self.assertEqual(blocked["workflow"]["status"], "blocked")
        self.assertEqual(blocked["workflow"]["blockingReason"]["code"], "no-ready-pdf")
        self.assertEqual(blocked["allowedActions"], ["cancel", "resume"])
        self._add_ready_source()

        resumed_response = self.client.post(
            f"/v1/workflows/{started['workflow']['id']}/resume",
            headers={"Idempotency-Key": "resume-workflow-0001"},
            json={"expectedWorkflowRevision": blocked["workflow"]["revision"]},
        )
        self.assertEqual(resumed_response.status_code, 202, resumed_response.text)
        resumed = resumed_response.json()
        self.assertEqual(resumed["workflow"]["status"], "running")
        self.assertEqual(resumed["plan"]["id"], plan_id)
        self.assertEqual(resumed["plan"]["steps"][0]["id"], inspect_task_id)
        self.assertEqual(resumed["plan"]["steps"][0]["status"], "queued")

        for _ in range(4):
            self.assertTrue(self._run_once())
        final = self.client.get(
            f"/v1/workflows/{started['workflow']['id']}"
        ).json()
        self.assertEqual(final["workflow"]["status"], "completed")
        self.assertEqual(final["plan"]["id"], plan_id)

    def test_cancelled_leased_job_converges_and_expired_lease_recovers(self) -> None:
        started = self._start(key="create-cancel-0001")
        workflow_id = started["workflow"]["id"]
        claimed = self.worker.claim_next_job()
        assert claimed is not None
        job_id, _ = claimed
        cancel = self.client.post(
            f"/v1/workflows/{workflow_id}/cancel",
            json={"expectedWorkflowRevision": started["workflow"]["revision"]},
        )
        self.assertEqual(cancel.status_code, 202, cancel.text)
        self.assertEqual(cancel.json()["workflow"]["status"], "planning")
        self.assertIsNotNone(cancel.json()["workflow"]["cancelRequestedAt"])
        with self.session_factory() as session:
            job = session.get(JobRecord, job_id)
            assert job is not None
            job.lease_expires_at = utc_now() - timedelta(seconds=1)
            session.commit()
        self.assertFalse(self._run_once())
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            job = session.get(JobRecord, job_id)
            assert workflow is not None
            assert job is not None
            self.assertEqual(workflow.status, "cancelled")
            self.assertEqual(job.status, "cancelled")
            self.assertIsNone(
                session.scalar(
                    select(JobRecord.id).where(
                        JobRecord.workflow_id == workflow_id,
                        JobRecord.status.in_(["queued", "leased"]),
                    )
                )
            )

        recovery = self._start(key="create-recovery-0001")
        recovery_id = recovery["workflow"]["id"]
        claimed = self.worker.claim_next_job()
        assert claimed is not None
        first_job_id, _ = claimed
        with self.session_factory() as session:
            job = session.get(JobRecord, first_job_id)
            assert job is not None
            job.lease_expires_at = utc_now() - timedelta(seconds=1)
            session.commit()
        restarted_worker = WorkflowWorker(self.session_factory, poll_interval_seconds=0.01)
        restarted_worker.recover()
        with self.session_factory() as session:
            attempts = list(
                session.scalars(
                    select(JobRecord)
                    .where(JobRecord.workflow_id == recovery_id)
                    .order_by(JobRecord.attempt)
                )
            )
            self.assertEqual([(job.attempt, job.status) for job in attempts], [
                (1, "failed"),
                (2, "queued"),
            ])
            self.assertEqual(attempts[0].error_code, "lease-expired")
            attempts[1].available_at = utc_now()
            session.commit()
        self.assertTrue(asyncio.run(restarted_worker.run_once()))
        with self.session_factory() as session:
            recovered_workflow = session.get(WorkflowRecord, recovery_id)
            assert recovered_workflow is not None
            self.assertEqual(
                recovered_workflow.status,
                "waiting-plan-approval",
            )

    def test_reviewer_rejects_non_extractive_tampered_claim(self) -> None:
        self._add_ready_source()
        started = self._start()
        planned = self._plan(started["workflow"]["id"])
        self._approve(planned)
        for _ in range(3):
            self.assertTrue(self._run_once())

        workflow_id = started["workflow"]["id"]
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            assert workflow is not None
            self.assertEqual(workflow.status, "reviewing")
            claim = session.scalar(select(ClaimRecord))
            assert claim is not None
            claim.statement = "A causal conclusion that does not occur in the evidence."
            review_job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "review-workflow",
                )
            )
            assert review_job is not None
            # Establish the tampered material as the review input so this test
            # exercises the reviewer itself, rather than the earlier input-hash
            # guard (which independently rejects post-queue mutation).
            review_job.input_sha256 = current_job_input_hash(
                session,
                workflow,
                kind="review-workflow",
                task=None,
            )
            session.commit()
        self.assertTrue(self._run_once())

        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            review = session.scalar(
                select(ReviewRecord).where(ReviewRecord.workflow_id == workflow_id)
            )
            claim = session.scalar(select(ClaimRecord))
            assert workflow is not None
            assert review is not None
            assert claim is not None
            self.assertEqual(workflow.status, "blocked")
            self.assertEqual(workflow.blocking_code, "review-required")
            self.assertEqual(review.verdict, "revision-required")
            self.assertEqual(claim.review_status, "unreviewed")
            self.assertTrue(review.result_json["requiredRevisions"])

    def test_reviewer_rejects_free_generated_summary_content(self) -> None:
        self._add_ready_source()
        started = self._start(key="tampered-summary-0001")
        planned = self._plan(started["workflow"]["id"])
        self._approve(planned)
        for _ in range(3):
            self.assertTrue(self._run_once())

        workflow_id = started["workflow"]["id"]
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            answer = session.scalar(
                select(AnswerRecord).where(AnswerRecord.workflow_id == workflow_id)
            )
            assert workflow is not None
            assert answer is not None
            answer.answer += " An unsupported generated conclusion."
            review_job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "review-workflow",
                )
            )
            assert review_job is not None
            review_job.input_sha256 = current_job_input_hash(
                session,
                workflow,
                kind="review-workflow",
                task=None,
            )
            session.commit()
        self.assertTrue(self._run_once())

        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            review = session.scalar(
                select(ReviewRecord).where(ReviewRecord.workflow_id == workflow_id)
            )
            assert workflow is not None
            assert review is not None
            summary_check = next(
                check
                for check in review.result_json["checks"]
                if check["code"] == "answer-extractive-summary"
            )
            self.assertEqual(workflow.status, "blocked")
            self.assertEqual(review.verdict, "revision-required")
            self.assertEqual(summary_check["status"], "failed")

    def test_reviewer_rejects_tampered_local_prompt_provenance(self) -> None:
        self._add_ready_source()
        started = self._start(key="tampered-local-provenance-0001")
        planned = self._plan(started["workflow"]["id"])
        self._approve(planned)
        for _ in range(3):
            self.assertTrue(self._run_once())

        workflow_id = started["workflow"]["id"]
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            answer = session.scalar(
                select(AnswerRecord).where(AnswerRecord.workflow_id == workflow_id)
            )
            assert workflow is not None
            assert answer is not None
            answer.prompt_version = "tampered-local-prompt"
            review_job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "review-workflow",
                )
            )
            assert review_job is not None
            review_job.input_sha256 = current_job_input_hash(
                session,
                workflow,
                kind="review-workflow",
                task=None,
            )
            session.commit()
        self.assertTrue(self._run_once())

        with self.session_factory() as session:
            review = session.scalar(
                select(ReviewRecord).where(ReviewRecord.workflow_id == workflow_id)
            )
            assert review is not None
            provenance_check = next(
                check
                for check in review.result_json["checks"]
                if check["code"] == "answer-generation-provenance"
            )
            self.assertEqual(review.verdict, "revision-required")
            self.assertEqual(provenance_check["status"], "failed")

    def test_provisional_claim_is_pending_review(self) -> None:
        self._add_ready_source()
        started = self._start(key="pending-review-result-0001")
        planned = self._plan(started["workflow"]["id"])
        self._approve(planned)
        for _ in range(3):
            self.assertTrue(self._run_once())
        snapshot = self.client.get(
            f"/v1/workflows/{started['workflow']['id']}"
        ).json()
        self.assertEqual(snapshot["workflow"]["status"], "reviewing")
        self.assertEqual(snapshot["result"]["integrityStatus"], "unfrozen")
        self.assertEqual(
            snapshot["result"]["claims"][0]["supportStatus"],
            "pending-review",
        )

    def test_completed_result_rejects_answer_claim_and_evidence_drift(self) -> None:
        for field in ("answer", "claim", "evidence"):
            with self.subTest(field=field):
                self.tearDown()
                self.setUp()
                workflow_id = self._complete_local_workflow(
                    f"completed-{field}-drift-0001"
                )
                with self.session_factory() as session:
                    if field == "answer":
                        answer = session.scalar(
                            select(AnswerRecord).where(
                                AnswerRecord.workflow_id == workflow_id
                            )
                        )
                        assert answer is not None
                        answer.answer += " Tampered after review."
                    elif field == "claim":
                        claim = session.scalar(select(ClaimRecord))
                        assert claim is not None
                        claim.statement = "A substituted claim after review."
                    else:
                        evidence = session.scalar(select(EvidenceSpanRecord))
                        assert evidence is not None
                        evidence.text = "A substituted evidence passage after review."
                        evidence.quote_hash = hashlib.sha256(
                            evidence.text.encode("utf-8")
                        ).hexdigest()
                    session.commit()
                self._assert_result_integrity_conflict(workflow_id)

    def test_completed_result_rejects_source_file_replacement(self) -> None:
        workflow_id = self._complete_local_workflow(
            "completed-source-drift-0001"
        )
        (self.root / "source-1.pdf").write_bytes(b"%PDF-replaced-after-review")
        self._assert_result_integrity_conflict(workflow_id)

    def test_completed_result_rejects_review_verdict_column_drift(self) -> None:
        workflow_id = self._complete_local_workflow(
            "completed-review-column-drift-0001"
        )
        with self.session_factory() as session:
            review = session.scalar(
                select(ReviewRecord).where(ReviewRecord.workflow_id == workflow_id)
            )
            assert review is not None
            review.verdict = "revision-required"
            session.commit()
        self._assert_result_integrity_conflict(workflow_id)

    def test_completed_result_rejects_coherent_review_downgrade_and_answer_drift(
        self,
    ) -> None:
        workflow_id = self._complete_local_workflow(
            "completed-review-answer-drift-0001"
        )
        with self.session_factory() as session:
            review = session.scalar(
                select(ReviewRecord).where(ReviewRecord.workflow_id == workflow_id)
            )
            answer = session.scalar(
                select(AnswerRecord).where(AnswerRecord.workflow_id == workflow_id)
            )
            assert review is not None
            assert answer is not None
            review.verdict = "revision-required"
            review.result_json = {
                **review.result_json,
                "verdict": "revision-required",
            }
            answer.answer += " Tampered after review."
            session.commit()
        self._assert_result_integrity_conflict(workflow_id)

    def test_true_legacy_review_snapshot_remains_readable_but_unfrozen(self) -> None:
        workflow_id = self._complete_legacy_workflow("legacy-review-read-0001")
        response = self.client.get(f"/v1/workflows/{workflow_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["result"]["integrityStatus"], "unfrozen")
        self.assertEqual(
            response.json()["latestReview"]["result"]["schemaVersion"],
            "1",
        )

    def test_completed_result_rejects_v2_review_disguised_as_legacy(self) -> None:
        workflow_id = self._complete_local_workflow("review-schema-downgrade-0001")
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            review = session.scalar(
                select(ReviewRecord).where(ReviewRecord.workflow_id == workflow_id)
            )
            review_job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "review-workflow",
                    JobRecord.status == "succeeded",
                )
            )
            answer = session.scalar(
                select(AnswerRecord).where(AnswerRecord.workflow_id == workflow_id)
            )
            assert workflow is not None
            assert review is not None
            assert review_job is not None
            assert answer is not None
            legacy_result = {
                key: value
                for key, value in review.result_json.items()
                if key not in {
                    "schemaVersion",
                    "resultSnapshot",
                    "resultSnapshotSha256",
                }
            }
            review.review_type = "deterministic-claims-v1"
            review.result_json = legacy_result
            review_job.handler_version = "deterministic-claims-v1"
            review_job.input_sha256 = job_input_hash_for_handler_version(
                session,
                workflow,
                kind="review-workflow",
                task=None,
                handler_version="deterministic-claims-v1",
            )
            review.input_sha256 = review_job.input_sha256
            answer.answer += " Tampered after a forged schema downgrade."
            session.commit()
        self._assert_result_integrity_conflict(workflow_id)

    def test_corrupt_review_and_completed_plan_drift_return_typed_conflicts(self) -> None:
        workflow_id = self._complete_local_workflow("corrupt-review-json-0001")
        with self.session_factory() as session:
            review = session.scalar(
                select(ReviewRecord).where(ReviewRecord.workflow_id == workflow_id)
            )
            assert review is not None
            review.result_json = {"schemaVersion": "invalid"}
            session.commit()
        self._assert_result_integrity_conflict(workflow_id)

        self.tearDown()
        self.setUp()
        workflow_id = self._complete_local_workflow("completed-plan-drift-0001")
        with self.session_factory() as session:
            plan = session.scalar(
                select(PlanRecord).where(PlanRecord.workflow_id == workflow_id)
            )
            assert plan is not None
            plan.generator = "forged-plan-generator"
            session.commit()
        response = self.client.get(f"/v1/workflows/{workflow_id}")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(
            response.json()["detail"]["code"],
            "plan-provenance-invalid",
        )


if __name__ == "__main__":
    unittest.main()
