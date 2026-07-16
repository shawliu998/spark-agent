from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Generator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session, sessionmaker

import open_science_core._analysis_service.execution as execution_module
import open_science_core._analysis_service.filesystem as filesystem_module
import open_science_core.analysis as analysis_module
from open_science_core.analysis import RuntimeExecutionResult
from open_science_core.analysis_service import execute_workflow_analysis_intent
from open_science_core.api import agent_runs as agent_runs_api
from open_science_core.api.agent_runs import (
    get_agent_session,
)
from open_science_core.api.agent_runs import (
    router as agent_run_router,
)
from open_science_core.api.workflows import (
    get_workflow_session,
)
from open_science_core.api.workflows import (
    router as workflow_router,
)
from open_science_core.config import settings
from open_science_core.db import Base
from open_science_core.models import (
    AnalysisIntentRecord,
    AnalysisSpecRecord,
    ApprovalRecord,
    ArtifactRecord,
    EventRecord,
    IntentDecisionRecord,
    InteractionRequestRecord,
    JobRecord,
    ModelInvocationRecord,
    PlanRecord,
    ProjectRecord,
    SourcePageRecord,
    SourceRecord,
    StructuredAnalysisResultRecord,
    UserResponseRecord,
    WorkflowRecord,
    utc_now,
)
from open_science_core.schemas import AnalysisRunOut
from open_science_core.workflow import agent_service, handlers
from open_science_core.workflow.intent_router import (
    INTENT_ROUTER_PROMPT_VERSION,
    IntentSource,
    SourceKind,
    intent_router_input_sha256,
)
from open_science_core.workflow.state import WorkflowFailure
from open_science_core.workflow.worker import WorkflowWorker
from runtime_attestation import write_attested_runtime_result


class _RequestClient(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Response: ...


class TypedTestClient(TestClient):
    def get(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("POST", url, **kwargs)


class _CountingIntentGateway:
    def __init__(
        self,
        *,
        destination: str,
        selected_source_id: str | None = None,
    ) -> None:
        self.call_count = 0
        self._selected_source_id = selected_source_id
        self._endpoint_identity = (
            "sha256:" + hashlib.sha256(destination.encode("utf-8")).hexdigest()
        )
        self._endpoint_host = f"{destination}.example.test"

    @property
    def configured(self) -> bool:
        return True

    @property
    def default_model(self) -> str:
        return "intent-router-test-model"

    @property
    def endpoint_host(self) -> str:
        return self._endpoint_host

    @property
    def endpoint_identity(self) -> str:
        return self._endpoint_identity

    async def complete_json(
        self,
        _system_prompt: str,
        _user_prompt: str,
        _model: str | None = None,
    ) -> dict[str, Any]:
        self.call_count += 1
        assert self._selected_source_id is not None
        return {
            "intent": "literature-synthesis",
            "confidence": 0.99,
            "reasoningSummary": "The selected PDF supports literature synthesis.",
            "selectedSourceIds": [self._selected_source_id],
            "missingInputs": [],
            "proposedWorkflowType": "literature-synthesis",
        }


class _SequencedIntentGateway(_CountingIntentGateway):
    """Requests clarification twice before resolving the selected PDF."""

    async def complete_json(
        self,
        _system_prompt: str,
        _user_prompt: str,
        _model: str | None = None,
    ) -> dict[str, Any]:
        self.call_count += 1
        assert self._selected_source_id is not None
        if self.call_count <= 2:
            return {
                "intent": "clarification-required",
                "confidence": 0.99,
                "reasoningSummary": "The research path still needs confirmation.",
                "selectedSourceIds": [self._selected_source_id],
                "missingInputs": ["confirm-research-path"],
                "proposedWorkflowType": None,
            }
        return {
            "intent": "literature-synthesis",
            "confidence": 0.99,
            "reasoningSummary": "The confirmed PDF path supports literature synthesis.",
            "selectedSourceIds": [self._selected_source_id],
            "missingInputs": [],
            "proposedWorkflowType": "literature-synthesis",
        }


class _MixedIntentGateway(_CountingIntentGateway):
    def __init__(self, *, destination: str, source_ids: list[str]) -> None:
        super().__init__(destination=destination)
        self._source_ids = source_ids

    async def complete_json(
        self,
        _system_prompt: str,
        _user_prompt: str,
        _model: str | None = None,
    ) -> dict[str, Any]:
        self.call_count += 1
        return {
            "intent": "mixed-research",
            "confidence": 0.99,
            "reasoningSummary": "The goal explicitly combines literature and dataset inputs.",
            "selectedSourceIds": self._source_ids,
            "missingInputs": [],
            "proposedWorkflowType": "mixed-research",
        }


class _DatasetMethodGateway(_CountingIntentGateway):
    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        _model: str | None = None,
    ) -> dict[str, Any]:
        self.call_count += 1
        assert self._selected_source_id is not None
        if "bounded scientific analysis method selector" not in system_prompt:
            return {
                "intent": "dataset-analysis",
                "confidence": 0.99,
                "reasoningSummary": "The selected CSV supports dataset analysis.",
                "selectedSourceIds": [self._selected_source_id],
                "missingInputs": [],
                "proposedWorkflowType": "dataset-analysis",
            }
        payload = cast(dict[str, Any], json.loads(user_prompt))
        untrusted = cast(dict[str, Any], payload["untrustedData"])
        identity = cast(dict[str, str], untrusted["datasetIdentity"])
        return {
            "confidence": 0.99,
            "decision": {
                "schemaVersion": "1",
                "objective": untrusted["goal"],
                "datasetSourceId": identity["datasetSourceId"],
                "datasetContentHash": identity["datasetContentHash"],
                "datasetProfileHash": identity["datasetProfileHash"],
                "operation": {
                    "type": "descriptive",
                    "columns": ["outcome"],
                    "statistics": ["count", "missing", "mean", "std"],
                    "plot": "histogram",
                },
                "missingValuePolicy": "drop-per-operation",
                "confidenceLevel": 0.95,
                "randomSeed": 7,
                "assumptions": [],
                "limitations": [
                    "Descriptive summaries do not test inferential hypotheses."
                ],
            },
        }


def _install_remote_gateway(
    monkeypatch: pytest.MonkeyPatch,
    gateway: _CountingIntentGateway,
) -> None:
    monkeypatch.setattr(agent_runs_api, "model_gateway", gateway)
    monkeypatch.setattr(handlers, "model_gateway", gateway)


@dataclass(frozen=True)
class AgentRunEnvironment:
    session_factory: sessionmaker[Session]
    project_roots: dict[str, Path]
    worker: WorkflowWorker


@pytest.fixture
def agent_run_environment(tmp_path: Path) -> Generator[AgentRunEnvironment, None, None]:
    database_path = tmp_path / "agent-runs.sqlite3"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )

    def configure_sqlite(dbapi_connection: DBAPIConnection, _record: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    event.listen(engine, "connect", configure_sqlite)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    project_roots = {
        "project-1": tmp_path / "project-1",
        "project-2": tmp_path / "project-2",
    }
    for root in project_roots.values():
        (root / "data" / "raw").mkdir(parents=True)
        (root / "sources").mkdir(parents=True)
        (root / "runs").mkdir(parents=True)
    with session_factory.begin() as session:
        for project_id, root in project_roots.items():
            session.add(
                ProjectRecord(
                    id=project_id,
                    title=f"Autonomous run fixture {project_id}",
                    description="",
                    project_path=str(root),
                    execution_mode="safe",
                )
            )
    worker = WorkflowWorker(
        session_factory,
        poll_interval_seconds=0.01,
        lease_seconds=1.0,
        heartbeat_seconds=0.1,
    )
    yield AgentRunEnvironment(
        session_factory=session_factory,
        project_roots=project_roots,
        worker=worker,
    )
    engine.dispose()


def _new_client(session_factory: sessionmaker[Session]) -> TypedTestClient:
    api = FastAPI()
    api.include_router(agent_run_router)
    api.include_router(workflow_router)

    def session_dependency() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    api.dependency_overrides[get_agent_session] = session_dependency
    api.dependency_overrides[get_workflow_session] = session_dependency
    return TypedTestClient(api)


@pytest.fixture
def agent_run_client(
    agent_run_environment: AgentRunEnvironment,
) -> Generator[TypedTestClient, None, None]:
    with _new_client(agent_run_environment.session_factory) as client:
        yield client


def _run_once(worker: WorkflowWorker) -> bool:
    return asyncio.run(worker.run_once())


def _add_pdf_source(
    environment: AgentRunEnvironment,
    *,
    source_id: str = "paper-1",
    project_id: str = "project-1",
) -> str:
    root = environment.project_roots[project_id]
    path = root / "sources" / f"{source_id}.pdf"
    content = f"%PDF-1.7 autonomous-agent-run-{source_id}".encode()
    path.write_bytes(content)
    content_hash = hashlib.sha256(content).hexdigest()
    passage = "Local evidence supports a bounded literature research workflow."
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
    with environment.session_factory.begin() as session:
        session.add(
            SourceRecord(
                id=source_id,
                project_id=project_id,
                title=f"Paper {source_id}",
                source_kind="pdf",
                authors=[],
                local_path=str(path),
                ingestion_status="ready",
                content_hash=content_hash,
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
    return source_id


def _add_dataset_source(
    environment: AgentRunEnvironment,
    *,
    source_id: str = "dataset-1",
    project_id: str = "project-1",
    ambiguous_group_columns: bool = False,
) -> str:
    root = environment.project_roots[project_id]
    path = root / "data" / "raw" / f"{source_id}.csv"
    content = (
        (
            "group,cohort,outcome,source\n"
            f"control,a,1,{source_id}\n"
            f"treated,b,3,{source_id}\n"
            f"control,a,2,{source_id}\n"
            f"treated,b,4,{source_id}\n"
            f"control,a,5,{source_id}\n"
            f"treated,b,6,{source_id}\n"
        )
        if ambiguous_group_columns
        else (
            "group,outcome,source\n"
            f"control,1,{source_id}\n"
            f"treated,3,{source_id}\n"
            f"control,2,{source_id}\n"
            f"treated,4,{source_id}\n"
            f"control,5,{source_id}\n"
            f"treated,6,{source_id}\n"
        )
    ).encode()
    path.write_bytes(content)
    path.chmod(0o444)
    with environment.session_factory.begin() as session:
        session.add(
            SourceRecord(
                id=source_id,
                project_id=project_id,
                title=f"Dataset {source_id}",
                source_kind="dataset",
                authors=[],
                local_path=str(path),
                ingestion_status="ready",
                content_hash=hashlib.sha256(content).hexdigest(),
            )
        )
    return source_id


def _create_agent_run(
    client: TypedTestClient,
    *,
    goal: str,
    source_ids: list[str],
    idempotency_key: str,
    project_id: str = "project-1",
    remote_data_approved: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "goal": goal,
        "sourceIds": source_ids,
        "mode": "autonomous",
    }
    if remote_data_approved:
        payload["remoteDataApproved"] = True
    response = client.post(
        f"/v1/projects/{project_id}/agent-runs",
        headers={"Idempotency-Key": idempotency_key},
        json=payload,
    )
    assert response.status_code == 202, response.text
    return cast(dict[str, Any], response.json())


def test_create_list_get_and_create_idempotency(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
) -> None:
    paper_id = _add_pdf_source(agent_run_environment)
    payload = {
        "goal": "Summarize the selected paper evidence.",
        "sourceIds": [paper_id],
        "mode": "autonomous",
    }
    headers = {"Idempotency-Key": "agent-create-idempotency-0001"}

    first = agent_run_client.post(
        "/v1/projects/project-1/agent-runs",
        headers=headers,
        json=payload,
    )
    replay = agent_run_client.post(
        "/v1/projects/project-1/agent-runs",
        headers=headers,
        json=payload,
    )

    assert first.status_code == 202, first.text
    assert replay.status_code == 202, replay.text
    first_snapshot = first.json()
    assert replay.json() == first_snapshot
    workflow_id = first_snapshot["workflow"]["id"]
    assert first_snapshot["workflow"] == {
        **first_snapshot["workflow"],
        "workflowType": None,
        "status": "routing",
        "sourceIds": [paper_id],
        "mode": "autonomous",
    }
    assert first_snapshot["intentDecision"] is None
    assert first_snapshot["allowedActions"] == ["cancel"]

    listed = agent_run_client.get("/v1/projects/project-1/agent-runs?activeOnly=true&limit=10")
    fetched = agent_run_client.get(f"/v1/agent-runs/{workflow_id}")
    assert listed.status_code == 200, listed.text
    assert [item["workflow"]["id"] for item in listed.json()] == [workflow_id]
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == first_snapshot

    changed_payload = dict(payload)
    changed_payload["goal"] = "Use the same key for a different request."
    conflict = agent_run_client.post(
        "/v1/projects/project-1/agent-runs",
        headers=headers,
        json=changed_payload,
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["detail"]["code"] == "idempotency-key-reused"

    with agent_run_environment.session_factory() as session:
        count = session.scalar(select(func.count()).select_from(WorkflowRecord))
        queued_routes = session.scalar(
            select(func.count())
            .select_from(JobRecord)
            .where(JobRecord.kind == "route-intent", JobRecord.status == "queued")
        )
    assert count == 1
    assert queued_routes == 1


def test_legacy_workflow_api_isolates_lists_and_returns_agent_mutation_snapshots(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
) -> None:
    paper_id = _add_pdf_source(agent_run_environment)
    started = _create_agent_run(
        agent_run_client,
        goal="Summarize the selected paper evidence.",
        source_ids=[paper_id],
        idempotency_key="agent-legacy-api-boundary-0001",
    )
    workflow_id = started["workflow"]["id"]

    legacy_list = agent_run_client.get("/v1/projects/project-1/workflows")
    legacy_get = agent_run_client.get(f"/v1/workflows/{workflow_id}")
    cancelled = agent_run_client.post(
        f"/v1/workflows/{workflow_id}/cancel",
        json={"expectedWorkflowRevision": started["workflow"]["revision"]},
    )

    assert legacy_list.status_code == 200, legacy_list.text
    assert legacy_list.json() == []
    assert legacy_get.status_code == 200, legacy_get.text
    assert legacy_get.json() == started
    assert cancelled.status_code == 202, cancelled.text
    cancelled_snapshot = cancelled.json()
    assert cancelled_snapshot["workflow"]["mode"] == "autonomous"
    assert cancelled_snapshot["workflow"]["status"] == "cancelled"
    assert cancelled_snapshot["workflow"]["workflowType"] is None
    assert cancelled_snapshot["intentDecision"] is None
    assert cancelled_snapshot["allowedActions"] == []


def test_create_rejects_source_owned_by_another_project(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
) -> None:
    foreign_paper_id = _add_pdf_source(
        agent_run_environment,
        source_id="foreign-paper",
        project_id="project-2",
    )

    response = agent_run_client.post(
        "/v1/projects/project-1/agent-runs",
        headers={"Idempotency-Key": "agent-source-ownership-0001"},
        json={
            "goal": "Review a source that does not belong to this project.",
            "sourceIds": [foreign_paper_id],
            "mode": "autonomous",
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "source-not-found"
    with agent_run_environment.session_factory() as session:
        count = session.scalar(select(func.count()).select_from(WorkflowRecord))
    assert count == 0


def test_no_source_routes_to_durable_clarification_and_event_timeline(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
) -> None:
    started = _create_agent_run(
        agent_run_client,
        goal="Help me investigate this research question.",
        source_ids=[],
        idempotency_key="agent-no-source-0001",
    )
    workflow_id = started["workflow"]["id"]

    assert _run_once(agent_run_environment.worker)
    snapshot_response = agent_run_client.get(f"/v1/agent-runs/{workflow_id}")
    interactions_response = agent_run_client.get(f"/v1/workflows/{workflow_id}/interactions")
    events_response = agent_run_client.get(f"/v1/workflows/{workflow_id}/events")

    assert snapshot_response.status_code == 200, snapshot_response.text
    snapshot = snapshot_response.json()
    assert snapshot["workflow"]["status"] == "waiting-clarification"
    assert snapshot["workflow"]["workflowType"] is None
    assert snapshot["intentDecision"]["intent"] == "clarification-required"
    assert snapshot["intentDecision"]["missingInputs"] == ["select-ready-supported-source"]
    assert snapshot["allowedActions"] == ["respond-interaction", "cancel"]
    assert interactions_response.status_code == 200, interactions_response.text
    interactions = interactions_response.json()
    assert len(interactions) == 1
    assert interactions[0]["requestType"] == "text"
    assert interactions[0]["status"] == "pending"
    assert interactions[0]["latestResponse"] is None

    assert events_response.status_code == 200, events_response.text
    events = events_response.json()["events"]
    sequences = [item["sequence"] for item in events]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    event_types = [item["type"] for item in events]
    assert event_types[0] == "agent-run.created"
    assert "intent.decision-recorded" in event_types
    assert "workflow.status-changed" in event_types
    assert event_types[-1] == "interaction.requested"
    assert events_response.json()["nextAfter"] == sequences[-1]
    assert events_response.json()["hasMore"] is False

    paged = agent_run_client.get(f"/v1/workflows/{workflow_id}/events?after={sequences[0]}&limit=2")
    assert paged.status_code == 200, paged.text
    assert all(item["sequence"] > sequences[0] for item in paged.json()["events"])
    assert len(paged.json()["events"]) == 2
    assert paged.json()["hasMore"] is True

    cancelled = agent_run_client.post(
        f"/v1/workflows/{workflow_id}/cancel",
        json={"expectedWorkflowRevision": snapshot["workflow"]["revision"]},
    )
    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["workflow"]["status"] == "cancelled"
    cancelled_interactions = agent_run_client.get(
        f"/v1/workflows/{workflow_id}/interactions"
    )
    assert cancelled_interactions.status_code == 200, cancelled_interactions.text
    assert cancelled_interactions.json()[0]["status"] == "cancelled"


def test_interaction_response_is_persisted_cas_idempotent_and_reloadable(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
) -> None:
    first_dataset = _add_dataset_source(
        agent_run_environment,
        source_id="dataset-response-first",
    )
    second_dataset = _add_dataset_source(
        agent_run_environment,
        source_id="dataset-response-second",
    )
    _add_dataset_source(
        agent_run_environment,
        source_id="dataset-not-authorized-for-run",
    )
    started = _create_agent_run(
        agent_run_client,
        goal="Analyze one of the selected datasets.",
        source_ids=[first_dataset, second_dataset],
        idempotency_key="agent-response-create-0001",
    )
    workflow_id = started["workflow"]["id"]
    assert _run_once(agent_run_environment.worker)
    waiting = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    interaction = waiting["interactions"][0]
    assert waiting["intentDecision"]["missingInputs"] == [
        "select-exactly-one-ready-dataset"
    ]
    assert interaction["requestType"] == "single-choice"
    assert [option["value"] for option in interaction["options"]] == [
        first_dataset,
        second_dataset,
    ]
    expected_revision = waiting["workflow"]["revision"]
    response_payload = {
        "response": first_dataset,
        "expectedWorkflowRevision": expected_revision,
    }

    stale = agent_run_client.post(
        f"/v1/interactions/{interaction['id']}/respond",
        headers={"Idempotency-Key": "agent-response-stale-0001"},
        json={
            "response": first_dataset,
            "expectedWorkflowRevision": expected_revision + 1,
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "workflow-revision-conflict"

    headers = {"Idempotency-Key": "agent-response-idempotency-0001"}
    answered = agent_run_client.post(
        f"/v1/interactions/{interaction['id']}/respond",
        headers=headers,
        json=response_payload,
    )
    replay = agent_run_client.post(
        f"/v1/interactions/{interaction['id']}/respond",
        headers=headers,
        json=response_payload,
    )
    assert answered.status_code == 202, answered.text
    assert replay.status_code == 202, replay.text
    assert replay.json() == answered.json()
    answered_snapshot = answered.json()
    assert answered_snapshot["workflow"]["status"] == "routing"
    assert answered_snapshot["workflow"]["sourceIds"] == [first_dataset]
    assert answered_snapshot["interactions"][0]["status"] == "answered"
    latest_response = answered_snapshot["interactions"][0]["latestResponse"]
    assert latest_response["revision"] == 1
    assert latest_response["response"] == first_dataset
    assert len(latest_response["responseSha256"]) == 64

    changed_replay = agent_run_client.post(
        f"/v1/interactions/{interaction['id']}/respond",
        headers=headers,
        json={
            "response": first_dataset,
            "expectedWorkflowRevision": answered_snapshot["workflow"]["revision"],
        },
    )
    assert changed_replay.status_code == 409, changed_replay.text
    assert changed_replay.json()["detail"]["code"] == "idempotency-key-reused"

    with agent_run_environment.session_factory() as session:
        response_count = session.scalar(select(func.count()).select_from(UserResponseRecord))
        stored_interaction = session.get(InteractionRequestRecord, interaction["id"])
    assert response_count == 1
    assert stored_interaction is not None
    assert stored_interaction.status == "answered"

    with _new_client(agent_run_environment.session_factory) as reloaded_client:
        reloaded = reloaded_client.get(f"/v1/agent-runs/{workflow_id}")
        reloaded_interactions = reloaded_client.get(f"/v1/workflows/{workflow_id}/interactions")
        assert reloaded.status_code == 200, reloaded.text
        assert reloaded.json() == answered_snapshot
        assert reloaded_interactions.status_code == 200, reloaded_interactions.text
        assert reloaded_interactions.json()[0]["latestResponse"] == latest_response

    restarted_worker = WorkflowWorker(
        agent_run_environment.session_factory,
        poll_interval_seconds=0.01,
        lease_seconds=1.0,
        heartbeat_seconds=0.1,
    )
    restarted_worker.recover()
    assert _run_once(restarted_worker)
    with _new_client(agent_run_environment.session_factory) as reloaded_client:
        resolved = reloaded_client.get(f"/v1/agent-runs/{workflow_id}")
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["workflow"]["status"] == "planning"
        assert resolved.json()["workflow"]["workflowType"] == "dataset-analysis"
        assert resolved.json()["intentDecision"]["intent"] == "dataset-analysis"


def test_answer_change_before_approval_supersedes_plan_and_creates_next_version(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
) -> None:
    first_dataset = _add_dataset_source(
        agent_run_environment,
        source_id="dataset-first",
    )
    second_dataset = _add_dataset_source(
        agent_run_environment,
        source_id="dataset-second",
    )
    started = _create_agent_run(
        agent_run_client,
        goal="Summarize one selected project dataset with descriptive statistics.",
        source_ids=[first_dataset, second_dataset],
        idempotency_key="agent-answer-change-create-0001",
    )
    workflow_id = started["workflow"]["id"]
    assert _run_once(agent_run_environment.worker)
    waiting = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    interaction = waiting["interactions"][0]

    first_answer = agent_run_client.post(
        f"/v1/interactions/{interaction['id']}/respond",
        headers={"Idempotency-Key": "agent-answer-change-first-0001"},
        json={
            "response": first_dataset,
            "expectedWorkflowRevision": waiting["workflow"]["revision"],
        },
    )
    assert first_answer.status_code == 202, first_answer.text
    assert _run_once(agent_run_environment.worker)
    assert _run_once(agent_run_environment.worker)
    first_plan = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    assert first_plan["workflow"]["status"] == "waiting-plan-approval"
    assert first_plan["workflow"]["planVersion"] == 1
    assert first_plan["plan"]["version"] == 1
    assert "respond-interaction" in first_plan["allowedActions"]

    changed_answer = agent_run_client.post(
        f"/v1/interactions/{interaction['id']}/respond",
        headers={"Idempotency-Key": "agent-answer-change-second-0001"},
        json={
            "response": second_dataset,
            "expectedWorkflowRevision": first_plan["workflow"]["revision"],
        },
    )
    assert changed_answer.status_code == 202, changed_answer.text
    changed_snapshot = changed_answer.json()
    assert changed_snapshot["workflow"]["status"] == "routing"
    assert changed_snapshot["workflow"]["sourceIds"] == [second_dataset]
    assert changed_snapshot["plan"] is None
    assert changed_snapshot["interactions"][0]["latestResponse"]["revision"] == 2

    assert _run_once(agent_run_environment.worker)
    assert _run_once(agent_run_environment.worker)
    second_plan = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    assert second_plan["workflow"]["status"] == "waiting-plan-approval"
    assert second_plan["workflow"]["planVersion"] == 2
    assert second_plan["plan"]["version"] == 2
    assert second_plan["intentDecision"]["selectedSourceIds"] == [second_dataset]

    with agent_run_environment.session_factory() as session:
        plan_jobs = list(
            session.scalars(
                select(JobRecord)
                .where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "generate-plan",
                )
                .order_by(JobRecord.created_at)
            )
        )
    assert [job.operation_key for job in plan_jobs] == [
        f"workflow:{workflow_id}:plan:1",
        f"workflow:{workflow_id}:plan:2",
    ]
    assert all(job.status == "succeeded" for job in plan_jobs)


def test_answer_change_while_first_plan_job_is_queued_reserves_next_plan_version(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
) -> None:
    first_dataset = _add_dataset_source(
        agent_run_environment,
        source_id="dataset-queued-first",
    )
    second_dataset = _add_dataset_source(
        agent_run_environment,
        source_id="dataset-queued-second",
    )
    started = _create_agent_run(
        agent_run_client,
        goal="Summarize exactly one selected dataset with descriptive statistics.",
        source_ids=[first_dataset, second_dataset],
        idempotency_key="agent-queued-plan-create-0001",
    )
    workflow_id = started["workflow"]["id"]
    assert _run_once(agent_run_environment.worker)
    waiting = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    interaction = waiting["interactions"][0]

    first_answer = agent_run_client.post(
        f"/v1/interactions/{interaction['id']}/respond",
        headers={"Idempotency-Key": "agent-queued-plan-first-0001"},
        json={
            "response": first_dataset,
            "expectedWorkflowRevision": waiting["workflow"]["revision"],
        },
    )
    assert first_answer.status_code == 202, first_answer.text
    assert _run_once(agent_run_environment.worker)
    first_planning = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    assert first_planning["workflow"]["status"] == "planning"
    assert first_planning["plan"] is None

    changed_answer = agent_run_client.post(
        f"/v1/interactions/{interaction['id']}/respond",
        headers={"Idempotency-Key": "agent-queued-plan-second-0001"},
        json={
            "response": second_dataset,
            "expectedWorkflowRevision": first_planning["workflow"]["revision"],
        },
    )
    assert changed_answer.status_code == 202, changed_answer.text
    assert changed_answer.json()["workflow"]["status"] == "routing"

    assert _run_once(agent_run_environment.worker)
    assert _run_once(agent_run_environment.worker)
    resolved = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    assert resolved["workflow"]["status"] == "waiting-plan-approval"
    assert resolved["workflow"]["planVersion"] == 2
    assert resolved["plan"]["version"] == 2
    assert resolved["intentDecision"]["selectedSourceIds"] == [second_dataset]

    with agent_run_environment.session_factory() as session:
        plan_jobs = list(
            session.scalars(
                select(JobRecord)
                .where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "generate-plan",
                )
                .order_by(JobRecord.operation_key)
            )
        )
    assert [(job.operation_key, job.status) for job in plan_jobs] == [
        (f"workflow:{workflow_id}:plan:1", "cancelled"),
        (f"workflow:{workflow_id}:plan:2", "succeeded"),
    ]


def test_repeated_answer_change_while_replacement_plan_is_queued_skips_history(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
) -> None:
    first_dataset = _add_dataset_source(
        agent_run_environment,
        source_id="dataset-repeated-first",
    )
    second_dataset = _add_dataset_source(
        agent_run_environment,
        source_id="dataset-repeated-second",
    )
    started = _create_agent_run(
        agent_run_client,
        goal=(
            "Summarize exactly one selected dataset with descriptive statistics "
            "and allow source correction."
        ),
        source_ids=[first_dataset, second_dataset],
        idempotency_key="agent-repeated-plan-create-0001",
    )
    workflow_id = started["workflow"]["id"]
    assert _run_once(agent_run_environment.worker)
    waiting = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    interaction = waiting["interactions"][0]

    first_answer = agent_run_client.post(
        f"/v1/interactions/{interaction['id']}/respond",
        headers={"Idempotency-Key": "agent-repeated-plan-first-0001"},
        json={
            "response": first_dataset,
            "expectedWorkflowRevision": waiting["workflow"]["revision"],
        },
    )
    assert first_answer.status_code == 202, first_answer.text
    assert _run_once(agent_run_environment.worker)
    assert _run_once(agent_run_environment.worker)
    first_plan = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    assert first_plan["workflow"]["planVersion"] == 1

    second_answer = agent_run_client.post(
        f"/v1/interactions/{interaction['id']}/respond",
        headers={"Idempotency-Key": "agent-repeated-plan-second-0001"},
        json={
            "response": second_dataset,
            "expectedWorkflowRevision": first_plan["workflow"]["revision"],
        },
    )
    assert second_answer.status_code == 202, second_answer.text
    assert _run_once(agent_run_environment.worker)
    replacement_planning = agent_run_client.get(
        f"/v1/agent-runs/{workflow_id}"
    ).json()
    assert replacement_planning["workflow"]["status"] == "planning"
    assert replacement_planning["plan"] is None

    third_answer = agent_run_client.post(
        f"/v1/interactions/{interaction['id']}/respond",
        headers={"Idempotency-Key": "agent-repeated-plan-third-0001"},
        json={
            "response": first_dataset,
            "expectedWorkflowRevision": replacement_planning["workflow"]["revision"],
        },
    )
    assert third_answer.status_code == 202, third_answer.text
    assert third_answer.json()["workflow"]["status"] == "routing"

    assert _run_once(agent_run_environment.worker)
    assert _run_once(agent_run_environment.worker)
    final_plan = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    assert final_plan["workflow"]["status"] == "waiting-plan-approval"
    assert final_plan["workflow"]["planVersion"] == 3
    assert final_plan["plan"]["version"] == 3
    assert final_plan["intentDecision"]["selectedSourceIds"] == [first_dataset]

    with agent_run_environment.session_factory() as session:
        plan_jobs = list(
            session.scalars(
                select(JobRecord)
                .where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "generate-plan",
                )
                .order_by(JobRecord.operation_key)
            )
        )
    assert [(job.operation_key, job.status) for job in plan_jobs] == [
        (f"workflow:{workflow_id}:plan:1", "succeeded"),
        (f"workflow:{workflow_id}:plan:2", "cancelled"),
        (f"workflow:{workflow_id}:plan:3", "succeeded"),
    ]


@pytest.mark.parametrize(
    ("source_kind", "expected_workflow_type"),
    [
        ("pdf", "literature-synthesis"),
        ("dataset", "dataset-analysis"),
    ],
)
def test_single_source_kind_is_recognized_and_resolved(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
    source_kind: str,
    expected_workflow_type: str,
) -> None:
    source_id = (
        _add_pdf_source(agent_run_environment)
        if source_kind == "pdf"
        else _add_dataset_source(agent_run_environment)
    )
    started = _create_agent_run(
        agent_run_client,
        goal=(
            "Synthesize the selected paper evidence."
            if source_kind == "pdf"
            else "Summarize outcomes in the selected dataset."
        ),
        source_ids=[source_id],
        idempotency_key=f"agent-route-{source_kind}-0001",
    )

    assert _run_once(agent_run_environment.worker)
    response = agent_run_client.get(f"/v1/agent-runs/{started['workflow']['id']}")

    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["workflow"]["status"] == "planning"
    assert snapshot["workflow"]["workflowType"] == expected_workflow_type
    assert snapshot["intentDecision"]["intent"] == expected_workflow_type
    assert snapshot["intentDecision"]["proposedWorkflowType"] == expected_workflow_type
    assert snapshot["intentDecision"]["selectedSourceIds"] == [source_id]
    assert snapshot["intentDecision"]["confidence"] == 1.0
    with agent_run_environment.session_factory() as session:
        workflow = session.get(WorkflowRecord, started["workflow"]["id"])
        generate_plan_jobs = session.scalar(
            select(func.count())
            .select_from(JobRecord)
            .where(
                JobRecord.workflow_id == started["workflow"]["id"],
                JobRecord.kind == "generate-plan",
                JobRecord.status == "queued",
            )
        )
    assert workflow is not None
    assert generate_plan_jobs == 1
    if source_kind == "dataset":
        assert workflow.dataset_source_id == source_id
        assert workflow.dataset_content_hash is not None
    else:
        assert workflow.dataset_source_id is None
        assert workflow.dataset_content_hash is None


def test_scientific_clarification_restarts_planning_and_binds_analysis_spec(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id = _add_dataset_source(
        agent_run_environment,
        source_id="dataset-scientific-clarification",
        ambiguous_group_columns=True,
    )
    started = _create_agent_run(
        agent_run_client,
        goal="Compare outcome between two independent populations.",
        source_ids=[dataset_id],
        idempotency_key="agent-scientific-create-0001",
    )
    workflow_id = started["workflow"]["id"]

    assert _run_once(agent_run_environment.worker)
    assert _run_once(agent_run_environment.worker)
    waiting = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    assert waiting["workflow"]["status"] == "planning"
    assert waiting["plan"] is None
    assert waiting["allowedActions"] == ["respond-interaction", "cancel"]
    interaction = waiting["interactions"][0]
    assert interaction["stepId"] == "select-analysis-method"
    assert interaction["requestType"] == "column-selection"
    assert interaction["responseSchema"]["clarificationType"] == "group-column"
    assert [option["value"] for option in interaction["options"]] == [
        "group",
        "cohort",
    ]

    restarted_worker = WorkflowWorker(
        agent_run_environment.session_factory,
        poll_interval_seconds=0.01,
        lease_seconds=1.0,
        heartbeat_seconds=0.1,
    )
    restarted_worker.recover()
    reloaded = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    assert reloaded["interactions"][0]["id"] == interaction["id"]

    response_payload = {
        "response": "group",
        "expectedWorkflowRevision": waiting["workflow"]["revision"],
    }
    answered = agent_run_client.post(
        f"/v1/interactions/{interaction['id']}/respond",
        headers={"Idempotency-Key": "agent-scientific-answer-0001"},
        json=response_payload,
    )
    assert answered.status_code == 202, answered.text
    answered_snapshot = answered.json()
    assert answered_snapshot["workflow"]["status"] == "planning"
    assert answered_snapshot["workflow"]["workflowType"] == "dataset-analysis"

    replay = agent_run_client.post(
        f"/v1/interactions/{interaction['id']}/respond",
        headers={"Idempotency-Key": "agent-scientific-answer-0001"},
        json=response_payload,
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["workflow"]["revision"] == answered_snapshot["workflow"]["revision"]

    stale = agent_run_client.post(
        f"/v1/interactions/{interaction['id']}/respond",
        headers={"Idempotency-Key": "agent-scientific-answer-stale-0001"},
        json=response_payload,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "workflow-revision-conflict"

    assert _run_once(restarted_worker)
    planned = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    assert planned["workflow"]["status"] == "waiting-plan-approval"
    assert planned["plan"]["spec"]["analysisSpecId"] is not None
    assert planned["plan"]["spec"]["analysisSpecSha256"] is not None

    with agent_run_environment.session_factory() as session:
        analysis_spec = session.scalar(
            select(AnalysisSpecRecord).where(
                AnalysisSpecRecord.workflow_id == workflow_id
            )
        )
        plan = session.scalar(
            select(PlanRecord).where(PlanRecord.workflow_id == workflow_id)
        )
        route_jobs = session.scalar(
            select(func.count())
            .select_from(JobRecord)
            .where(
                JobRecord.workflow_id == workflow_id,
                JobRecord.kind == "route-intent",
            )
        )
    assert analysis_spec is not None
    assert analysis_spec.revision == 1
    assert analysis_spec.selector_kind == "local-deterministic"
    assert analysis_spec.dataset_profile_sha256 == analysis_spec.spec_json[
        "datasetProfileHash"
    ]
    assert plan is not None
    assert plan.spec_json["analysisSpecId"] == analysis_spec.id
    assert plan.spec_json["analysisSpecSha256"] == analysis_spec.spec_sha256
    assert route_jobs == 1

    changed = agent_run_client.post(
        f"/v1/interactions/{interaction['id']}/respond",
        headers={"Idempotency-Key": "agent-scientific-answer-change-0001"},
        json={
            "response": "cohort",
            "expectedWorkflowRevision": planned["workflow"]["revision"],
        },
    )
    assert changed.status_code == 202, changed.text
    assert changed.json()["workflow"]["status"] == "planning"
    assert changed.json()["plan"] is None
    assert changed.json()["analysisSpec"] is None
    with agent_run_environment.session_factory() as session:
        superseded_immediately = session.get(AnalysisSpecRecord, analysis_spec.id)
        assert superseded_immediately is not None
        assert superseded_immediately.status == "superseded"

    assert _run_once(restarted_worker)
    revised = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    assert revised["workflow"]["status"] == "waiting-plan-approval"
    assert revised["workflow"]["planVersion"] == 3
    with agent_run_environment.session_factory() as session:
        specs = list(
            session.scalars(
                select(AnalysisSpecRecord)
                .where(AnalysisSpecRecord.workflow_id == workflow_id)
                .order_by(AnalysisSpecRecord.revision)
            )
        )
    assert [record.revision for record in specs] == [1, 2]
    assert [record.status for record in specs] == ["superseded", "pending-approval"]
    assert specs[0].spec_sha256 != specs[1].spec_sha256
    assert revised["plan"]["spec"]["analysisSpecId"] == specs[1].id

    approval = revised["pendingApprovals"][0]
    approved_plan = agent_run_client.post(
        f"/v1/workflows/{workflow_id}/approve-plan",
        json={
            "approvalId": approval["id"],
            "planId": revised["plan"]["id"],
            "planVersion": revised["plan"]["version"],
            "planSha256": revised["plan"]["planSha256"],
            "expectedWorkflowRevision": revised["workflow"]["revision"],
        },
    )
    assert approved_plan.status_code == 200, approved_plan.text
    assert _run_once(restarted_worker)  # inspect-dataset
    assert _run_once(restarted_worker)  # prepare-analysis
    execution_waiting = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    assert execution_waiting["analysisIntent"] is not None, (
        execution_waiting["workflow"]["status"],
        execution_waiting["workflow"]["statusReason"],
    )
    assert execution_waiting["analysisIntent"]["status"] == "waiting-approval"
    assert execution_waiting["analysisSpec"]["id"] == specs[1].id
    assert execution_waiting["analysisSpec"]["specSha256"] == specs[1].spec_sha256
    assert execution_waiting["analysisSpec"]["spec"]["operation"]["groupColumn"] == (
        "cohort"
    )
    assert execution_waiting["structuredResult"] is None
    intent_id = execution_waiting["analysisIntent"]["id"]
    with agent_run_environment.session_factory() as session:
        compiled_intent = session.get(AnalysisIntentRecord, intent_id)
        execution_approval = session.scalar(
            select(ApprovalRecord).where(ApprovalRecord.subject_id == intent_id)
        )
    assert compiled_intent is not None
    assert compiled_intent.analysis_spec_id == specs[1].id
    assert compiled_intent.spec_sha256 == specs[1].spec_sha256
    assert compiled_intent.dataset_profile_sha256 == specs[1].dataset_profile_sha256
    assert compiled_intent.compiler_version == "analysis-spec-compiler-v1"
    assert compiled_intent.code_sha256 == hashlib.sha256(
        compiled_intent.code.encode("utf-8")
    ).hexdigest()
    assert compiled_intent.runtime_policy_id == "dataset-analysis-spec-v1"
    assert execution_waiting["analysisIntent"]["analysisSpecId"] == specs[1].id
    assert execution_waiting["analysisIntent"]["specSha256"] == specs[1].spec_sha256
    execution_pending = execution_waiting["pendingApprovals"][0]
    assert execution_pending["approvalSchemaVersion"] == "analysis-intent-v4"
    assert execution_pending["analysisSpecId"] == specs[1].id
    assert execution_pending["codeSha256"] == compiled_intent.code_sha256
    assert execution_pending["runtimePolicyId"] == "dataset-analysis-spec-v1"
    assert execution_approval is not None
    assert execution_approval.payload_schema_version == "analysis-intent-v4"

    execution_decision = agent_run_client.post(
        f"/v1/workflows/{workflow_id}/analysis-intents/{intent_id}/decision",
        json={
            "approvalId": execution_pending["id"],
            "decision": "approved",
            "payloadSha256": compiled_intent.payload_sha256,
            "expectedWorkflowRevision": execution_waiting["workflow"]["revision"],
        },
    )
    assert execution_decision.status_code == 200, execution_decision.text

    test_root = agent_run_environment.project_roots["project-1"].parent
    execution_settings = replace(
        settings,
        runtime_exchange_dir=test_root / "runtime-exchange",
        runtime_socket_path=test_root / "runtime.sock",
    )
    monkeypatch.setattr(analysis_module, "settings", execution_settings)
    monkeypatch.setattr(execution_module, "settings", execution_settings)
    monkeypatch.setattr(filesystem_module, "settings", execution_settings)

    execution_errors: list[Exception] = []
    expected_intent_id = intent_id

    async def analysis_executor(
        intent_id: str,
        *,
        session_factory: sessionmaker[Session],
        expected_workflow_id: str,
        approval_workflow_revision: int,
    ) -> AnalysisRunOut:
        assert intent_id == expected_intent_id

        async def runtime_executor(**payload: object) -> RuntimeExecutionResult:
            assert payload["policy_profile_id"] == "dataset-analysis-spec-v1"
            assert payload["policy_template"] == "analysis-spec-compiler-v1"
            run_dir = payload["run_dir"]
            dataset_path = payload["dataset_path"]
            code = payload["code"]
            assert isinstance(run_dir, Path)
            assert isinstance(dataset_path, Path)
            assert isinstance(code, str)
            exec(  # noqa: S102 - executes only the approved canonical compiler output.
                compile(code, "<compiled-analysis>", "exec"),
                {"DATASET_PATH": dataset_path, "RUN_DIR": run_dir},
            )
            generated_names = {
                "analysis-spec.json",
                "results.json",
                "summary.csv",
                "figure.png",
            }
            generated_files = {
                name: (run_dir / name).read_bytes()
                for name in generated_names
                if (run_dir / name).is_file()
            }
            for name in generated_files:
                (run_dir / name).unlink()
            return write_attested_runtime_result(
                run_dir,
                execution_settings.runtime_exchange_dir,
                payload,
                stdout="compiled analysis completed\n",
                generated_files=generated_files,
            )

        try:
            return await execute_workflow_analysis_intent(
                intent_id,
                session_factory=session_factory,
                expected_workflow_id=expected_workflow_id,
                approval_workflow_revision=approval_workflow_revision,
                runtime_executor=runtime_executor,
            )
        except Exception as error:
            execution_errors.append(error)
            raise

    execution_worker = WorkflowWorker(
        agent_run_environment.session_factory,
        poll_interval_seconds=0.01,
        lease_seconds=1.0,
        heartbeat_seconds=0.1,
        analysis_executor=analysis_executor,
    )
    assert _run_once(execution_worker)  # execute compiled analysis
    with agent_run_environment.session_factory() as session:
        workflow = session.get(WorkflowRecord, workflow_id)
        assert workflow is not None
        agent_service.agent_run_snapshot(session, workflow)
    execution_response = agent_run_client.get(f"/v1/agent-runs/{workflow_id}")
    assert execution_response.status_code == 200, execution_response.text
    execution_snapshot = execution_response.json()
    assert execution_snapshot["analysisRun"]["status"] == "completed", (
        execution_snapshot["workflow"]["status"],
        execution_snapshot["workflow"]["statusReason"],
        execution_snapshot["analysisRun"]["error"],
        execution_snapshot["analysisIntent"]["errorSummary"],
        [
            (
                type(error).__name__,
                getattr(error, "code", None),
                getattr(error, "detail", None),
                repr(error.__cause__),
            )
            for error in execution_errors
        ],
    )
    assert _run_once(execution_worker)  # collect artifacts
    result_snapshot = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    assert result_snapshot["analysisRun"]["status"] == "completed"
    assert result_snapshot["structuredResult"]["analysisSpecId"] == specs[1].id
    assert result_snapshot["structuredResult"]["analysisIntentId"] == intent_id
    structured = result_snapshot["structuredResult"]["result"]
    assert structured["operationType"] == "two-group-comparison"
    assert structured["result"]["groupColumn"] == "cohort"
    assert structured["result"]["sampleSizes"] == {"a": 3, "b": 3}
    assert 0 <= structured["result"]["pValue"] <= 1
    with agent_run_environment.session_factory() as session:
        stored_result = session.scalar(
            select(StructuredAnalysisResultRecord).where(
                StructuredAnalysisResultRecord.analysis_intent_id == intent_id
            )
        )
        result_artifact = session.scalar(
            select(ArtifactRecord).where(
                ArtifactRecord.run_id == stored_result.run_id,
                ArtifactRecord.path.like("%/results.json"),
            )
        ) if stored_result is not None else None
    assert stored_result is not None
    assert result_artifact is not None
    assert (
        result_artifact.metadata_json["structuredResultSha256"]
        == stored_result.result_sha256
    )
    assert _run_once(execution_worker)  # deterministic compiled-result review
    reviewed_snapshot = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    assert reviewed_snapshot["workflow"]["status"] == "reviewing"
    review_snapshot = reviewed_snapshot["latestReview"]
    assert review_snapshot["reviewType"] == "deterministic-analysis-v1"
    assert review_snapshot["verdict"] == "passed-with-warnings"
    review_result = review_snapshot["result"]
    assert review_result["analysisSpecId"] == specs[1].id
    assert review_result["structuredResultSha256"] == stored_result.result_sha256
    conclusion = review_result["conclusion"]
    assert "used 6 of 6 row(s)" in conclusion
    assert "a: n=3, missing=0; b: n=3, missing=0" in conclusion
    assert f"p={structured['result']['pValue']:.6g}" in conclusion
    assert (
        f"{structured['result']['effectSizeName']}="
        f"{structured['result']['effectSize']:.6g}"
    ) in conclusion
    assert review_result["analysisIntentId"] == intent_id
    assert reviewed_snapshot["structuredResult"]["resultSha256"] == (
        review_result["structuredResultSha256"]
    )
    events_response = agent_run_client.get(
        f"/v1/workflows/{workflow_id}/events?after=0&limit=100"
    )
    assert events_response.status_code == 200, events_response.text
    event_types = {item["type"] for item in events_response.json()["events"]}
    assert {
        "analysis.method-selection-started",
        "analysis.clarification-requested",
        "analysis.spec-created",
        "analysis.spec-superseded",
        "analysis.spec-approved",
        "analysis.compiled",
        "analysis.execution-approval-requested",
        "analysis.execution-started",
        "analysis.structured-result-created",
        "analysis.review-completed",
    }.issubset(event_types)


def test_remote_dataset_method_selection_persists_model_provenance(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_id = _add_dataset_source(
        agent_run_environment,
        source_id="dataset-remote-method",
    )
    gateway = _DatasetMethodGateway(
        destination="dataset-method-selector",
        selected_source_id=dataset_id,
    )
    _install_remote_gateway(monkeypatch, gateway)
    started = _create_agent_run(
        agent_run_client,
        goal="Summarize outcome with descriptive statistics.",
        source_ids=[dataset_id],
        idempotency_key="agent-remote-dataset-method-0001",
        remote_data_approved=True,
    )
    workflow_id = started["workflow"]["id"]

    assert _run_once(agent_run_environment.worker)
    assert _run_once(agent_run_environment.worker)
    snapshot = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    assert snapshot["workflow"]["status"] == "waiting-plan-approval"
    assert snapshot["workflow"]["generationMode"] == "remote-model-assisted"
    assert gateway.call_count == 2

    with agent_run_environment.session_factory() as session:
        analysis_spec = session.scalar(
            select(AnalysisSpecRecord).where(
                AnalysisSpecRecord.workflow_id == workflow_id
            )
        )
        invocations = list(
            session.scalars(
                select(ModelInvocationRecord)
                .where(ModelInvocationRecord.workflow_id == workflow_id)
                .order_by(ModelInvocationRecord.created_at)
            )
        )
    assert analysis_spec is not None
    assert analysis_spec.selector_kind == "remote-model-assisted"
    assert analysis_spec.model_invocation_id is not None
    method_invocation = next(
        record
        for record in invocations
        if record.operation_type == "analysis-method-selection"
    )
    assert analysis_spec.model_invocation_id == method_invocation.id
    assert method_invocation.model == gateway.default_model
    assert method_invocation.endpoint_identity == gateway.endpoint_identity
    assert method_invocation.prompt_version == "analysis-method-selector-v1"
    assert method_invocation.status == "succeeded"


def test_unsupported_scientific_method_blocks_without_substitution(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
) -> None:
    dataset_id = _add_dataset_source(
        agent_run_environment,
        source_id="dataset-unsupported-method",
    )
    started = _create_agent_run(
        agent_run_client,
        goal="Run a paired test comparing outcome before and after treatment.",
        source_ids=[dataset_id],
        idempotency_key="agent-unsupported-method-0001",
    )
    workflow_id = started["workflow"]["id"]

    assert _run_once(agent_run_environment.worker)
    assert _run_once(agent_run_environment.worker)
    snapshot = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    assert snapshot["workflow"]["status"] == "blocked"
    assert snapshot["workflow"]["statusReason"]["code"] == (
        "analysis-unsupported:paired-test"
    )
    assert snapshot["plan"] is None
    with agent_run_environment.session_factory() as session:
        analysis_specs = session.scalar(
            select(func.count())
            .select_from(AnalysisSpecRecord)
            .where(AnalysisSpecRecord.workflow_id == workflow_id)
        )
        unsupported_events = session.scalar(
            select(func.count())
            .select_from(EventRecord)
            .where(
                EventRecord.workflow_id == workflow_id,
                EventRecord.event_type == "analysis.unsupported",
            )
        )
    assert analysis_specs == 0
    assert unsupported_events == 1


@pytest.mark.parametrize(
    ("answer", "expected_workflow_type"),
    [
        ("literature-synthesis", "literature-synthesis"),
        ("dataset-analysis", "dataset-analysis"),
    ],
)
def test_local_pdf_and_dataset_require_a_supported_single_workflow_choice(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
    answer: str,
    expected_workflow_type: str,
) -> None:
    paper_id = _add_pdf_source(agent_run_environment)
    dataset_id = _add_dataset_source(agent_run_environment)
    started = _create_agent_run(
        agent_run_client,
        goal="Research the selected literature and dataset.",
        source_ids=[paper_id, dataset_id],
        idempotency_key=f"agent-route-local-mixed-{answer}-0001",
    )
    workflow_id = started["workflow"]["id"]

    assert _run_once(agent_run_environment.worker)
    response = agent_run_client.get(f"/v1/agent-runs/{workflow_id}")

    assert response.status_code == 200, response.text
    waiting = response.json()
    assert waiting["workflow"]["status"] == "waiting-clarification"
    assert waiting["workflow"]["workflowType"] is None
    assert waiting["intentDecision"]["intent"] == "clarification-required"
    assert waiting["intentDecision"]["missingInputs"] == [
        "select-supported-single-workflow"
    ]
    assert waiting["intentDecision"]["generator"] == "deterministic-intent-router-v1"
    assert waiting["intentDecision"]["usedModel"] is False
    assert waiting["intentDecision"]["model"] is None
    assert waiting["intentDecision"]["endpointIdentity"] is None
    assert waiting["intentDecision"]["parseResult"] == "model-not-configured"
    interaction = waiting["interactions"][0]
    assert interaction["question"] == (
        "Which single supported research path should this run use first?"
    )
    assert [option["value"] for option in interaction["options"]] == [
        "literature-synthesis",
        "dataset-analysis",
    ]

    answered = agent_run_client.post(
        f"/v1/interactions/{interaction['id']}/respond",
        headers={"Idempotency-Key": f"agent-route-choice-{answer}-0001"},
        json={
            "response": answer,
            "expectedWorkflowRevision": waiting["workflow"]["revision"],
        },
    )
    assert answered.status_code == 202, answered.text
    assert answered.json()["workflow"]["status"] == "routing"

    assert _run_once(agent_run_environment.worker)
    resolved = agent_run_client.get(f"/v1/agent-runs/{workflow_id}")
    assert resolved.status_code == 200, resolved.text
    resolved_snapshot = resolved.json()
    assert resolved_snapshot["workflow"]["status"] == "planning"
    assert resolved_snapshot["workflow"]["workflowType"] == expected_workflow_type
    assert resolved_snapshot["intentDecision"]["intent"] == expected_workflow_type
    assert resolved_snapshot["intentDecision"]["selectedSourceIds"] == (
        [paper_id] if expected_workflow_type == "literature-synthesis" else [dataset_id]
    )


def test_model_mixed_decision_is_explicitly_unsupported(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = _add_pdf_source(agent_run_environment, source_id="paper-model-mixed")
    dataset_id = _add_dataset_source(
        agent_run_environment,
        source_id="dataset-model-mixed",
    )
    gateway = _MixedIntentGateway(
        destination="mixed-router",
        source_ids=[paper_id, dataset_id],
    )
    _install_remote_gateway(monkeypatch, gateway)
    started = _create_agent_run(
        agent_run_client,
        goal="Compare literature claims with the observed dataset outcomes.",
        source_ids=[paper_id, dataset_id],
        idempotency_key="agent-route-model-mixed-0001",
        remote_data_approved=True,
    )

    assert _run_once(agent_run_environment.worker)
    response = agent_run_client.get(
        f"/v1/agent-runs/{started['workflow']['id']}"
    )
    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["workflow"]["status"] == "unsupported"
    assert snapshot["intentDecision"]["intent"] == "mixed-research"
    assert snapshot["workflow"]["statusReason"]["code"] == (
        "mixed-workflow-not-yet-available"
    )
    assert snapshot["intentDecision"]["generator"] == (
        "model-assisted-intent-router-v1"
    )
    assert snapshot["intentDecision"]["usedModel"] is True
    assert snapshot["intentDecision"]["model"] == gateway.default_model
    assert snapshot["intentDecision"]["endpointIdentity"] == gateway.endpoint_identity
    assert snapshot["intentDecision"]["parseResult"] == "valid"


def test_sem_goal_is_deterministically_unsupported(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
) -> None:
    dataset_id = _add_dataset_source(agent_run_environment)
    started = _create_agent_run(
        agent_run_client,
        goal="Fit a structural equation model (SEM) to this dataset.",
        source_ids=[dataset_id],
        idempotency_key="agent-route-sem-unsupported-0001",
    )

    assert _run_once(agent_run_environment.worker)
    response = agent_run_client.get(f"/v1/agent-runs/{started['workflow']['id']}")

    assert response.status_code == 200, response.text
    snapshot = response.json()
    assert snapshot["workflow"]["status"] == "unsupported"
    assert snapshot["workflow"]["workflowType"] is None
    assert snapshot["intentDecision"]["intent"] == "unsupported"
    assert snapshot["intentDecision"]["proposedWorkflowType"] is None
    assert snapshot["workflow"]["statusReason"]["code"] == ("research-capability-unsupported")
    with agent_run_environment.session_factory() as session:
        decision = session.get(
            IntentDecisionRecord,
            snapshot["intentDecision"]["id"],
        )
    assert decision is not None
    assert decision.parse_result == "deterministic-capability-guard"
    assert decision.used_model is False


def test_restarted_worker_routes_queued_run_and_preserves_pending_interaction(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
) -> None:
    started = _create_agent_run(
        agent_run_client,
        goal="Research an unspecified local source.",
        source_ids=[],
        idempotency_key="agent-router-restart-0001",
    )
    workflow_id = started["workflow"]["id"]
    restarted_worker = WorkflowWorker(
        agent_run_environment.session_factory,
        poll_interval_seconds=0.01,
        lease_seconds=1.0,
        heartbeat_seconds=0.1,
    )

    restarted_worker.recover()
    assert _run_once(restarted_worker)
    before_second_restart = agent_run_client.get(f"/v1/agent-runs/{workflow_id}")
    assert before_second_restart.status_code == 200, before_second_restart.text
    waiting_snapshot = before_second_restart.json()
    interaction_id = waiting_snapshot["interactions"][0]["id"]
    event_cursor = waiting_snapshot["eventCursor"]
    assert waiting_snapshot["workflow"]["status"] == "waiting-clarification"
    assert waiting_snapshot["interactions"][0]["status"] == "pending"

    second_restart = WorkflowWorker(
        agent_run_environment.session_factory,
        poll_interval_seconds=0.01,
        lease_seconds=1.0,
        heartbeat_seconds=0.1,
    )
    second_restart.recover()
    assert _run_once(second_restart) is False

    after_second_restart = agent_run_client.get(f"/v1/agent-runs/{workflow_id}")
    assert after_second_restart.status_code == 200, after_second_restart.text
    persisted = after_second_restart.json()
    assert persisted["workflow"]["status"] == "waiting-clarification"
    assert persisted["interactions"][0]["id"] == interaction_id
    assert persisted["interactions"][0]["status"] == "pending"
    assert persisted["eventCursor"] == event_cursor
    with agent_run_environment.session_factory() as session:
        pending_interactions = session.scalar(
            select(func.count())
            .select_from(InteractionRequestRecord)
            .where(
                InteractionRequestRecord.workflow_id == workflow_id,
                InteractionRequestRecord.status == "pending",
            )
        )
        queued_jobs = session.scalar(
            select(func.count())
            .select_from(JobRecord)
            .where(
                JobRecord.workflow_id == workflow_id,
                JobRecord.status == "queued",
            )
        )
    assert pending_interactions == 1
    assert queued_jobs == 0


def test_remote_gateway_approval_mismatch_never_starts_an_invocation(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = _add_pdf_source(
        agent_run_environment,
        source_id="paper-approval-mismatch",
    )
    approved_gateway = _CountingIntentGateway(
        destination="approved-router",
        selected_source_id=paper_id,
    )
    changed_gateway = _CountingIntentGateway(
        destination="changed-router",
        selected_source_id=paper_id,
    )
    _install_remote_gateway(monkeypatch, approved_gateway)
    started = _create_agent_run(
        agent_run_client,
        goal="Synthesize the approved paper with the remote intent router.",
        source_ids=[paper_id],
        idempotency_key="agent-remote-approval-mismatch-0001",
        remote_data_approved=True,
    )
    monkeypatch.setattr(handlers, "model_gateway", changed_gateway)

    assert _run_once(agent_run_environment.worker)

    assert approved_gateway.call_count == 0
    assert changed_gateway.call_count == 0
    with agent_run_environment.session_factory() as session:
        invocation_count = session.scalar(
            select(func.count()).select_from(ModelInvocationRecord)
        )
        decision_count = session.scalar(
            select(func.count()).select_from(IntentDecisionRecord)
        )
        workflow = session.get(WorkflowRecord, started["workflow"]["id"])
    assert invocation_count == 0
    assert decision_count == 0
    assert workflow is not None
    assert workflow.status == "failed"
    assert workflow.last_error_code == "remote-gateway-approval-mismatch"


def test_pending_remote_invocation_recovers_without_a_second_model_call(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = _add_pdf_source(
        agent_run_environment,
        source_id="paper-pending-invocation",
    )
    gateway = _CountingIntentGateway(
        destination="pending-router",
        selected_source_id=paper_id,
    )
    _install_remote_gateway(monkeypatch, gateway)
    started = _create_agent_run(
        agent_run_client,
        goal="Synthesize the paper after recovering an uncertain remote request.",
        source_ids=[paper_id],
        idempotency_key="agent-pending-invocation-0001",
        remote_data_approved=True,
    )
    workflow_id = started["workflow"]["id"]

    with agent_run_environment.session_factory.begin() as session:
        workflow = session.get(WorkflowRecord, workflow_id)
        job = session.scalar(
            select(JobRecord).where(
                JobRecord.workflow_id == workflow_id,
                JobRecord.kind == "route-intent",
            )
        )
        source = session.get(SourceRecord, paper_id)
        assert workflow is not None
        assert job is not None
        assert source is not None
        router_sources = [
            IntentSource(
                id=source.id,
                source_kind=cast(SourceKind, source.source_kind),
                ingestion_status=source.ingestion_status,
            )
        ]
        input_sha256 = intent_router_input_sha256(
            workflow.goal,
            router_sources,
            model=gateway.default_model,
        )
        invocation = ModelInvocationRecord(
            id="pending-router-invocation",
            workflow_id=workflow_id,
            schema_version="1",
            operation_type="intent-router",
            operation_key=job.operation_key,
            attempt=job.attempt,
            generator="openai-compatible",
            model=gateway.default_model,
            endpoint_identity=gateway.endpoint_identity,
            prompt_version=INTENT_ROUTER_PROMPT_VERSION,
            input_sha256=input_sha256,
            output_sha256=None,
            token_usage={},
            validation_errors=[],
            request_idempotency_key="pending-router-request-0001",
            request_payload_sha256=input_sha256,
            status="pending",
            error_code=None,
            error_message=None,
            finished_at=None,
        )
        session.add(invocation)

    assert _run_once(agent_run_environment.worker)

    assert gateway.call_count == 0
    snapshot = agent_run_client.get(f"/v1/agent-runs/{workflow_id}")
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["workflow"]["status"] == "planning"
    assert snapshot.json()["intentDecision"]["intent"] == "literature-synthesis"
    with agent_run_environment.session_factory() as session:
        invocation = session.get(ModelInvocationRecord, "pending-router-invocation")
        decisions = list(
            session.scalars(
                select(IntentDecisionRecord).where(
                    IntentDecisionRecord.workflow_id == workflow_id
                )
            )
        )
    assert invocation is not None
    assert invocation.status == "failed"
    assert invocation.error_code == "model-request-outcome-unknown"
    assert invocation.output_sha256 is None
    assert len(decisions) == 1
    assert decisions[0].model_invocation_id == invocation.id
    assert decisions[0].parse_result == "model-request-outcome-unknown"


def test_retry_reuses_durable_remote_decision_after_publication_failure(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = _add_pdf_source(
        agent_run_environment,
        source_id="paper-publication-retry",
    )
    gateway = _CountingIntentGateway(
        destination="publication-router",
        selected_source_id=paper_id,
    )
    _install_remote_gateway(monkeypatch, gateway)
    started = _create_agent_run(
        agent_run_client,
        goal="Synthesize the paper and safely retry publication.",
        source_ids=[paper_id],
        idempotency_key="agent-publication-retry-0001",
        remote_data_approved=True,
    )
    workflow_id = started["workflow"]["id"]
    original_finish_job = agent_service.finish_job
    publication_attempts = 0

    def fail_first_publication(
        session: Session,
        job: JobRecord,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        nonlocal publication_attempts
        if job.kind == "route-intent" and status == "succeeded" and publication_attempts == 0:
            publication_attempts += 1
            raise WorkflowFailure(
                "test-intent-publication-failed",
                "The test interrupted downstream intent publication.",
                retryable=True,
            )
        original_finish_job(session, job, status, error_code, error_message)

    monkeypatch.setattr(agent_service, "finish_job", fail_first_publication)
    assert _run_once(agent_run_environment.worker)
    assert gateway.call_count == 1

    with agent_run_environment.session_factory.begin() as session:
        invocations = list(
            session.scalars(
                select(ModelInvocationRecord).where(
                    ModelInvocationRecord.workflow_id == workflow_id
                )
            )
        )
        decisions = list(
            session.scalars(
                select(IntentDecisionRecord).where(
                    IntentDecisionRecord.workflow_id == workflow_id
                )
            )
        )
        retry_job = session.scalar(
            select(JobRecord).where(
                JobRecord.workflow_id == workflow_id,
                JobRecord.kind == "route-intent",
                JobRecord.status == "queued",
            )
        )
        assert retry_job is not None
        retry_job.available_at = utc_now()
    assert len(invocations) == 1
    assert invocations[0].status == "succeeded"
    assert len(decisions) == 1
    assert decisions[0].model_invocation_id == invocations[0].id

    monkeypatch.setattr(agent_service, "finish_job", original_finish_job)
    assert _run_once(agent_run_environment.worker)

    assert gateway.call_count == 1
    snapshot = agent_run_client.get(f"/v1/agent-runs/{workflow_id}")
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["workflow"]["status"] == "planning"
    with agent_run_environment.session_factory() as session:
        invocation_count = session.scalar(
            select(func.count())
            .select_from(ModelInvocationRecord)
            .where(ModelInvocationRecord.workflow_id == workflow_id)
        )
        decision_count = session.scalar(
            select(func.count())
            .select_from(IntentDecisionRecord)
            .where(IntentDecisionRecord.workflow_id == workflow_id)
        )
        route_jobs = list(
            session.scalars(
                select(JobRecord)
                .where(
                    JobRecord.workflow_id == workflow_id,
                    JobRecord.kind == "route-intent",
                )
                .order_by(JobRecord.attempt)
            )
        )
    assert invocation_count == 1
    assert decision_count == 1
    assert [(job.attempt, job.status) for job in route_jobs] == [
        (1, "failed"),
        (2, "succeeded"),
    ]


def test_remote_route_stops_before_model_call_after_lease_token_replacement(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = _add_pdf_source(
        agent_run_environment,
        source_id="paper-lease-fencing",
    )
    gateway = _CountingIntentGateway(
        destination="lease-fencing-router",
        selected_source_id=paper_id,
    )
    _install_remote_gateway(monkeypatch, gateway)
    started = _create_agent_run(
        agent_run_client,
        goal="Synthesize the paper only while this routing lease remains valid.",
        source_ids=[paper_id],
        idempotency_key="agent-lease-fencing-0001",
        remote_data_approved=True,
    )
    workflow_id = started["workflow"]["id"]
    original_begin = getattr(agent_service, "_begin_model_invocation")

    def replace_lease_after_begin(
        session: Session,
        workflow: WorkflowRecord,
        job: JobRecord,
        sources: list[SourceRecord],
        answered_context: list[dict[str, object]],
        model_gateway: Any,
    ) -> ModelInvocationRecord:
        invocation = original_begin(
            session,
            workflow,
            job,
            sources,
            answered_context,
            model_gateway,
        )
        job.lease_token = "replacement-lease-token"
        return invocation

    monkeypatch.setattr(
        agent_service,
        "_begin_model_invocation",
        replace_lease_after_begin,
    )

    assert _run_once(agent_run_environment.worker)
    assert gateway.call_count == 0
    with agent_run_environment.session_factory() as session:
        workflow = session.get(WorkflowRecord, workflow_id)
        job = session.scalar(
            select(JobRecord).where(
                JobRecord.workflow_id == workflow_id,
                JobRecord.kind == "route-intent",
            )
        )
        invocations = list(
            session.scalars(
                select(ModelInvocationRecord).where(
                    ModelInvocationRecord.workflow_id == workflow_id
                )
            )
        )
        decisions = list(
            session.scalars(
                select(IntentDecisionRecord).where(
                    IntentDecisionRecord.workflow_id == workflow_id
                )
            )
        )
    assert workflow is not None
    assert workflow.status == "routing"
    assert job is not None
    assert job.status == "leased"
    assert job.lease_token == "replacement-lease-token"
    assert [item.status for item in invocations] == ["pending"]
    assert decisions == []


def test_number_interaction_rejects_an_overflowing_json_integer(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
) -> None:
    started = _create_agent_run(
        agent_run_client,
        goal="Collect a numeric clarification for this research question.",
        source_ids=[],
        idempotency_key="agent-number-overflow-0001",
    )
    workflow_id = started["workflow"]["id"]
    assert _run_once(agent_run_environment.worker)
    waiting = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    interaction_id = waiting["interactions"][0]["id"]
    with agent_run_environment.session_factory.begin() as session:
        interaction = session.get(InteractionRequestRecord, interaction_id)
        assert interaction is not None
        interaction.request_type = "number"
        interaction.options = []
        interaction.response_schema = {"type": "number"}

    response = agent_run_client.post(
        f"/v1/interactions/{interaction_id}/respond",
        headers={"Idempotency-Key": "agent-number-overflow-response-0001"},
        json={
            "response": 10**400,
            "expectedWorkflowRevision": waiting["workflow"]["revision"],
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "interaction-response-invalid"


def test_new_clarification_supersedes_the_previous_answered_request(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = _add_pdf_source(
        agent_run_environment,
        source_id="paper-multi-clarification",
    )
    gateway = _SequencedIntentGateway(
        destination="multi-clarification-router",
        selected_source_id=paper_id,
    )
    _install_remote_gateway(monkeypatch, gateway)
    started = _create_agent_run(
        agent_run_client,
        goal="Confirm the research path before synthesizing this paper.",
        source_ids=[paper_id],
        idempotency_key="agent-multi-clarification-0001",
        remote_data_approved=True,
    )
    workflow_id = started["workflow"]["id"]

    assert _run_once(agent_run_environment.worker)
    first_waiting = agent_run_client.get(f"/v1/agent-runs/{workflow_id}").json()
    first_interaction = first_waiting["interactions"][0]
    answered = agent_run_client.post(
        f"/v1/interactions/{first_interaction['id']}/respond",
        headers={"Idempotency-Key": "agent-multi-clarification-answer-0001"},
        json={
            "response": "literature-synthesis",
            "expectedWorkflowRevision": first_waiting["workflow"]["revision"],
        },
    )
    assert answered.status_code == 202, answered.text

    assert _run_once(agent_run_environment.worker)
    second_waiting_response = agent_run_client.get(f"/v1/agent-runs/{workflow_id}")
    assert second_waiting_response.status_code == 200, second_waiting_response.text
    second_waiting = second_waiting_response.json()
    assert second_waiting["workflow"]["status"] == "waiting-clarification"
    assert gateway.call_count == 2
    interactions = {item["id"]: item for item in second_waiting["interactions"]}
    assert interactions[first_interaction["id"]]["status"] == "superseded"
    assert interactions[first_interaction["id"]]["latestResponse"] is not None
    pending = [item for item in interactions.values() if item["status"] == "pending"]
    assert len(pending) == 1
    assert "respond-interaction" in second_waiting["allowedActions"]

    stale_revision = agent_run_client.post(
        f"/v1/interactions/{first_interaction['id']}/respond",
        headers={"Idempotency-Key": "agent-multi-clarification-stale-0001"},
        json={
            "response": "literature-synthesis",
            "expectedWorkflowRevision": second_waiting["workflow"]["revision"],
        },
    )
    assert stale_revision.status_code == 409, stale_revision.text
    assert stale_revision.json()["detail"]["code"] == "interaction-superseded"


def test_agent_retry_count_counts_retry_jobs_instead_of_triangular_attempts(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
) -> None:
    paper_id = _add_pdf_source(
        agent_run_environment,
        source_id="paper-retry-count",
    )
    started = _create_agent_run(
        agent_run_client,
        goal="Synthesize this paper and report retry accounting accurately.",
        source_ids=[paper_id],
        idempotency_key="agent-retry-count-0001",
    )
    workflow_id = started["workflow"]["id"]
    assert _run_once(agent_run_environment.worker)

    with agent_run_environment.session_factory.begin() as session:
        first = session.scalar(
            select(JobRecord).where(
                JobRecord.workflow_id == workflow_id,
                JobRecord.kind == "route-intent",
            )
        )
        assert first is not None
        now = utc_now()
        session.add_all(
            [
                JobRecord(
                    id=f"retry-count-job-{attempt}",
                    workflow_id=workflow_id,
                    kind=first.kind,
                    operation_key=first.operation_key,
                    attempt=attempt,
                    input_sha256=first.input_sha256,
                    handler_version=first.handler_version,
                    status="failed",
                    available_at=now,
                    error_code="test-retry",
                    error_message="Synthetic retry history.",
                    finished_at=now,
                )
                for attempt in (2, 3)
            ]
        )

    response = agent_run_client.get(f"/v1/agent-runs/{workflow_id}")
    assert response.status_code == 200, response.text
    assert response.json()["workflow"]["retryCount"] == 2


def test_snapshot_rejects_nonterminal_or_hash_mismatched_intent_provenance(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper_id = _add_pdf_source(
        agent_run_environment,
        source_id="paper-provenance-integrity",
    )
    gateway = _CountingIntentGateway(
        destination="provenance-integrity-router",
        selected_source_id=paper_id,
    )
    _install_remote_gateway(monkeypatch, gateway)
    started = _create_agent_run(
        agent_run_client,
        goal="Synthesize this paper with immutable routing provenance.",
        source_ids=[paper_id],
        idempotency_key="agent-provenance-integrity-0001",
        remote_data_approved=True,
    )
    workflow_id = started["workflow"]["id"]
    assert _run_once(agent_run_environment.worker)

    with agent_run_environment.session_factory.begin() as session:
        invocation = session.scalar(
            select(ModelInvocationRecord).where(
                ModelInvocationRecord.workflow_id == workflow_id
            )
        )
        assert invocation is not None
        terminal_output_sha256 = invocation.output_sha256
        terminal_finished_at = invocation.finished_at
        invocation.status = "pending"
        invocation.output_sha256 = None
        invocation.error_code = None
        invocation.error_message = None
        invocation.finished_at = None

    pending = agent_run_client.get(f"/v1/agent-runs/{workflow_id}")
    assert pending.status_code == 409, pending.text
    assert pending.json()["detail"]["code"] == "intent-decision-integrity-failed"
    assert gateway.call_count == 1

    with agent_run_environment.session_factory.begin() as session:
        invocation = session.scalar(
            select(ModelInvocationRecord).where(
                ModelInvocationRecord.workflow_id == workflow_id
            )
        )
        decision = session.scalar(
            select(IntentDecisionRecord).where(
                IntentDecisionRecord.workflow_id == workflow_id
            )
        )
        assert invocation is not None
        assert decision is not None
        invocation.status = "succeeded"
        invocation.output_sha256 = terminal_output_sha256
        invocation.finished_at = terminal_finished_at
        decision.output_sha256 = "0" * 64

    tampered = agent_run_client.get(f"/v1/agent-runs/{workflow_id}")
    assert tampered.status_code == 409, tampered.text
    assert tampered.json()["detail"]["code"] == "intent-decision-integrity-failed"
    assert gateway.call_count == 1


def test_snapshot_requires_current_decision_and_pending_clarification(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
) -> None:
    paper_id = _add_pdf_source(
        agent_run_environment,
        source_id="paper-missing-decision",
    )
    resolved = _create_agent_run(
        agent_run_client,
        goal="Synthesize this paper with a current routing decision.",
        source_ids=[paper_id],
        idempotency_key="agent-missing-decision-0001",
    )
    resolved_id = resolved["workflow"]["id"]
    assert _run_once(agent_run_environment.worker)
    with agent_run_environment.session_factory.begin() as session:
        workflow = session.get(WorkflowRecord, resolved_id)
        assert workflow is not None
        workflow.current_intent_decision_id = None

    missing_decision = agent_run_client.get(f"/v1/agent-runs/{resolved_id}")
    assert missing_decision.status_code == 409, missing_decision.text
    assert missing_decision.json()["detail"]["code"] == (
        "intent-decision-integrity-failed"
    )


def test_snapshot_rejects_waiting_workflow_without_a_pending_interaction(
    agent_run_environment: AgentRunEnvironment,
    agent_run_client: TypedTestClient,
) -> None:
    waiting = _create_agent_run(
        agent_run_client,
        goal="Clarify the missing source before routing this run.",
        source_ids=[],
        idempotency_key="agent-missing-pending-interaction-0001",
    )
    workflow_id = waiting["workflow"]["id"]
    assert _run_once(agent_run_environment.worker)
    with agent_run_environment.session_factory.begin() as session:
        interaction = session.scalar(
            select(InteractionRequestRecord).where(
                InteractionRequestRecord.workflow_id == workflow_id
            )
        )
        assert interaction is not None
        interaction.status = "superseded"

    response = agent_run_client.get(f"/v1/agent-runs/{workflow_id}")
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "interaction-integrity-failed"
