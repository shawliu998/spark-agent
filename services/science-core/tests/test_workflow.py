from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from collections.abc import Generator
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from open_science_core.api.workflows import get_workflow_session, router
from open_science_core.db import Base
from open_science_core.models import (
    ClaimRecord,
    JobRecord,
    ProjectRecord,
    ReviewRecord,
    SourcePageRecord,
    SourceRecord,
    WorkflowRecord,
    utc_now,
)
from open_science_core.workflow.service import current_job_input_hash
from open_science_core.workflow.worker import WorkflowWorker


GOAL = "How do brain computer interfaces improve communication?"
PASSAGE = (
    "Brain computer interfaces improve communication for people with severe motor "
    "impairments using verified neural signals."
)


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

    def _add_ready_source(self) -> None:
        path = self.root / "paper.pdf"
        path.write_bytes(b"%PDF-local-workflow-test")
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
            for index, word in enumerate(PASSAGE.split())
        ]
        with self.session_factory() as session:
            session.add(
                SourceRecord(
                    id="source-1",
                    project_id="project-1",
                    title="Local paper",
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
                    source_id="source-1",
                    page_index=0,
                    page_label="1",
                    width=500.0,
                    height=700.0,
                    text=PASSAGE,
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

    def _run_once(self) -> bool:
        return asyncio.run(self.worker.run_once())

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


if __name__ == "__main__":
    unittest.main()
