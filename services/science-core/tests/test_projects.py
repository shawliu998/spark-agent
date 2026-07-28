from __future__ import annotations

from collections.abc import Generator
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Protocol, cast
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from open_science_core.api.agent_runs import get_agent_session
from open_science_core.api.workflows import get_workflow_session
from open_science_core.app import app as production_app
from open_science_core.app import get_session, require_token
from open_science_core.config import settings
from open_science_core.db import Base
from open_science_core.models import WorkflowRecord


class _RequestClient(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Response: ...


_close_test_client = cast(Callable[[TestClient], None], getattr(TestClient, "close"))


class TypedTestClient(TestClient):
    def get(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("POST", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("PATCH", url, **kwargs)

    def close(self) -> None:
        _close_test_client(self)


class ProjectEnvironment:
    def __init__(self) -> None:
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.original_overrides = dict(production_app.dependency_overrides)

        def session_dependency() -> Generator[Session, None, None]:
            with Session(self.engine) as session:
                yield session

        production_app.dependency_overrides[get_session] = session_dependency
        production_app.dependency_overrides[get_agent_session] = session_dependency
        production_app.dependency_overrides[get_workflow_session] = session_dependency
        production_app.dependency_overrides[require_token] = lambda: None
        self.client = TypedTestClient(production_app)

    def close(self) -> None:
        self.client.close()
        production_app.dependency_overrides = self.original_overrides
        self.engine.dispose()
        self.directory.cleanup()


def _create_project(environment: ProjectEnvironment) -> dict[str, Any]:
    with patch("open_science_core.app.settings", replace_settings(environment.root)):
        response = environment.client.post("/v1/projects", json={"title": "  Initial project  "})
    assert response.status_code == 200, response.text
    return response.json()


def replace_settings(data_dir: Path) -> Any:
    return replace(settings, data_dir=data_dir)


def test_project_mutations_trim_titles_are_strict_and_idempotent() -> None:
    environment = ProjectEnvironment()
    try:
        created = _create_project(environment)
        assert created["title"] == "Initial project"
        assert created["rowVersion"] == 1
        assert created["archivedAt"] is None
        project_path = created["projectPath"]

        key = "rename-project-0001"
        renamed = environment.client.patch(
            f"/v1/projects/{created['id']}",
            headers={"Idempotency-Key": key},
            json={"title": "  Renamed project  ", "expectedRowVersion": 1},
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["title"] == "Renamed project"
        assert renamed.json()["rowVersion"] == 2
        assert renamed.json()["projectPath"] == project_path

        repeated = environment.client.patch(
            f"/v1/projects/{created['id']}",
            headers={"Idempotency-Key": key},
            json={"title": "  Renamed project  ", "expectedRowVersion": 1},
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["rowVersion"] == 2

        stale = environment.client.patch(
            f"/v1/projects/{created['id']}",
            headers={"Idempotency-Key": "rename-project-stale-0001"},
            json={"title": "Stale rename", "expectedRowVersion": 1},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "project-revision-conflict"

        reused = environment.client.patch(
            f"/v1/projects/{created['id']}",
            headers={"Idempotency-Key": key},
            json={"title": "Different title", "expectedRowVersion": 1},
        )
        assert reused.status_code == 409
        assert reused.json()["detail"]["code"] == "project-idempotency-key-reused"

        changed = environment.client.patch(
            f"/v1/projects/{created['id']}",
            headers={"Idempotency-Key": "rename-project-0002"},
            json={"title": "Second project", "expectedRowVersion": 2},
        )
        assert changed.status_code == 200
        stale_replay = environment.client.patch(
            f"/v1/projects/{created['id']}",
            headers={"Idempotency-Key": key},
            json={"title": "  Renamed project  ", "expectedRowVersion": 1},
        )
        assert stale_replay.status_code == 409
        assert stale_replay.json()["detail"]["code"] == "project-idempotency-stale"

        invalid = environment.client.patch(
            f"/v1/projects/{created['id']}",
            headers={"Idempotency-Key": "rename-project-invalid-0001"},
            json={"title": "Valid", "expectedRowVersion": 2, "unexpected": True},
        )
        assert invalid.status_code == 422
    finally:
        environment.close()


def test_project_archive_hides_and_restore_reveals_without_moving_project() -> None:
    environment = ProjectEnvironment()
    try:
        created = _create_project(environment)
        archive = environment.client.post(
            f"/v1/projects/{created['id']}/archive",
            headers={"Idempotency-Key": "archive-project-0001"},
            json={"expectedRowVersion": created["rowVersion"]},
        )
        assert archive.status_code == 200, archive.text
        assert archive.json()["archivedAt"] is not None
        assert archive.json()["projectPath"] == created["projectPath"]

        hidden = environment.client.get("/v1/projects")
        assert hidden.status_code == 200
        assert hidden.json() == []
        visible = environment.client.get("/v1/projects?includeArchived=true")
        assert visible.status_code == 200
        assert visible.json()[0]["id"] == created["id"]

        repeated = environment.client.post(
            f"/v1/projects/{created['id']}/archive",
            headers={"Idempotency-Key": "archive-project-0001"},
            json={"expectedRowVersion": created["rowVersion"]},
        )
        assert repeated.status_code == 200
        assert repeated.json()["rowVersion"] == archive.json()["rowVersion"]

        restored = environment.client.post(
            f"/v1/projects/{created['id']}/restore",
            headers={"Idempotency-Key": "restore-project-0001"},
            json={"expectedRowVersion": archive.json()["rowVersion"]},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["archivedAt"] is None
        assert environment.client.get("/v1/projects").json()[0]["id"] == created["id"]

        stale_archive_replay = environment.client.post(
            f"/v1/projects/{created['id']}/archive",
            headers={"Idempotency-Key": "archive-project-0001"},
            json={"expectedRowVersion": created["rowVersion"]},
        )
        assert stale_archive_replay.status_code == 409
        assert stale_archive_replay.json()["detail"]["code"] == "project-idempotency-stale"
        assert environment.client.get("/v1/projects").json()[0]["id"] == created["id"]
    finally:
        environment.close()


def test_archived_project_rejects_agent_run_creation_but_allows_read_lookup() -> None:
    environment = ProjectEnvironment()
    try:
        created = _create_project(environment)
        archive = environment.client.post(
            f"/v1/projects/{created['id']}/archive",
            headers={"Idempotency-Key": "archive-agent-project-0001"},
            json={"expectedRowVersion": created["rowVersion"]},
        )
        assert archive.status_code == 200, archive.text

        response = environment.client.post(
            f"/v1/projects/{created['id']}/agent-runs",
            headers={"Idempotency-Key": "agent-run-archived-0001"},
            json={"goal": "Review archived project"},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "project-archived"

        workflow_response = environment.client.post(
            f"/v1/projects/{created['id']}/workflows",
            headers={"Idempotency-Key": "workflow-archived-0001"},
            json={
                "goal": "Review archived project",
                "workflowType": "literature-synthesis",
            },
        )
        assert workflow_response.status_code == 409
        assert workflow_response.json()["detail"]["code"] == "project-archived"

        runs = environment.client.get(f"/v1/projects/{created['id']}/agent-runs")
        assert runs.status_code == 200, runs.text
        assert runs.json() == []
    finally:
        environment.close()


def test_project_archive_conflicts_with_active_workflow() -> None:
    environment = ProjectEnvironment()
    try:
        created = _create_project(environment)
        with Session(environment.engine) as session:
            session.add(
                WorkflowRecord(
                    id="workflow-active",
                    project_id=created["id"],
                    create_idempotency_key="workflow-create-0001",
                    create_payload_sha256="a" * 64,
                    creation_mode="fixed-workflow",
                    selected_source_ids=[],
                    workflow_type="literature-synthesis",
                    goal="Active project workflow",
                    generation_mode="local-deterministic",
                    status="running",
                    row_version=1,
                )
            )
            session.commit()
        response = environment.client.post(
            f"/v1/projects/{created['id']}/archive",
            headers={"Idempotency-Key": "archive-active-project-0001"},
            json={"expectedRowVersion": created["rowVersion"]},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "project-has-active-workflows"
        assert response.json()["detail"]["details"]["workflowIds"] == ["workflow-active"]
    finally:
        environment.close()
