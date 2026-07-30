from __future__ import annotations

import hashlib
from collections.abc import Generator
from datetime import datetime, timezone
from typing import Any, Protocol, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from open_science_core.api.candidate_triage import (
    get_candidate_triage_session,
    router,
)
from open_science_core.models import (
    Base,
    CandidateTriageDecisionRecord,
    DiscoveryCandidateRecord,
    ProjectRecord,
    SourceRecord,
)


class _RequestClient(Protocol):
    def request(self, method: str, url: str, **kwargs: Any) -> Response: ...


class TypedTestClient(TestClient):
    def get(self, url: str, **kwargs: Any) -> Response:  # pyright: ignore[reportIncompatibleMethodOverride]
        return cast(_RequestClient, self).request("GET", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> Response:  # pyright: ignore[reportIncompatibleMethodOverride]
        return cast(_RequestClient, self).request("PUT", url, **kwargs)


def _project(project_id: str) -> ProjectRecord:
    return ProjectRecord(
        id=project_id,
        title=project_id,
        description="",
        project_path=f"/tmp/{project_id}",
        research_domain=None,
        execution_mode="safe",
    )


def _candidate(candidate_id: str, project_id: str) -> DiscoveryCandidateRecord:
    return DiscoveryCandidateRecord(
        id=candidate_id,
        project_id=project_id,
        provider="crossref",
        provider_id=candidate_id,
        normalized_identity=f"crossref:{candidate_id}",
        metadata_json={},
        candidate_sha256=hashlib.sha256(candidate_id.encode()).hexdigest(),
    )


class TestCandidateTriageApi:
    def setup_method(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        def enable_foreign_keys(dbapi_connection: object, _record: object) -> None:
            dbapi_connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]

        event.listen(self.engine, "connect", enable_foreign_keys)
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add_all([_project("project-a"), _project("project-b")])
            session.flush()
            session.add_all(
                [
                    _candidate("candidate-a", "project-a"),
                    _candidate("candidate-z", "project-a"),
                    _candidate("candidate-b", "project-b"),
                ]
            )
            session.commit()

        api = FastAPI()
        api.include_router(router)

        def session_dependency() -> Generator[Session, None, None]:
            with Session(self.engine) as session:
                yield session

        api.dependency_overrides[get_candidate_triage_session] = session_dependency
        self.client = TypedTestClient(api)

    def test_create_update_and_stale_version(self) -> None:
        created = self.client.put(
            "/v1/projects/project-a/candidate-triage-decisions/candidate-a",
            json={
                "decision": "keep",
                "reason": "  Relevant abstract  ",
                "expectedVersion": 0,
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["decision"] == "keep"
        assert created.json()["reason"] == "Relevant abstract"
        assert created.json()["criteriaVersion"] == "candidate-triage-v1"
        assert created.json()["evidenceStatus"] == "not-evidence"
        assert created.json()["rowVersion"] == 1

        updated = self.client.put(
            "/v1/projects/project-a/candidate-triage-decisions/candidate-a",
            json={
                "decision": "uncertain",
                "reason": None,
                "criteriaVersion": "candidate-pilot-2026-07",
                "expectedVersion": 1,
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["decision"] == "uncertain"
        assert updated.json()["rowVersion"] == 2

        stale = self.client.put(
            "/v1/projects/project-a/candidate-triage-decisions/candidate-a",
            json={"decision": "reject", "expectedVersion": 1},
        )
        assert stale.status_code == 409

    def test_project_scope_and_strict_payload_fail_closed(self) -> None:
        cross_project = self.client.put(
            "/v1/projects/project-a/candidate-triage-decisions/candidate-b",
            json={"decision": "keep", "expectedVersion": 0},
        )
        missing = self.client.put(
            "/v1/projects/project-a/candidate-triage-decisions/missing",
            json={"decision": "keep", "expectedVersion": 0},
        )
        assert cross_project.status_code == 404
        assert missing.status_code == 404
        for payload in (
            {"decision": "include", "expectedVersion": 0},
            {"decision": "keep", "reason": "x" * 2001, "expectedVersion": 0},
            {"decision": "keep", "criteriaVersion": " ", "expectedVersion": 0},
            {"decision": "keep", "expectedVersion": "0"},
            {"decision": "keep", "expectedVersion": 0, "unknown": True},
        ):
            response = self.client.put(
                "/v1/projects/project-a/candidate-triage-decisions/candidate-a",
                json=payload,
            )
            assert response.status_code == 422, (payload, response.text)

    def test_list_is_project_scoped_and_does_not_create_sources(self) -> None:
        timestamp = datetime(2026, 7, 27, tzinfo=timezone.utc)
        with Session(self.engine) as session:
            session.add_all(
                [
                    CandidateTriageDecisionRecord(
                        id="triage-z",
                        project_id="project-a",
                        candidate_id="candidate-z",
                        decision="reject",
                        criteria_version="candidate-triage-v1",
                        row_version=1,
                        created_at=timestamp,
                        updated_at=timestamp,
                    ),
                    CandidateTriageDecisionRecord(
                        id="triage-a",
                        project_id="project-a",
                        candidate_id="candidate-a",
                        decision="keep",
                        criteria_version="candidate-triage-v1",
                        row_version=1,
                        created_at=timestamp,
                        updated_at=timestamp,
                    ),
                ]
            )
            session.commit()
        response = self.client.get(
            "/v1/projects/project-a/candidate-triage-decisions"
        )
        assert response.status_code == 200
        assert [item["candidateId"] for item in response.json()] == [
            "candidate-a",
            "candidate-z",
        ]
        assert (
            self.client.get(
                "/v1/projects/project-b/candidate-triage-decisions"
            ).json()
            == []
        )
        with Session(self.engine) as session:
            assert list(session.scalars(select(SourceRecord))) == []

    def test_candidate_and_project_deletion_cascade(self) -> None:
        for project_id, candidate_id in (
            ("project-a", "candidate-a"),
            ("project-b", "candidate-b"),
        ):
            response = self.client.put(
                f"/v1/projects/{project_id}/candidate-triage-decisions/{candidate_id}",
                json={"decision": "keep", "expectedVersion": 0},
            )
            assert response.status_code == 200
        with Session(self.engine) as session:
            session.delete(session.get(DiscoveryCandidateRecord, "candidate-a"))
            session.delete(session.get(ProjectRecord, "project-b"))
            session.commit()
            assert (
                list(session.scalars(select(CandidateTriageDecisionRecord))) == []
            )
