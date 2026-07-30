from __future__ import annotations

import copy
import hashlib
import os
from collections.abc import Callable, Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Never, Protocol, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Client, Response
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from open_science_core.api.research_memory import get_research_memory_session
from open_science_core.api.research_memory import router as research_memory_router
from open_science_core.db import Base
from open_science_core.models import (
    AgentContextSnapshotRecord,
    EventRecord,
    EvidenceSpanRecord,
    JobRecord,
    PlanRecord,
    ProjectRecord,
    ResearchMemoryRecord,
    SkillActivationRecord,
    SkillCandidateRecord,
    SourcePageRecord,
    SourceRecord,
    StepObservationRecord,
    TaskRecord,
    WorkflowRecord,
    utc_now,
)
from open_science_core.pdf import PdfPage, locate_quote
from open_science_core.workflow import research_memory as research_memory_service
from open_science_core.workflow import skill_activations as skill_activation_service
from open_science_core.workflow._service.integrity import content_sha256
from open_science_core.workflow.agent_loop.schemas import (
    ObservationFact,
    StepObservation,
    UnresolvedQuestion,
    step_observation_sha256,
)
from open_science_core.workflow.research_memory import (
    create_observation_memory_candidates,
    decision_context_payload,
    get_and_verify_remembered_evidence_episode,
    get_or_create_context_snapshot,
    get_research_memory_workspace,
)
from open_science_core.workflow.research_memory import (
    resolve_memory_candidate as _resolve_memory_candidate,
)
from open_science_core.workflow.skill_candidates import (
    CAPABILITY_ARGUMENT_EXAMPLE,
    CAPABILITY_ARGUMENT_KEYS,
    SKILL_MD,
    remember_verified_evidence_capability,
)
from open_science_core.workflow.state import WorkflowFailure


class _RequestClient(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Response: ...


class TypedTestClient(TestClient):
    def get(self, url: str, **kwargs: Any) -> Response:  # pyright: ignore[reportIncompatibleMethodOverride]
        return cast(_RequestClient, self).request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:  # pyright: ignore[reportIncompatibleMethodOverride]
        return cast(_RequestClient, self).request("POST", url, **kwargs)

    def close(self) -> None:
        Client.close(self)  # pyright: ignore[reportArgumentType]


def _rehash(memory: ResearchMemoryRecord) -> None:
    memory.memory_sha256 = content_sha256(
        {
            "artifactRefs": memory.artifact_refs,
            "content": memory.content_json,
            "invalidationRule": memory.invalidation_rule,
            "scopeWorkflowId": memory.scope_workflow_id,
            "sourceRefs": memory.source_refs,
            "subjectKey": memory.subject_key,
            "type": memory.type,
        }
    )


def _source_evidence_memory(
    session: Session,
    workflow: WorkflowRecord,
    observation: StepObservationRecord,
    suffix: str,
) -> tuple[
    SourceRecord,
    SourcePageRecord,
    EvidenceSpanRecord,
    ResearchMemoryRecord,
]:
    source = SourceRecord(
        id=f"source-{suffix}",
        project_id=workflow.project_id,
        title="Verified local source",
        source_kind="pdf",
        authors=[],
        local_path=f"/tmp/{suffix}.pdf",
        ingestion_status="ready",
        content_hash=hashlib.sha256(f"source-{suffix}".encode()).hexdigest(),
        page_count=1,
    )
    quote = "The intervention reduced symptoms significantly."
    page_text = f"Background context. {quote} Follow-up context."
    words: list[dict[str, object]] = []
    x = 10.0
    for value in page_text.split():
        width = max(8.0, float(len(value) * 5))
        words.append(
            {
                "text": value,
                "x0": x,
                "y0": 20.0,
                "x1": x + width,
                "y1": 32.0,
            }
        )
        x += width + 4.0
    page = SourcePageRecord(
        source_id=source.id,
        page_index=0,
        page_label="1",
        width=600,
        height=800,
        text=page_text,
        words=words,
    )
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
    assert located is not None and located.verified
    evidence = EvidenceSpanRecord(
        id=f"evidence-{suffix}",
        source_id=source.id,
        page_index=0,
        page_label=located.page_label,
        text=quote,
        bbox=located.bbox,
        coordinate_space="normalized-rotated-top-left-v1",
        quote_hash=hashlib.sha256(quote.encode()).hexdigest(),
        extraction_method="exact-quote-v1",
        confidence=1.0,
        verified=True,
    )
    memory = create_observation_memory_candidates(
        session,
        workflow,
        observation,
    )[0]
    memory.source_refs = [
        *memory.source_refs,
        {
            "id": source.id,
            "sha256": source.content_hash,
            "type": "source",
        },
        {
            "id": evidence.id,
            "sha256": evidence.quote_hash,
            "type": "evidence",
        },
    ]
    _rehash(memory)
    resolve_memory_candidate(
        session,
        workflow,
        memory,
        decision="accept",
        expected_content_hash=memory.memory_sha256,
    )
    session.add(source)
    session.flush()
    session.add(page)
    session.flush()
    session.add(evidence)
    session.flush()
    session.flush()
    return source, page, evidence, memory


def resolve_memory_candidate(
    session: Session,
    workflow: WorkflowRecord,
    memory: ResearchMemoryRecord,
    *,
    decision: str,
    expected_content_hash: str,
) -> ResearchMemoryRecord:
    head = session.execute(
        select(ResearchMemoryRecord.id, ResearchMemoryRecord.revision)
        .where(
            ResearchMemoryRecord.project_id == workflow.project_id,
            ResearchMemoryRecord.scope_workflow_id == workflow.id,
            ResearchMemoryRecord.subject_key == memory.subject_key,
        )
        .order_by(ResearchMemoryRecord.revision.desc())
        .limit(1)
    ).one()
    return _resolve_memory_candidate(
        session,
        workflow,
        memory,
        decision=decision,
        expected_content_hash=expected_content_hash,
        expected_status="candidate",
        expected_revision=memory.revision,
        expected_subject_head_id=head.id,
        expected_subject_head_revision=head.revision,
    )


@pytest.fixture
def memory_store(
    tmp_path: Path,
) -> Generator[tuple[sessionmaker[Session], TypedTestClient], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'research-memory.sqlite3'}",
        connect_args={"check_same_thread": False},
    )

    def configure_sqlite(
        dbapi_connection: DBAPIConnection,
        _record: object,
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    event.listen(engine, "connect", configure_sqlite)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def dependency() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(research_memory_router)
    app.dependency_overrides[get_research_memory_session] = dependency
    client = TypedTestClient(app)
    try:
        yield factory, client
    finally:
        client.close()
        engine.dispose()


def _seed_workflow(
    session: Session,
    tmp_path: Path,
    suffix: str,
    *,
    project: ProjectRecord | None = None,
) -> tuple[WorkflowRecord, PlanRecord, TaskRecord]:
    if project is None:
        project = ProjectRecord(
            id=f"project-{suffix}",
            title=f"Memory {suffix}",
            description="",
            project_path=str(tmp_path / suffix),
            execution_mode="safe",
        )
        session.add(project)
        session.flush()
    assert project is not None
    workflow = WorkflowRecord(
        id=f"workflow-{suffix}",
        project_id=project.id,
        create_idempotency_key=f"create-{suffix}",
        create_payload_sha256="a" * 64,
        creation_mode="autonomous",
        selected_source_ids=[],
        workflow_type="literature-synthesis",
        goal="Keep verified research continuity.",
        generation_mode="local-deterministic",
        status="running",
        row_version=1,
        event_sequence=0,
    )
    session.add(workflow)
    session.flush()
    plan = PlanRecord(
        id=f"plan-{suffix}",
        workflow_id=workflow.id,
        version=1,
        spec_json={"steps": [{"key": "inspect-sources"}]},
        spec_sha256="b" * 64,
        status="approved",
        generator="test",
        approved_at=utc_now(),
    )
    session.add(plan)
    session.flush()
    task = TaskRecord(
        id=f"task-{suffix}",
        project_id=project.id,
        workflow_id=workflow.id,
        plan_id=plan.id,
        step_key="inspect-sources",
        order_index=1,
        objective="Inspect sources.",
        task_type="inspect-sources",
        inputs={},
        expected_outputs=[],
        outputs={},
        acceptance_criteria=[],
        permissions=[],
        status="failed",
    )
    session.add(task)
    session.flush()
    return workflow, plan, task


def test_verified_evidence_creates_reviewable_idempotent_memory_candidate(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
) -> None:
    factory, client = memory_store
    with factory() as session:
        workflow, plan, task = _seed_workflow(session, tmp_path, "remember")
        observation = _observation(
            session,
            workflow,
            plan,
            task,
            "remember",
            status="blocked",
            question=True,
        )
        source, _page, evidence, _memory = _source_evidence_memory(
            session,
            workflow,
            observation,
            "remember",
        )
        snapshot_a = get_or_create_context_snapshot(
            session,
            workflow,
            plan_id=plan.id,
            observation_id=observation.id,
        )
        session.commit()
        project_id = workflow.project_id
        workflow_id = workflow.id
        snapshot_a_id = snapshot_a.id
        snapshot_a_sha256 = snapshot_a.context_sha256

    endpoint = (
        f"/v1/projects/{project_id}/workflows/{workflow_id}"
        "/research-memory-candidates/from-evidence"
    )
    payload = {
        "evidenceId": evidence.id,
        "expectedSourceContentHash": source.content_hash,
        "expectedQuoteHash": evidence.quote_hash,
    }
    created = client.post(endpoint, json=payload, headers={"Idempotency-Key": "remember-1"})
    assert created.status_code == 200
    created_value = created.json()
    assert created_value["outcome"] == "candidate-created"
    memory = created_value["memory"]
    assert memory["status"] == "candidate"
    assert memory["type"] == "user-decision"
    assert memory["contentJson"] == {
        "schemaVersion": "1",
        "kind": "remembered-evidence",
        "evidenceId": evidence.id,
        "sourceId": source.id,
        "pageIndex": 0,
        "quoteHash": evidence.quote_hash,
        "claimStatus": "not-a-claim",
    }
    assert memory["sourceRefs"] == [
        {"id": source.id, "sha256": source.content_hash, "type": "source"},
        {"id": evidence.id, "sha256": evidence.quote_hash, "type": "evidence"},
    ]
    assert memory["artifactRefs"] == []
    episode = created_value["verifiedEpisode"]
    assert episode["schemaVersion"] == "1"
    assert episode["action"] == "remembered-evidence-action-v1"
    assert len(episode["episodeSha256"]) == 64

    replay = client.post(endpoint, json=payload, headers={"Idempotency-Key": "remember-2"})
    assert replay.status_code == 200
    assert replay.json()["outcome"] == "already-remembered"
    assert replay.json()["memory"]["id"] == memory["id"]
    assert replay.json()["verifiedEpisode"] == episode

    workspace = client.get(
        f"/v1/projects/{project_id}/workflows/{workflow_id}/research-memory-workspace"
    ).json()
    remembered_item = next(item for item in workspace["items"] if item["id"] == memory["id"])
    accepted = client.post(
        (
            f"/v1/projects/{project_id}/workflows/{workflow_id}"
            f"/research-memories/{memory['id']}/resolve"
        ),
        json={
            "decision": "accept",
            "expectedContentHash": memory["memorySha256"],
            "expectedStatus": "candidate",
            "expectedRevision": memory["revision"],
            "expectedSubjectHeadId": remembered_item["subjectHeadId"],
            "expectedSubjectHeadRevision": remembered_item["subjectHeadRevision"],
        },
    )
    assert accepted.status_code == 200

    with factory() as session:
        workflow = session.get(WorkflowRecord, workflow_id)
        plan = session.scalar(select(PlanRecord).where(PlanRecord.workflow_id == workflow_id))
        task = session.scalar(select(TaskRecord).where(TaskRecord.workflow_id == workflow_id))
        assert workflow is not None and plan is not None and task is not None
        next_observation = _observation(
            session,
            workflow,
            plan,
            task,
            "remember-next",
            status="succeeded",
            question=False,
        )
        snapshot_b = get_or_create_context_snapshot(
            session,
            workflow,
            plan_id=plan.id,
            observation_id=next_observation.id,
        )
        remembered_context = next(
            item for item in snapshot_b.context_json["items"] if item["id"] == memory["id"]
        )
        assert remembered_context["sourceRefs"] == memory["sourceRefs"]
        immutable_a = session.get(AgentContextSnapshotRecord, snapshot_a_id)
        assert immutable_a is not None
        assert immutable_a.context_sha256 == snapshot_a_sha256
        assert all(ref["id"] != memory["id"] for ref in immutable_a.selected_memory_refs)
        session.commit()

    stale = client.post(
        endpoint,
        json={**payload, "expectedQuoteHash": "f" * 64},
        headers={"Idempotency-Key": "remember-stale"},
    )
    assert stale.status_code == 409

    workspace = client.get(
        f"/v1/projects/{project_id}/workflows/{workflow_id}/research-memory-workspace"
    ).json()
    committed_item = next(item for item in workspace["items"] if item["id"] == memory["id"])
    invalidated = client.post(
        (
            f"/v1/projects/{project_id}/workflows/{workflow_id}"
            f"/research-memories/{memory['id']}/invalidate"
        ),
        json={
            "expectedContentHash": memory["memorySha256"],
            "expectedStatus": "committed",
            "expectedRevision": memory["revision"],
            "expectedSubjectHeadId": committed_item["subjectHeadId"],
            "expectedSubjectHeadRevision": committed_item["subjectHeadRevision"],
        },
    )
    assert invalidated.status_code == 200
    reopened = client.post(
        endpoint,
        json=payload,
        headers={"Idempotency-Key": "remember-reopen"},
    )
    assert reopened.status_code == 200
    assert reopened.json()["outcome"] == "candidate-reopened"
    assert reopened.json()["memory"]["revision"] == 2
    assert reopened.json()["memory"]["previousId"] == memory["id"]
    assert reopened.json()["verifiedEpisode"]["episodeId"] != episode["episodeId"]
    reopened_replay = client.post(
        endpoint,
        json=payload,
        headers={"Idempotency-Key": "remember-reopen-replay"},
    )
    assert reopened_replay.json()["outcome"] == "already-remembered"
    assert reopened_replay.json()["memory"]["id"] == reopened.json()["memory"]["id"]
    assert reopened_replay.json()["verifiedEpisode"] == reopened.json()["verifiedEpisode"]

    with factory() as session:
        remembered = list(
            session.scalars(
                select(ResearchMemoryRecord).where(
                    ResearchMemoryRecord.created_by == "remembered-evidence-action-v1"
                )
            )
        )
        assert {item.id for item in remembered} == {
            memory["id"],
            reopened.json()["memory"]["id"],
        }
        events = list(
            session.scalars(
                select(EventRecord).where(
                    EventRecord.event_type == "research-memory.remembered-evidence-verified"
                )
            )
        )
        assert len(events) == 2
        workflow = session.get(WorkflowRecord, workflow_id)
        assert workflow is not None
        verified = get_and_verify_remembered_evidence_episode(
            session,
            workflow,
            reopened.json()["verifiedEpisode"]["episodeId"],
        )
        assert verified.episode_sha256 == reopened.json()["verifiedEpisode"]["episodeSha256"]


def _observation(
    session: Session,
    workflow: WorkflowRecord,
    plan: PlanRecord,
    task: TaskRecord,
    suffix: str,
    *,
    status: str,
    question: bool,
) -> StepObservationRecord:
    job = JobRecord(
        id=f"source-job-{suffix}",
        workflow_id=workflow.id,
        task_id=task.id,
        kind="execute-task",
        operation_key=f"workflow:{workflow.id}:source:{suffix}",
        attempt=1,
        input_sha256="c" * 64,
        handler_version="test",
        status="failed" if status in {"failed", "blocked"} else "succeeded",
        error_code="runtime-timeout" if status in {"failed", "blocked"} else None,
        error_message="Timed out." if status in {"failed", "blocked"} else None,
        finished_at=utc_now(),
    )
    session.add(job)
    session.flush()
    value = StepObservation(
        schema_version="1",
        workflow_id=workflow.id,
        plan_id=plan.id,
        task_id=task.id,
        source_job_id=job.id,
        observation_type="step-output",
        step_key="inspect-sources",
        attempt=1,
        status=cast(Any, status),
        facts=[
            ObservationFact(
                code="step-status",
                statement="The deterministic observer recorded the step status.",
                value=status,
                source_type="workflow",
                source_id=job.id,
            )
        ],
        unresolved_questions=(
            [
                UnresolvedQuestion(
                    code="scope-choice",
                    question="Which source scope should be used?",
                    answer_type="single-choice",
                )
            ]
            if question
            else []
        ),
        failure_category=(cast(Any, "runtime") if status in {"failed", "blocked"} else "none"),
        recommended_actions=(["request-clarification", "stop"] if question else ["stop"]),
    )
    record = StepObservationRecord(
        id=f"observation-{suffix}",
        workflow_id=workflow.id,
        plan_id=plan.id,
        task_id=task.id,
        source_job_id=job.id,
        schema_version="1",
        observation_type="step-output",
        step_key="inspect-sources",
        attempt=1,
        status=status,
        facts_json=[item.model_dump(mode="json", by_alias=True) for item in value.facts],
        warnings_json=[],
        unresolved_questions_json=[
            item.model_dump(mode="json", by_alias=True) for item in value.unresolved_questions
        ],
        artifact_ids_json=[],
        failure_category=value.failure_category,
        recommended_actions_json=list(value.recommended_actions),
        input_sha256="d" * 64,
        output_sha256=step_observation_sha256(value),
        generator="deterministic-observer-v1",
    )
    session.add(record)
    session.flush()
    return record


def test_verified_episode_tamper_stale_and_scope_fail_closed(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
) -> None:
    factory, client = memory_store
    with factory() as session:
        workflow, plan, task = _seed_workflow(session, tmp_path, "episode-integrity")
        observation = _observation(
            session,
            workflow,
            plan,
            task,
            "episode-integrity",
            status="blocked",
            question=True,
        )
        source, _page, evidence, _memory = _source_evidence_memory(
            session,
            workflow,
            observation,
            "episode-integrity",
        )
        other_workflow, _other_plan, _other_task = _seed_workflow(
            session,
            tmp_path,
            "episode-other",
            project=session.get(ProjectRecord, workflow.project_id),
        )
        session.commit()
        project_id = workflow.project_id
        workflow_id = workflow.id

    endpoint = (
        f"/v1/projects/{project_id}/workflows/{workflow_id}"
        "/research-memory-candidates/from-evidence"
    )
    payload = {
        "evidenceId": evidence.id,
        "expectedSourceContentHash": source.content_hash,
        "expectedQuoteHash": evidence.quote_hash,
    }
    created = client.post(
        endpoint,
        json=payload,
        headers={"Idempotency-Key": "episode-integrity-create"},
    )
    assert created.status_code == 200
    episode_id = created.json()["verifiedEpisode"]["episodeId"]

    with factory() as session:
        workflow = session.get(WorkflowRecord, workflow_id)
        assert workflow is not None
        event = session.scalar(
            select(EventRecord).where(
                EventRecord.event_type == "research-memory.remembered-evidence-verified"
            )
        )
        assert event is not None
        original_payload = copy.deepcopy(event.payload)
        with pytest.raises(WorkflowFailure, match="missing or ambiguous"):
            get_and_verify_remembered_evidence_episode(
                session,
                other_workflow,
                episode_id,
            )
        event.payload = {
            **event.payload,
            "boundaries": {
                **cast(dict[str, bool], event.payload["boundaries"]),
                "createsClaim": True,
            },
        }
        session.commit()
    tampered_retry = client.post(
        endpoint,
        json=payload,
        headers={"Idempotency-Key": "episode-integrity-retry"},
    )
    assert tampered_retry.status_code == 409
    with factory() as session:
        event = session.scalar(
            select(EventRecord).where(
                EventRecord.event_type == "research-memory.remembered-evidence-verified"
            )
        )
        assert event is not None
        event.payload = original_payload
        session.commit()

    with factory() as session:
        workflow = session.get(WorkflowRecord, workflow_id)
        source_record = session.get(SourceRecord, source.id)
        assert workflow is not None and source_record is not None
        source_hash = source_record.content_hash
        source_record.content_hash = "f" * 64
        session.commit()
    with factory() as session:
        workflow = session.get(WorkflowRecord, workflow_id)
        assert workflow is not None
        with pytest.raises(WorkflowFailure, match="dependencies"):
            get_and_verify_remembered_evidence_episode(session, workflow, episode_id)
        source_record = session.get(SourceRecord, source.id)
        assert source_record is not None
        source_record.content_hash = source_hash
        session.commit()

    with factory() as session:
        workflow = session.get(WorkflowRecord, workflow_id)
        evidence_record = session.get(EvidenceSpanRecord, evidence.id)
        assert workflow is not None and evidence_record is not None
        evidence_record.verified = False
        session.commit()
    with factory() as session:
        workflow = session.get(WorkflowRecord, workflow_id)
        assert workflow is not None
        with pytest.raises(WorkflowFailure, match="dependencies"):
            get_and_verify_remembered_evidence_episode(session, workflow, episode_id)
        evidence_record = session.get(EvidenceSpanRecord, evidence.id)
        assert evidence_record is not None
        evidence_record.verified = True
        session.commit()

    with factory() as session:
        workflow = session.get(WorkflowRecord, workflow_id)
        memory = session.scalar(
            select(ResearchMemoryRecord).where(
                ResearchMemoryRecord.created_by == "remembered-evidence-action-v1"
            )
        )
        assert workflow is not None and memory is not None
        memory_hash = memory.memory_sha256
        memory.memory_sha256 = "e" * 64
        session.commit()
    with factory() as session:
        workflow = session.get(WorkflowRecord, workflow_id)
        assert workflow is not None
        with pytest.raises(WorkflowFailure, match="dependencies"):
            get_and_verify_remembered_evidence_episode(session, workflow, episode_id)
        memory = session.scalar(
            select(ResearchMemoryRecord).where(
                ResearchMemoryRecord.created_by == "remembered-evidence-action-v1"
            )
        )
        assert memory is not None
        memory.memory_sha256 = memory_hash
        session.commit()


def test_verified_episode_and_candidate_roll_back_together(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, client = memory_store
    with factory() as session:
        workflow, plan, task = _seed_workflow(session, tmp_path, "episode-rollback")
        observation = _observation(
            session,
            workflow,
            plan,
            task,
            "episode-rollback",
            status="blocked",
            question=True,
        )
        source, _page, evidence, _memory = _source_evidence_memory(
            session,
            workflow,
            observation,
            "episode-rollback",
        )
        session.commit()
        project_id = workflow.project_id
        workflow_id = workflow.id

    def fail_episode(*_args: object, **_kwargs: object) -> Never:
        raise WorkflowFailure(
            "verified-episode-test-failure",
            "The episode write failed after candidate creation.",
        )

    monkeypatch.setattr(
        research_memory_service,
        "_get_or_create_remembered_evidence_episode",
        fail_episode,
    )
    response = client.post(
        (
            f"/v1/projects/{project_id}/workflows/{workflow_id}"
            "/research-memory-candidates/from-evidence"
        ),
        json={
            "evidenceId": evidence.id,
            "expectedSourceContentHash": source.content_hash,
            "expectedQuoteHash": evidence.quote_hash,
        },
        headers={"Idempotency-Key": "episode-rollback-create"},
    )
    assert response.status_code == 409
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ResearchMemoryRecord)
                .where(ResearchMemoryRecord.created_by == "remembered-evidence-action-v1")
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(EventRecord)
                .where(EventRecord.event_type == "research-memory.remembered-evidence-verified")
            )
            == 0
        )


def test_candidate_generation_replay_non_generation_and_revision(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
) -> None:
    factory, _client = memory_store
    with factory() as session:
        workflow, plan, task = _seed_workflow(session, tmp_path, "one")
        first_observation = _observation(
            session, workflow, plan, task, "one-first", status="failed", question=True
        )
        first = create_observation_memory_candidates(session, workflow, first_observation)
        assert [item.type for item in first] == ["open-question", "failure-lesson"]
        assert [
            item.id
            for item in create_observation_memory_candidates(session, workflow, first_observation)
        ] == [item.id for item in first]

        second_observation = _observation(
            session, workflow, plan, task, "one-second", status="succeeded", question=True
        )
        second = create_observation_memory_candidates(session, workflow, second_observation)
        assert len(second) == 1
        assert second[0].type == "open-question"
        assert second[0].revision == 2
        assert second[0].previous_id == first[0].id
        assert first[0].status == "superseded"
        assert first[1].status == "candidate"

        third_observation = _observation(
            session, workflow, plan, task, "one-third", status="succeeded", question=False
        )
        assert create_observation_memory_candidates(session, workflow, third_observation) == ()


def test_candidate_excluded_until_accept_and_frozen_snapshot_is_unchanged(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
) -> None:
    factory, _client = memory_store
    with factory() as session:
        workflow, plan, task = _seed_workflow(session, tmp_path, "context")
        source = _observation(
            session, workflow, plan, task, "context-source", status="succeeded", question=True
        )
        candidate = create_observation_memory_candidates(session, workflow, source)[0]
        before = get_or_create_context_snapshot(
            session, workflow, plan_id=plan.id, observation_id=source.id
        )
        before_id = before.id
        before_sha256 = before.context_sha256
        before_refs = list(before.selected_memory_refs)
        assert decision_context_payload(before)["items"] == []
        resolve_memory_candidate(
            session,
            workflow,
            candidate,
            decision="accept",
            expected_content_hash=candidate.memory_sha256,
        )
        session.commit()

    with factory() as restarted_session:
        workflow = restarted_session.get_one(WorkflowRecord, "workflow-context")
        plan = restarted_session.get_one(PlanRecord, "plan-context")
        source = restarted_session.get_one(StepObservationRecord, "observation-context-source")
        candidate = restarted_session.get_one(ResearchMemoryRecord, candidate.id)
        after_restart = get_or_create_context_snapshot(
            restarted_session,
            workflow,
            plan_id=plan.id,
            observation_id=source.id,
        )
        assert after_restart.id != before_id
        assert [item["id"] for item in after_restart.context_json["items"]] == [candidate.id]
        assert (
            get_or_create_context_snapshot(
                restarted_session,
                workflow,
                plan_id=plan.id,
                observation_id=source.id,
            ).id
            == after_restart.id
        )
        frozen_before = restarted_session.get_one(
            type(after_restart),
            before_id,
        )
        assert frozen_before.context_sha256 == before_sha256
        assert frozen_before.selected_memory_refs == before_refs

        future = _observation(
            restarted_session,
            workflow,
            plan,
            restarted_session.get_one(TaskRecord, "task-context"),
            "context-future",
            status="succeeded",
            question=False,
        )
        after = get_or_create_context_snapshot(
            restarted_session,
            workflow,
            plan_id=plan.id,
            observation_id=future.id,
        )
        assert [item["id"] for item in after.context_json["items"]] == [candidate.id]


def test_accept_replay_is_idempotent_and_does_not_create_more_context_generations(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
) -> None:
    factory, _client = memory_store
    with factory() as session:
        workflow, plan, task = _seed_workflow(session, tmp_path, "accept-replay")
        source = _observation(
            session,
            workflow,
            plan,
            task,
            "accept-replay-source",
            status="succeeded",
            question=True,
        )
        candidate = create_observation_memory_candidates(session, workflow, source)[0]
        get_or_create_context_snapshot(session, workflow, plan_id=plan.id, observation_id=source.id)
        expected_hash = candidate.memory_sha256
        resolve_memory_candidate(
            session,
            workflow,
            candidate,
            decision="accept",
            expected_content_hash=expected_hash,
        )
        first_after = get_or_create_context_snapshot(
            session, workflow, plan_id=plan.id, observation_id=source.id
        )
        replayed = resolve_memory_candidate(
            session,
            workflow,
            candidate,
            decision="accept",
            expected_content_hash=expected_hash,
        )
        second_after = get_or_create_context_snapshot(
            session, workflow, plan_id=plan.id, observation_id=source.id
        )
        assert replayed.id == candidate.id
        assert replayed.status == "committed"
        assert second_after.id == first_after.id


@pytest.mark.parametrize(
    ("mutation", "reason_code"),
    [
        ("missing", "source-missing"),
        ("not-ready", "source-not-ready"),
        ("hash", "source-stale"),
    ],
)
def test_source_bound_memory_fails_closed_without_changing_semantic_status(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
    mutation: str,
    reason_code: str,
) -> None:
    factory, _client = memory_store
    with factory() as session:
        workflow, plan, task = _seed_workflow(
            session,
            tmp_path,
            f"source-dependency-{mutation}",
        )
        observation = _observation(
            session,
            workflow,
            plan,
            task,
            f"src-dep-{mutation}",
            status="succeeded",
            question=True,
        )
        source, _page, _evidence, memory = _source_evidence_memory(
            session,
            workflow,
            observation,
            f"source-dependency-{mutation}",
        )
        if mutation == "missing":
            session.delete(source)
        elif mutation == "not-ready":
            source.ingestion_status = "indexing"
        else:
            source.content_hash = "f" * 64
        session.flush()

        snapshot = get_or_create_context_snapshot(
            session,
            workflow,
            plan_id=plan.id,
            observation_id=observation.id,
        )
        workspace = get_research_memory_workspace(session, workflow)
        item = next(item for item in workspace.items if item.id == memory.id)
        assert snapshot.context_json["items"] == []
        assert item.status == "committed"
        assert item.context.state == "excluded"
        assert item.context.reason_code == reason_code


@pytest.mark.parametrize(
    "mutation",
    ["missing", "unverified", "quote", "source", "page"],
)
def test_evidence_bound_memory_fails_closed_when_verified_identity_drifts(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
    mutation: str,
) -> None:
    factory, _client = memory_store
    with factory() as session:
        workflow, plan, task = _seed_workflow(
            session,
            tmp_path,
            f"evidence-{mutation}",
        )
        observation = _observation(
            session,
            workflow,
            plan,
            task,
            f"ev-dep-{mutation}",
            status="succeeded",
            question=True,
        )
        _source, page, evidence, memory = _source_evidence_memory(
            session,
            workflow,
            observation,
            f"evidence-dependency-{mutation}",
        )
        if mutation == "missing":
            session.delete(evidence)
        elif mutation == "unverified":
            evidence.verified = False
        elif mutation == "quote":
            evidence.quote_hash = "f" * 64
        elif mutation == "source":
            substitute = SourceRecord(
                id=f"substitute-{mutation}",
                project_id=workflow.project_id,
                title="Substitute source",
                source_kind="pdf",
                authors=[],
                local_path=f"/tmp/substitute-{mutation}.pdf",
                ingestion_status="ready",
                content_hash="e" * 64,
                page_count=1,
            )
            session.add(substitute)
            session.flush()
            evidence.source_id = substitute.id
        else:
            page.text = "The referenced quote is no longer present on this page."
        session.flush()

        snapshot = get_or_create_context_snapshot(
            session,
            workflow,
            plan_id=plan.id,
            observation_id=observation.id,
        )
        workspace = get_research_memory_workspace(session, workflow)
        item = next(item for item in workspace.items if item.id == memory.id)
        assert snapshot.context_json["items"] == []
        assert item.status == "committed"
        assert item.context.reason_code == (
            "evidence-missing" if mutation == "missing" else "evidence-invalid"
        )


def test_dependency_generation_creates_restart_stable_c_and_preserves_b(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
) -> None:
    factory, _client = memory_store
    with factory() as session:
        workflow, plan, task = _seed_workflow(
            session,
            tmp_path,
            "dependency-generation",
        )
        observation = _observation(
            session,
            workflow,
            plan,
            task,
            "dependency-generation",
            status="succeeded",
            question=True,
        )
        source, _page, _evidence, memory = _source_evidence_memory(
            session,
            workflow,
            observation,
            "dependency-generation",
        )
        expected_source_hash = source.content_hash
        snapshot_b = get_or_create_context_snapshot(
            session,
            workflow,
            plan_id=plan.id,
            observation_id=observation.id,
        )
        assert [item["id"] for item in snapshot_b.context_json["items"]] == [memory.id]
        snapshot_b_sha256 = snapshot_b.context_sha256
        snapshot_b_refs = list(snapshot_b.selected_memory_refs)
        source.content_hash = "f" * 64
        session.commit()
        snapshot_b_id = snapshot_b.id

    with factory() as restarted_session:
        workflow = restarted_session.get_one(
            WorkflowRecord,
            "workflow-dependency-generation",
        )
        plan = restarted_session.get_one(
            PlanRecord,
            "plan-dependency-generation",
        )
        observation = restarted_session.get_one(
            StepObservationRecord,
            "observation-dependency-generation",
        )
        snapshot_c = get_or_create_context_snapshot(
            restarted_session,
            workflow,
            plan_id=plan.id,
            observation_id=observation.id,
        )
        assert snapshot_c.id != snapshot_b_id
        assert snapshot_c.context_json["items"] == []
        assert (
            get_or_create_context_snapshot(
                restarted_session,
                workflow,
                plan_id=plan.id,
                observation_id=observation.id,
            ).id
            == snapshot_c.id
        )
        frozen_b = restarted_session.get_one(type(snapshot_c), snapshot_b_id)
        assert frozen_b.context_sha256 == snapshot_b_sha256
        assert frozen_b.selected_memory_refs == snapshot_b_refs
        source = restarted_session.get_one(
            SourceRecord,
            "source-dependency-generation",
        )
        source.content_hash = expected_source_hash
        restarted_session.flush()
        assert (
            get_or_create_context_snapshot(
                restarted_session,
                workflow,
                plan_id=plan.id,
                observation_id=observation.id,
            ).id
            == snapshot_b_id
        )


def test_dependency_resolution_does_not_read_another_project(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
) -> None:
    factory, _client = memory_store
    with factory() as session:
        first, first_plan, first_task = _seed_workflow(
            session,
            tmp_path,
            "dependency-project-one",
        )
        second, _second_plan, _second_task = _seed_workflow(
            session,
            tmp_path,
            "dependency-project-two",
        )
        observation = _observation(
            session,
            first,
            first_plan,
            first_task,
            "dependency-project-one",
            status="succeeded",
            question=True,
        )
        second_observation = _observation(
            session,
            second,
            _second_plan,
            _second_task,
            "dependency-project-two",
            status="succeeded",
            question=True,
        )
        foreign_source, _page, foreign_evidence, _foreign_memory = _source_evidence_memory(
            session,
            second,
            second_observation,
            "dependency-project-two",
        )
        memory = create_observation_memory_candidates(
            session,
            first,
            observation,
        )[0]
        memory.source_refs = [
            *memory.source_refs,
            {
                "id": foreign_source.id,
                "sha256": foreign_source.content_hash,
                "type": "source",
            },
            {
                "id": foreign_evidence.id,
                "sha256": foreign_evidence.quote_hash,
                "type": "evidence",
            },
        ]
        _rehash(memory)
        resolve_memory_candidate(
            session,
            first,
            memory,
            decision="accept",
            expected_content_hash=memory.memory_sha256,
        )
        session.flush()

        snapshot = get_or_create_context_snapshot(
            session,
            first,
            plan_id=first_plan.id,
            observation_id=observation.id,
        )
        workspace = get_research_memory_workspace(session, first)
        item = next(item for item in workspace.items if item.id == memory.id)
        assert snapshot.context_json["items"] == []
        assert item.context.reason_code == "source-missing"


def test_candidate_tamper_fails_closed(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
) -> None:
    factory, _client = memory_store
    with factory() as session:
        workflow, plan, task = _seed_workflow(session, tmp_path, "tamper")
        observation = _observation(
            session, workflow, plan, task, "tamper", status="succeeded", question=True
        )
        candidate = create_observation_memory_candidates(session, workflow, observation)[0]
        candidate.content_json = {**candidate.content_json, "question": "Tampered"}
        _rehash(candidate)
        session.flush()
        with pytest.raises(WorkflowFailure, match="source observation"):
            resolve_memory_candidate(
                session,
                workflow,
                candidate,
                decision="accept",
                expected_content_hash=candidate.memory_sha256,
            )


def test_workflows_in_one_project_never_share_revision_or_supersession(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
) -> None:
    factory, _client = memory_store
    with factory() as session:
        first, first_plan, first_task = _seed_workflow(session, tmp_path, "shared-one")
        project = session.get_one(ProjectRecord, first.project_id)
        second, second_plan, second_task = _seed_workflow(
            session, tmp_path, "shared-two", project=project
        )
        first_source = _observation(
            session,
            first,
            first_plan,
            first_task,
            "shared-one-source",
            status="succeeded",
            question=True,
        )
        second_source = _observation(
            session,
            second,
            second_plan,
            second_task,
            "shared-two-source",
            status="succeeded",
            question=True,
        )
        first_memory = create_observation_memory_candidates(session, first, first_source)[0]
        second_memory = create_observation_memory_candidates(session, second, second_source)[0]
        assert first_memory.subject_key != second_memory.subject_key
        assert first_memory.revision == second_memory.revision == 1
        for workflow, memory in ((first, first_memory), (second, second_memory)):
            resolve_memory_candidate(
                session,
                workflow,
                memory,
                decision="accept",
                expected_content_hash=memory.memory_sha256,
            )
        revision_source = _observation(
            session,
            first,
            first_plan,
            first_task,
            "shared-one-revision",
            status="succeeded",
            question=True,
        )
        revision = create_observation_memory_candidates(session, first, revision_source)[0]
        assert revision.revision == 2
        resolve_memory_candidate(
            session,
            first,
            revision,
            decision="accept",
            expected_content_hash=revision.memory_sha256,
        )
        assert first_memory.status == "superseded"
        assert second_memory.status == "committed"


def test_rehashed_source_drift_and_committed_replay_corruption_fail_closed(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
) -> None:
    factory, _client = memory_store
    with factory() as session:
        workflow, plan, task = _seed_workflow(session, tmp_path, "source-drift")
        observation = _observation(
            session,
            workflow,
            plan,
            task,
            "source-drift",
            status="succeeded",
            question=True,
        )
        candidate = create_observation_memory_candidates(session, workflow, observation)[0]
        session.commit()
        candidate.source_refs = [{**candidate.source_refs[0], "sha256": "e" * 64}]
        _rehash(candidate)
        session.flush()
        with pytest.raises(WorkflowFailure, match="source observation"):
            resolve_memory_candidate(
                session,
                workflow,
                candidate,
                decision="accept",
                expected_content_hash=candidate.memory_sha256,
            )
        session.rollback()

    with factory() as session:
        workflow = session.get_one(WorkflowRecord, "workflow-source-drift")
        observation = session.get_one(StepObservationRecord, "observation-source-drift")
        candidate = session.scalar(
            select(ResearchMemoryRecord).where(
                ResearchMemoryRecord.scope_workflow_id == workflow.id
            )
        )
        assert candidate is not None
        resolve_memory_candidate(
            session,
            workflow,
            candidate,
            decision="accept",
            expected_content_hash=candidate.memory_sha256,
        )
        observation.output_sha256 = "e" * 64
        session.flush()
        with pytest.raises(WorkflowFailure, match="source observation"):
            resolve_memory_candidate(
                session,
                workflow,
                candidate,
                decision="accept",
                expected_content_hash=candidate.memory_sha256,
            )


def test_review_api_enforces_scope_hash_and_state_machine(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
) -> None:
    factory, client = memory_store
    with factory() as session:
        workflow, plan, task = _seed_workflow(session, tmp_path, "api")
        other, _other_plan, _other_task = _seed_workflow(session, tmp_path, "other")
        observation = _observation(
            session, workflow, plan, task, "api", status="succeeded", question=True
        )
        candidate = create_observation_memory_candidates(session, workflow, observation)[0]
        candidate_id = candidate.id
        candidate_hash = candidate.memory_sha256
        session.commit()

    base = (
        f"/v1/projects/{workflow.project_id}/workflows/{workflow.id}"
        f"/research-memories/{candidate_id}"
    )
    candidate_guard = {
        "expectedStatus": "candidate",
        "expectedRevision": 1,
        "expectedSubjectHeadId": candidate_id,
        "expectedSubjectHeadRevision": 1,
    }
    assert (
        client.get(f"/v1/projects/{other.project_id}/workflows/{other.id}/research-memories").json()
        == []
    )
    assert (
        client.post(
            f"/v1/projects/{other.project_id}/workflows/{other.id}"
            f"/research-memories/{candidate_id}/resolve",
            json={
                "decision": "accept",
                "expectedContentHash": candidate_hash,
                **candidate_guard,
            },
        ).status_code
        == 404
    )
    stale = client.post(
        f"{base}/resolve",
        json={
            "decision": "accept",
            "expectedContentHash": "f" * 64,
            **candidate_guard,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "research-memory-content-stale"
    accepted = client.post(
        f"{base}/resolve",
        json={
            "decision": "accept",
            "expectedContentHash": candidate_hash,
            **candidate_guard,
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "committed"
    replayed = client.post(
        f"{base}/resolve",
        json={
            "decision": "accept",
            "expectedContentHash": candidate_hash,
            **candidate_guard,
        },
    )
    assert replayed.status_code == 200
    assert replayed.json()["id"] == candidate_id
    assert replayed.json()["status"] == "committed"
    conflicting = client.post(
        f"{base}/resolve",
        json={
            "decision": "reject",
            "expectedContentHash": candidate_hash,
            **candidate_guard,
        },
    )
    assert conflicting.status_code == 409
    invalidated = client.post(
        f"{base}/invalidate",
        json={
            "expectedContentHash": candidate_hash,
            "expectedStatus": "committed",
            "expectedRevision": 1,
            "expectedSubjectHeadId": candidate_id,
            "expectedSubjectHeadRevision": 1,
        },
    )
    assert invalidated.status_code == 200
    assert invalidated.json()["status"] == "invalidated"
    assert (
        client.post(
            f"{base}/invalidate",
            json={
                "expectedContentHash": candidate_hash,
                "expectedStatus": "committed",
                "expectedRevision": 1,
                "expectedSubjectHeadId": candidate_id,
                "expectedSubjectHeadRevision": 1,
            },
        ).status_code
        == 409
    )


def test_workspace_read_is_atomic_scoped_and_restart_stable(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
) -> None:
    factory, client = memory_store
    with factory() as session:
        workflow, plan, task = _seed_workflow(session, tmp_path, "workspace")
        other, _other_plan, _other_task = _seed_workflow(
            session,
            tmp_path,
            "workspace-other",
        )
        observation = _observation(
            session,
            workflow,
            plan,
            task,
            "workspace-source",
            status="succeeded",
            question=True,
        )
        candidate = create_observation_memory_candidates(
            session,
            workflow,
            observation,
        )[0]
        candidate_id = candidate.id
        candidate_hash = candidate.memory_sha256
        session.commit()

    workspace_url = (
        f"/v1/projects/{workflow.project_id}/workflows/{workflow.id}/research-memory-workspace"
    )
    first = client.get(workspace_url)
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["counts"] == {
        "candidate": 1,
        "committed": 0,
        "rejected": 0,
        "superseded": 0,
        "invalidated": 0,
    }
    assert first_payload["items"][0]["id"] == candidate_id
    assert first_payload["items"][0]["availableActions"] == [
        "accept",
        "reject",
    ]
    assert first_payload["items"][0]["context"] == {
        "state": "excluded",
        "reasonCode": "candidate-excluded",
        "snapshotId": None,
        "snapshotSha256": None,
    }
    assert (
        client.get(
            f"/v1/projects/{other.project_id}/workflows/{workflow.id}/research-memory-workspace"
        ).status_code
        == 404
    )

    accepted = client.post(
        (
            f"/v1/projects/{workflow.project_id}/workflows/{workflow.id}"
            f"/research-memories/{candidate_id}/resolve"
        ),
        json={
            "decision": "accept",
            "expectedContentHash": candidate_hash,
            "expectedStatus": "candidate",
            "expectedRevision": 1,
            "expectedSubjectHeadId": candidate_id,
            "expectedSubjectHeadRevision": 1,
        },
    )
    assert accepted.status_code == 200
    with factory() as session:
        persisted_workflow = session.get_one(WorkflowRecord, workflow.id)
        persisted_plan = session.get_one(PlanRecord, plan.id)
        persisted_task = session.get_one(TaskRecord, task.id)
        future = _observation(
            session,
            persisted_workflow,
            persisted_plan,
            persisted_task,
            "workspace-future",
            status="succeeded",
            question=False,
        )
        snapshot = get_or_create_context_snapshot(
            session,
            persisted_workflow,
            plan_id=persisted_plan.id,
            observation_id=future.id,
        )
        session.commit()
        snapshot_id = snapshot.id

    restarted = client.get(workspace_url)
    assert restarted.status_code == 200
    restarted_payload = restarted.json()
    assert restarted_payload["latestContextSnapshotId"] == snapshot_id
    assert restarted_payload["counts"]["committed"] == 1
    assert restarted_payload["items"][0]["context"]["reasonCode"] == ("selected-in-latest-snapshot")
    assert (
        restarted_payload["workspaceSha256"] == client.get(workspace_url).json()["workspaceSha256"]
    )


def test_committed_remembered_evidence_creates_sanitized_evaluated_skill_candidate(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, client = memory_store
    with factory() as session:
        workflow, plan, task = _seed_workflow(session, tmp_path, "skill-candidate")
        observation = _observation(
            session,
            workflow,
            plan,
            task,
            "skill-candidate",
            status="blocked",
            question=True,
        )
        source, _page, evidence, _memory = _source_evidence_memory(
            session,
            workflow,
            observation,
            "skill-candidate",
        )
        session.commit()
        project_id = workflow.project_id
        workflow_id = workflow.id

    remembered = client.post(
        (
            f"/v1/projects/{project_id}/workflows/{workflow_id}"
            "/research-memory-candidates/from-evidence"
        ),
        json={
            "evidenceId": evidence.id,
            "expectedSourceContentHash": source.content_hash,
            "expectedQuoteHash": evidence.quote_hash,
        },
        headers={"Idempotency-Key": "remember-for-skill"},
    )
    assert remembered.status_code == 200
    memory = remembered.json()["memory"]
    workspace = client.get(
        f"/v1/projects/{project_id}/workflows/{workflow_id}/research-memory-workspace"
    ).json()
    item = next(value for value in workspace["items"] if value["id"] == memory["id"])
    accepted = client.post(
        (
            f"/v1/projects/{project_id}/workflows/{workflow_id}"
            f"/research-memories/{memory['id']}/resolve"
        ),
        json={
            "decision": "accept",
            "expectedContentHash": memory["memorySha256"],
            "expectedStatus": "candidate",
            "expectedRevision": memory["revision"],
            "expectedSubjectHeadId": item["subjectHeadId"],
            "expectedSubjectHeadRevision": item["subjectHeadRevision"],
        },
    )
    assert accepted.status_code == 200

    endpoint = f"/v1/projects/{project_id}/workflows/{workflow_id}/skill-candidates"
    payload = {
        "memoryId": memory["id"],
        "expectedMemoryContentHash": memory["memorySha256"],
    }
    created = client.post(
        endpoint,
        json=payload,
        headers={"Idempotency-Key": "skill-1"},
    )
    assert created.status_code == 200, created.text
    result = created.json()
    assert result["outcome"] == "candidate-created"
    candidate = result["candidate"]
    assert candidate["status"] == "awaiting-approval"
    assert candidate["allowedToolsJson"] == ["spark.research_memory.remember_verified_evidence@1"]
    assert candidate["requiredPermissionsJson"] == ["project-memory:candidate-write"]
    assert candidate["originTraceIds"] == [remembered.json()["verifiedEpisode"]["episodeId"]]
    assert candidate["evaluationJson"]["passed"] is True
    assert [item["name"] for item in candidate["evaluationJson"]["results"]] == [
        "happy",
        "malformed",
        "tool-failure",
        "permission-denial",
        "prompt-injection",
        "restart-recovery",
    ]
    assert all(item["passed"] for item in candidate["evaluationJson"]["results"])
    skill_md = candidate["generatedSkillMd"]
    assert skill_md == SKILL_MD
    assert f"```json\n{CAPABILITY_ARGUMENT_EXAMPLE}\n```" in skill_md
    assert CAPABILITY_ARGUMENT_KEYS == {
        "evidenceId",
        "expectedSourceContentHash",
        "expectedQuoteHash",
    }
    assert "three camelCase keys and no extra keys" in skill_md
    assert "Never supply project or workflow identifiers as capability arguments" in skill_md
    assert "Never read, copy, or pass raw evidence text" in skill_md
    assert "trusted bound execution context or exact capability is unavailable" in skill_md
    for leaked in (
        project_id,
        workflow_id,
        source.id,
        evidence.id,
        memory["id"],
        source.content_hash,
        evidence.quote_hash,
        evidence.text,
    ):
        assert leaked not in skill_md
    assert set(
        line.split(":", 1)[0] for line in skill_md.split("---", 2)[1].strip().splitlines()
    ) == {"name", "description"}

    core_data = tmp_path / "core-data"
    project_root = core_data / "projects" / project_id
    project_root.mkdir(parents=True)
    with factory() as session:
        project = session.get_one(ProjectRecord, project_id)
        project.project_path = str(project_root)
        session.commit()
    monkeypatch.setattr(
        skill_activation_service,
        "settings",
        SimpleNamespace(data_dir=core_data),
    )
    target_dir = project_root / ".opencode" / "skills" / "remember-verified-evidence"
    target_dir.mkdir(parents=True)
    target = target_dir / "SKILL.md"
    prior = b"\x00\xffprior-project-skill\n"
    target.write_bytes(prior)

    activation_base = f"{endpoint}/{candidate['id']}"
    preview = client.get(f"{activation_base}/activation-preview")
    assert preview.status_code == 200, preview.text
    preview_payload = preview.json()
    assert preview_payload["priorPresent"] is True
    assert preview_payload["priorSha256"] == hashlib.sha256(prior).hexdigest()
    approval = {
        "expectedStatus": "awaiting-approval",
        "expectedCandidateContentHash": preview_payload["candidateContentHash"],
        "expectedTemplateSha256": preview_payload["templateSha256"],
        "expectedEvaluationSha256": preview_payload["evaluationSha256"],
        "expectedApprovalSha256": preview_payload["approvalSha256"],
        "expectedPriorPresent": preview_payload["priorPresent"],
        "expectedPriorSha256": preview_payload["priorSha256"],
        "expectedTargetDirectoryPresent": preview_payload["targetDirectoryPresent"],
    }
    stale_approval = client.post(
        f"{activation_base}/approve-and-activate",
        json={
            **approval,
            "expectedApprovalSha256": "0" * 64,
        },
        headers={"Idempotency-Key": "activate-stale-preview"},
    )
    assert stale_approval.status_code == 409
    assert target.read_bytes() == prior
    extra_approval = client.post(
        f"{activation_base}/approve-and-activate",
        json={**approval, "workflowId": workflow_id},
        headers={"Idempotency-Key": "activate-extra-key"},
    )
    assert extra_approval.status_code == 422
    activated = client.post(
        f"{activation_base}/approve-and-activate",
        json=approval,
        headers={"Idempotency-Key": "activate-skill-prior"},
    )
    assert activated.status_code == 200, activated.text
    activation = activated.json()
    assert activation["status"] == "active"
    assert (
        client.get(
            f"/v1/projects/{project_id}/skill-activations",
            params={"workflow_id": workflow_id},
        ).json()[0]["id"]
        == activation["id"]
    )
    assert (
        client.get(f"/v1/projects/{project_id}/skill-activations/{activation['id']}").json()[
            "status"
        ]
        == "active"
    )
    with factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO skill_activations "
                    "(id, project_id, workflow_id, candidate_id, schema_version, "
                    "skill_name, target_relative_path, candidate_content_hash, "
                    "template_sha256, evaluation_sha256, approval_sha256, "
                    "request_sha256, idempotency_key, prior_present, prior_bytes, "
                    "prior_sha256, installed_sha256, created_directory, status, "
                    "created_at, updated_at) "
                    "SELECT 'concurrent-activation', project_id, workflow_id, "
                    "candidate_id, schema_version, skill_name, target_relative_path, "
                    "candidate_content_hash, template_sha256, evaluation_sha256, "
                    "approval_sha256, request_sha256, 'concurrent-key', prior_present, "
                    "prior_bytes, prior_sha256, installed_sha256, created_directory, "
                    "'installing', created_at, updated_at "
                    "FROM skill_activations WHERE id = :activation_id"
                ),
                {"activation_id": activation["id"]},
            )
            session.commit()
        session.rollback()
    assert target.read_bytes() == SKILL_MD.encode()
    assert hashlib.sha256(target.read_bytes()).hexdigest() == activation["installedSha256"]
    with factory() as session:
        post_write = session.get_one(SkillActivationRecord, activation["id"])
        post_write.status = "installing"
        post_write.activated_at = None
        session.commit()
    finalized_by_preview = client.get(f"{activation_base}/activation-preview")
    assert finalized_by_preview.status_code == 200
    assert finalized_by_preview.json()["latestActivation"]["status"] == "active"
    assert (
        client.post(
            f"{activation_base}/approve-and-activate",
            json=approval,
            headers={"Idempotency-Key": "activate-skill-prior"},
        ).json()["id"]
        == activation["id"]
    )
    conflict = client.post(
        f"{activation_base}/approve-and-activate",
        json={
            **approval,
            "expectedApprovalSha256": "1" * 64,
        },
        headers={"Idempotency-Key": "activate-skill-prior"},
    )
    assert conflict.status_code == 409

    rollback_payload = {
        "expectedStatus": "active",
        "expectedActivationId": activation["id"],
        "expectedApprovalSha256": activation["approvalSha256"],
        "expectedInstalledSha256": activation["installedSha256"],
        "expectedCurrentTargetSha256": activation["installedSha256"],
    }
    with factory() as session:
        tampered_pending = session.get_one(
            SkillActivationRecord,
            activation["id"],
        )
        tampered_pending.status = "rollback-pending"
        tampered_pending.rollback_idempotency_key = "tampered-prior-recovery"
        tampered_pending.rollback_request_sha256 = content_sha256(
            rollback_payload,
        )
        tampered_pending.prior_bytes = b"tampered-prior-bytes"
        session.commit()
    rejected_recovery = client.get(
        f"/v1/projects/{project_id}/skill-activations/{activation['id']}"
    )
    assert rejected_recovery.status_code == 409
    assert target.read_bytes() == SKILL_MD.encode()
    assert hashlib.sha256(target.read_bytes()).hexdigest() == activation["installedSha256"]
    with factory() as session:
        blocked = session.get_one(SkillActivationRecord, activation["id"])
        assert blocked.status == "blocked"
        assert blocked.prior_bytes == b"tampered-prior-bytes"
        blocked.status = "active"
        blocked.prior_bytes = prior
        blocked.rollback_idempotency_key = None
        blocked.rollback_request_sha256 = None
        session.commit()

    invoked = client.post(
        (f"/v1/projects/{project_id}/active-skill-capabilities/remember-verified-evidence/invoke"),
        json={
            "evidenceId": evidence.id,
            "expectedSourceContentHash": source.content_hash,
            "expectedQuoteHash": evidence.quote_hash,
        },
        headers={"Idempotency-Key": "invoke-active-skill"},
    )
    assert invoked.status_code == 200, invoked.text
    assert invoked.json()["outcome"] == "already-remembered"
    target.write_bytes(b"user-edited-after-activation")
    with factory() as session:
        before_tampered_invoke = (
            session.scalar(select(func.count()).select_from(ResearchMemoryRecord)),
            session.scalar(select(func.count()).select_from(EventRecord)),
        )
    tampered_invoke = client.post(
        (f"/v1/projects/{project_id}/active-skill-capabilities/remember-verified-evidence/invoke"),
        json={
            "evidenceId": evidence.id,
            "expectedSourceContentHash": source.content_hash,
            "expectedQuoteHash": evidence.quote_hash,
        },
        headers={"Idempotency-Key": "invoke-tampered-skill"},
    )
    assert tampered_invoke.status_code == 409
    with factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(ResearchMemoryRecord)),
            session.scalar(select(func.count()).select_from(EventRecord)),
        ) == before_tampered_invoke
    drifted_rollback = client.post(
        f"/v1/projects/{project_id}/skill-activations/{activation['id']}/rollback",
        json={
            "expectedStatus": "active",
            "expectedActivationId": activation["id"],
            "expectedApprovalSha256": activation["approvalSha256"],
            "expectedInstalledSha256": activation["installedSha256"],
            "expectedCurrentTargetSha256": activation["installedSha256"],
        },
        headers={"Idempotency-Key": "rollback-user-edit"},
    )
    assert drifted_rollback.status_code == 409
    with factory() as session:
        assert session.get_one(SkillActivationRecord, activation["id"]).status == "active"
    target.write_bytes(SKILL_MD.encode())

    original_atomic_write = cast(
        Callable[[Path, bytes], None],
        getattr(skill_activation_service, "_atomic_write"),
    )

    def fail_atomic_write(_target: Path, _content: bytes) -> Never:
        raise WorkflowFailure(
            "test-install-interruption",
            "Synthetic interruption after the durable intent.",
        )

    monkeypatch.setattr(
        skill_activation_service,
        "_atomic_write",
        fail_atomic_write,
    )
    interrupted_rollback = client.post(
        f"/v1/projects/{project_id}/skill-activations/{activation['id']}/rollback",
        json={
            "expectedStatus": "active",
            "expectedActivationId": activation["id"],
            "expectedApprovalSha256": activation["approvalSha256"],
            "expectedInstalledSha256": activation["installedSha256"],
            "expectedCurrentTargetSha256": activation["installedSha256"],
        },
        headers={"Idempotency-Key": "rollback-skill-prior"},
    )
    assert interrupted_rollback.status_code == 409
    with factory() as session:
        assert session.get_one(SkillActivationRecord, activation["id"]).status == "rollback-pending"
    monkeypatch.setattr(
        skill_activation_service,
        "_atomic_write",
        original_atomic_write,
    )
    recovered_by_read = client.get(
        f"/v1/projects/{project_id}/skill-activations/{activation['id']}"
    )
    assert recovered_by_read.status_code == 200
    assert recovered_by_read.json()["status"] == "rolled-back"
    assert target.read_bytes() == prior
    rollback = client.post(
        f"/v1/projects/{project_id}/skill-activations/{activation['id']}/rollback",
        json={
            "expectedStatus": "active",
            "expectedActivationId": activation["id"],
            "expectedApprovalSha256": activation["approvalSha256"],
            "expectedInstalledSha256": activation["installedSha256"],
            "expectedCurrentTargetSha256": activation["installedSha256"],
        },
        headers={"Idempotency-Key": "rollback-skill-prior"},
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["status"] == "rolled-back"
    assert target.read_bytes() == prior
    with factory() as session:
        post_restore = session.get_one(SkillActivationRecord, activation["id"])
        post_restore.status = "rollback-pending"
        session.commit()
    finalized_rollback = client.get(
        f"/v1/projects/{project_id}/skill-activations/{activation['id']}"
    )
    assert finalized_rollback.status_code == 200
    assert finalized_rollback.json()["status"] == "rolled-back"
    assert target.read_bytes() == prior
    (target_dir / "unknown.txt").write_text("unmanaged", encoding="utf-8")
    unknown = client.get(f"{activation_base}/activation-preview")
    assert unknown.status_code == 409
    assert unknown.json()["detail"]["code"] == ("skill-target-contains-unknown-files")
    (target_dir / "unknown.txt").unlink()
    target.unlink()
    target.symlink_to(project_root / "outside-skill")
    symlinked = client.get(f"{activation_base}/activation-preview")
    assert symlinked.status_code == 409
    assert symlinked.json()["detail"]["code"] == "skill-target-invalid"
    target.unlink()
    outside = project_root / "outside-skill"
    outside.write_bytes(prior)
    target.hardlink_to(outside)
    hardlinked = client.get(f"{activation_base}/activation-preview")
    assert hardlinked.status_code == 409
    assert hardlinked.json()["detail"]["code"] == "skill-target-invalid"
    target.unlink()
    outside.unlink()
    if hasattr(os, "mkfifo"):
        os.mkfifo(target)
        special = client.get(f"{activation_base}/activation-preview")
        assert special.status_code == 409
        assert special.json()["detail"]["code"] == "skill-target-invalid"
        target.unlink()
    target.write_bytes(prior)

    target.unlink()
    target_dir.rmdir()
    absent_preview = client.get(f"{activation_base}/activation-preview")
    assert absent_preview.status_code == 200
    absent = absent_preview.json()
    first_approval = {
        "expectedStatus": "awaiting-approval",
        "expectedCandidateContentHash": absent["candidateContentHash"],
        "expectedTemplateSha256": absent["templateSha256"],
        "expectedEvaluationSha256": absent["evaluationSha256"],
        "expectedApprovalSha256": absent["approvalSha256"],
        "expectedPriorPresent": False,
        "expectedPriorSha256": None,
        "expectedTargetDirectoryPresent": False,
    }
    monkeypatch.setattr(
        skill_activation_service,
        "_atomic_write",
        fail_atomic_write,
    )
    interrupted_install = client.post(
        f"{activation_base}/approve-and-activate",
        json=first_approval,
        headers={"Idempotency-Key": "activate-skill-first"},
    )
    assert interrupted_install.status_code == 409
    with factory() as session:
        installing = session.scalar(
            select(SkillActivationRecord).where(
                SkillActivationRecord.project_id == project_id,
                SkillActivationRecord.status == "installing",
            )
        )
        assert installing is not None
    monkeypatch.setattr(
        skill_activation_service,
        "_atomic_write",
        original_atomic_write,
    )
    recovered_preview = client.get(f"{activation_base}/activation-preview")
    assert recovered_preview.status_code == 200
    assert recovered_preview.json()["latestActivation"]["status"] == "active"
    assert target.read_bytes() == SKILL_MD.encode()
    first = client.post(
        f"{activation_base}/approve-and-activate",
        json=first_approval,
        headers={"Idempotency-Key": "activate-skill-first"},
    )
    assert first.status_code == 200, first.text
    first_activation = first.json()
    assert first_activation["createdDirectory"] is True
    first_rollback = client.post(
        (f"/v1/projects/{project_id}/skill-activations/{first_activation['id']}/rollback"),
        json={
            "expectedStatus": "active",
            "expectedActivationId": first_activation["id"],
            "expectedApprovalSha256": first_activation["approvalSha256"],
            "expectedInstalledSha256": first_activation["installedSha256"],
            "expectedCurrentTargetSha256": first_activation["installedSha256"],
        },
        headers={"Idempotency-Key": "rollback-skill-first"},
    )
    assert first_rollback.status_code == 200, first_rollback.text
    assert not target.exists()
    assert not target_dir.exists()
    with factory() as session:
        rows = list(
            session.scalars(
                select(SkillActivationRecord).where(SkillActivationRecord.project_id == project_id)
            )
        )
        assert [row.status for row in rows] == ["rolled-back", "rolled-back"]

    replay = client.post(
        endpoint,
        json={
            **payload,
            "episodeId": remembered.json()["verifiedEpisode"]["episodeId"],
            "expectedEpisodeSha256": remembered.json()["verifiedEpisode"]["episodeSha256"],
        },
        headers={"Idempotency-Key": "skill-2"},
    )
    assert replay.status_code == 200
    assert replay.json()["outcome"] == "already-exists"
    assert replay.json()["candidate"]["id"] == candidate["id"]
    listed = client.get(endpoint)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [candidate["id"]]
    with factory() as session:
        stored = session.get_one(SkillCandidateRecord, candidate["id"])
        stored.evaluation_json = {
            **stored.evaluation_json,
            "passed": False,
        }
        session.commit()
    tampered = client.get(f"{endpoint}/{candidate['id']}")
    assert tampered.status_code == 409
    assert tampered.json()["detail"]["code"] == "skill-candidate-integrity-invalid"
    with factory() as session:
        stored = session.get_one(SkillCandidateRecord, candidate["id"])
        stored.evaluation_json = candidate["evaluationJson"]
        session.commit()
    committed_workspace = client.get(
        f"/v1/projects/{project_id}/workflows/{workflow_id}/research-memory-workspace"
    ).json()
    committed_item = next(
        value for value in committed_workspace["items"] if value["id"] == memory["id"]
    )
    invalidated = client.post(
        (
            f"/v1/projects/{project_id}/workflows/{workflow_id}"
            f"/research-memories/{memory['id']}/invalidate"
        ),
        json={
            "expectedContentHash": memory["memorySha256"],
            "expectedStatus": "committed",
            "expectedRevision": memory["revision"],
            "expectedSubjectHeadId": committed_item["subjectHeadId"],
            "expectedSubjectHeadRevision": committed_item["subjectHeadRevision"],
        },
    )
    assert invalidated.status_code == 200
    stale_list = client.get(endpoint)
    assert stale_list.status_code == 409
    assert stale_list.json()["detail"]["code"] == "skill-candidate-origin-stale"


@pytest.mark.parametrize(
    "arguments",
    [
        {
            "evidenceId": "evidence-1",
            "expectedSourceContentHash": "a" * 64,
        },
        {
            "evidenceId": "evidence-1",
            "expectedSourceContentHash": "a" * 64,
            "expectedQuoteHash": "b" * 64,
            "projectId": "project-1",
        },
        {
            "evidenceId": "evidence-1",
            "expectedSourceContentHash": "a" * 64,
            "expectedQuoteHash": "b" * 64,
            "workflowId": "workflow-1",
        },
        {
            "evidenceId": "evidence-1",
            "expectedSourceContentHash": "a" * 64,
            "expectedQuoteHash": "b" * 64,
            "evidenceText": "raw text",
        },
        {
            "evidence_id": "evidence-1",
            "expected_source_content_hash": "a" * 64,
            "expected_quote_hash": "b" * 64,
        },
    ],
)
def test_skill_capability_rejects_missing_extra_scope_raw_text_and_snake_case_keys(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    arguments: dict[str, object],
) -> None:
    factory, _client = memory_store
    with factory() as session:
        before = (
            session.scalar(select(func.count()).select_from(ResearchMemoryRecord)),
            session.scalar(select(func.count()).select_from(EventRecord)),
        )
        with pytest.raises(WorkflowFailure) as captured:
            remember_verified_evidence_capability(
                session,
                execution_project_id="trusted-project",
                execution_workflow_id="trusted-workflow",
                arguments=arguments,
                granted_permissions=frozenset({"project-memory:candidate-write"}),
            )
        session.rollback()
        assert captured.value.code == "skill-capability-input-invalid"
        assert (
            session.scalar(select(func.count()).select_from(ResearchMemoryRecord)),
            session.scalar(select(func.count()).select_from(EventRecord)),
        ) == before


def test_skill_candidate_rejects_uncommitted_or_stale_origin(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
) -> None:
    factory, client = memory_store
    with factory() as session:
        workflow, plan, task = _seed_workflow(session, tmp_path, "skill-stale")
        observation = _observation(
            session,
            workflow,
            plan,
            task,
            "skill-stale",
            status="blocked",
            question=True,
        )
        source, _page, evidence, _memory = _source_evidence_memory(
            session,
            workflow,
            observation,
            "skill-stale",
        )
        session.commit()
        project_id = workflow.project_id
        workflow_id = workflow.id
    remembered = client.post(
        (
            f"/v1/projects/{project_id}/workflows/{workflow_id}"
            "/research-memory-candidates/from-evidence"
        ),
        json={
            "evidenceId": evidence.id,
            "expectedSourceContentHash": source.content_hash,
            "expectedQuoteHash": evidence.quote_hash,
        },
        headers={"Idempotency-Key": "remember-stale"},
    ).json()
    endpoint = f"/v1/projects/{project_id}/workflows/{workflow_id}/skill-candidates"
    rejected = client.post(
        endpoint,
        json={
            "memoryId": remembered["memory"]["id"],
            "expectedMemoryContentHash": remembered["memory"]["memorySha256"],
        },
        headers={"Idempotency-Key": "skill-stale"},
    )
    assert rejected.status_code == 409
    assert client.get(endpoint).json() == []


def test_active_skill_capability_is_structured_inactive_and_zero_write(
    memory_store: tuple[sessionmaker[Session], TypedTestClient],
    tmp_path: Path,
) -> None:
    factory, client = memory_store
    project_id = "project-inactive-capability"
    with factory() as session:
        session.add(
            ProjectRecord(
                id=project_id,
                title="Inactive skill capability",
                description="",
                project_path=str(tmp_path / project_id),
                execution_mode="safe",
            )
        )
        session.commit()
        before = (
            session.scalar(select(func.count()).select_from(ResearchMemoryRecord)),
            session.scalar(select(func.count()).select_from(EventRecord)),
            session.scalar(select(func.count()).select_from(SkillCandidateRecord)),
        )

    endpoint = (
        f"/v1/projects/{project_id}/active-skill-capabilities/remember-verified-evidence/invoke"
    )
    response = client.post(
        endpoint,
        json={
            "evidenceId": "evidence-1",
            "expectedSourceContentHash": "a" * 64,
            "expectedQuoteHash": "b" * 64,
        },
        headers={"Idempotency-Key": "inactive-capability-test"},
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "skill-capability-not-active",
        "userMessage": "This project has no active verified-evidence skill capability.",
        "retryable": False,
    }
    extra = client.post(
        endpoint,
        json={
            "evidenceId": "evidence-1",
            "expectedSourceContentHash": "a" * 64,
            "expectedQuoteHash": "b" * 64,
            "workflowId": "forbidden",
        },
        headers={"Idempotency-Key": "inactive-capability-extra"},
    )
    assert extra.status_code == 422
    missing = client.post(
        ("/v1/projects/missing/active-skill-capabilities/remember-verified-evidence/invoke"),
        json={
            "evidenceId": "evidence-1",
            "expectedSourceContentHash": "a" * 64,
            "expectedQuoteHash": "b" * 64,
        },
        headers={"Idempotency-Key": "inactive-capability-missing"},
    )
    assert missing.status_code == 404

    with factory() as session:
        after = (
            session.scalar(select(func.count()).select_from(ResearchMemoryRecord)),
            session.scalar(select(func.count()).select_from(EventRecord)),
            session.scalar(select(func.count()).select_from(SkillCandidateRecord)),
        )
    assert after == before
