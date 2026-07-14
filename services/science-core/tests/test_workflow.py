from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from collections.abc import Generator
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from open_science_core.api.workflows import get_workflow_session, router
from open_science_core.db import Base
from open_science_core.models import (
    AnswerRecord,
    ApprovalRecord,
    ClaimRecord,
    EvidenceSpanRecord,
    EventRecord,
    JobRecord,
    PlanRecord,
    ProjectRecord,
    ReviewRecord,
    SourcePageRecord,
    SourceRecord,
    TaskRecord,
    WorkflowRecord,
    utc_now,
)
from open_science_core.workflow import handlers as workflow_handlers
from open_science_core.workflow import service as workflow_service
from open_science_core.workflow.service import (
    current_job_input_hash,
    job_input_hash_for_handler_version,
    task_input_hash,
)
from open_science_core.workflow.state import WorkflowFailure
from open_science_core.workflow.worker import WorkflowWorker


GOAL = "How do brain computer interfaces improve communication?"
PASSAGE = (
    "Brain computer interfaces improve communication for people with severe motor "
    "impairments using verified neural signals."
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
        self.calls: list[dict[str, object]] = []

    @property
    def endpoint_identity(self) -> str:
        endpoint = f"https://{self.endpoint_host}{self.endpoint_path}"
        return f"sha256:{hashlib.sha256(endpoint.encode('utf-8')).hexdigest()}"

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> dict[str, object]:
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

        @event.listens_for(self.engine, "connect")
        def configure_sqlite(dbapi_connection: object, _record: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.worker = WorkflowWorker(
            self.session_factory,
            poll_interval_seconds=0.01,
            lease_seconds=0.1,
            heartbeat_seconds=0.03,
        )
        app = FastAPI()
        app.include_router(router)

        def session_dependency() -> Generator[Session, None, None]:
            with self.session_factory() as session:
                yield session

        app.dependency_overrides[get_workflow_session] = session_dependency
        self.client = TestClient(app)
        self._create_project()

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()
        self._temporary_directory.cleanup()

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

    def _start(self, *, key: str = "create-workflow-0001") -> dict[str, object]:
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
    ) -> dict[str, object]:
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
            self.assertIsNotNone(job)
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

    def _plan(self, workflow_id: str) -> dict[str, object]:
        self.assertTrue(self._run_once())
        response = self.client.get(f"/v1/workflows/{workflow_id}")
        self.assertEqual(response.status_code, 200, response.text)
        snapshot = response.json()
        self.assertEqual(snapshot["workflow"]["status"], "waiting-plan-approval")
        return snapshot

    def _approve(self, snapshot: dict[str, object]) -> dict[str, object]:
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
            self.assertEqual(legacy_job.status, "succeeded")
            self.assertEqual(legacy_job.handler_version, "template-plan-v1")
            approval = session.scalar(
                select(ApprovalRecord).where(
                    ApprovalRecord.workflow_id == workflow_id
                )
            )
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
            self.assertIsNotNone(stored_plan)
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
            self.assertEqual(failed_job.error_code, "plan-content-corrupt")
            self.assertEqual(session.get(WorkflowRecord, workflow_id).status, "failed")
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
    def _plan_record_type() -> type:
        # Local import avoids obscuring the API-facing setup above.
        from open_science_core.models import PlanRecord

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
            self.assertIn("evidenceFingerprints", evidence_task.outputs)
            inspect_task = session.scalar(
                select(TaskRecord).where(
                    TaskRecord.workflow_id == workflow_id,
                    TaskRecord.task_type == "inspect-sources",
                )
            )
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
            "_handle_review",
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
                self.assertIsNotNone(answer)
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
                job = session.scalar(
                    select(JobRecord).where(
                        JobRecord.task_id == task.id,
                        JobRecord.status == "queued",
                    )
                )
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
                self.assertIsNotNone(failed_job)
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
                self.assertIsNotNone(failed_job)
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
                    self.assertIsNotNone(failed_job)
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
                self.assertIsNotNone(failed_job)
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
                self.assertIsNotNone(failed_job)
                retry_job = session.scalar(
                    select(JobRecord).where(
                        JobRecord.workflow_id == workflow_id,
                        JobRecord.status == "queued",
                        JobRecord.previous_job_id == failed_job.id,
                    )
                )
                self.assertIsNotNone(retry_job)
                self.assertEqual(
                    session.get(WorkflowRecord, workflow_id).status,
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
                self.assertEqual(
                    failed_job.error_code,
                    "remote-gateway-approval-mismatch",
                )
                self.assertEqual(
                    session.get(WorkflowRecord, workflow_id).status,
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
                self.assertEqual(
                    failed_job.error_code,
                    "remote-gateway-approval-mismatch",
                )
                self.assertEqual(
                    session.get(WorkflowRecord, workflow_id).status,
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
                self.assertIsNotNone(failed_job)
                self.assertIsNone(
                    session.scalar(
                        select(AnswerRecord).where(
                            AnswerRecord.workflow_id == workflow_id
                        )
                    )
                )
                workflow = session.get(WorkflowRecord, workflow_id)
                self.assertEqual(workflow.status, "running")
                retry_job = session.scalar(
                    select(JobRecord).where(
                        JobRecord.workflow_id == workflow_id,
                        JobRecord.status == "queued",
                        JobRecord.previous_job_id == failed_job.id,
                    )
                )
                self.assertIsNotNone(retry_job)

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
        claimed = self.worker._claim_next_job()
        self.assertIsNotNone(claimed)
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
            job.lease_expires_at = utc_now() - timedelta(seconds=1)
            session.commit()
        self.assertFalse(self._run_once())
        with self.session_factory() as session:
            workflow = session.get(WorkflowRecord, workflow_id)
            job = session.get(JobRecord, job_id)
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
        claimed = self.worker._claim_next_job()
        self.assertIsNotNone(claimed)
        first_job_id, _ = claimed
        with self.session_factory() as session:
            job = session.get(JobRecord, first_job_id)
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
            self.assertEqual(
                session.get(WorkflowRecord, recovery_id).status,
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
            self.assertEqual(workflow.status, "reviewing")
            claim = session.scalar(select(ClaimRecord))
            claim.statement = "A causal conclusion that does not occur in the evidence."
            review_job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "review-workflow",
                )
            )
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
            answer.answer += " An unsupported generated conclusion."
            review_job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "review-workflow",
                )
            )
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
            answer.prompt_version = "tampered-local-prompt"
            review_job = session.scalar(
                select(JobRecord).where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "review-workflow",
                )
            )
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
                        answer.answer += " Tampered after review."
                    elif field == "claim":
                        claim = session.scalar(select(ClaimRecord))
                        claim.statement = "A substituted claim after review."
                    else:
                        evidence = session.scalar(select(EvidenceSpanRecord))
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
