# pyright: reportPrivateUsage=false

from __future__ import annotations

import hashlib
from collections.abc import Generator
from typing import Any, Protocol, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from open_science_core.api.evidence_directions import get_session, router
from open_science_core.db import Base
from open_science_core.models import AnswerRecord, ProjectRecord, SourceRecord


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


def _source(
    source_id: str,
    project_id: str,
    *,
    kind: str = "pdf",
    status: str = "ready",
) -> SourceRecord:
    return SourceRecord(
        id=source_id,
        project_id=project_id,
        title=source_id,
        source_kind=kind,
        authors=[],
        local_path=f"/tmp/{project_id}/{source_id}",
        ingestion_status=status,
        content_hash=hashlib.sha256(source_id.encode()).hexdigest(),
    )


def _answer(answer_id: str, project_id: str) -> AnswerRecord:
    return AnswerRecord(
        id=answer_id,
        project_id=project_id,
        question="Does the evidence support the intervention?",
        answer="A persisted answer.",
        unresolved_questions=[],
        generator="local-deterministic",
    )


class TestEvidenceDirectionsApi:
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
                    _source("a-source", "project-a"),
                    _source("a-second", "project-a"),
                    _source("a-dataset", "project-a", kind="dataset"),
                    _source("a-pending", "project-a", status="pending"),
                    _source("b-source", "project-b"),
                    _answer("answer-a", "project-a"),
                    _answer("answer-b", "project-b"),
                ]
            )
            session.commit()

        api = FastAPI()
        api.include_router(router)

        def session_dependency() -> Generator[Session, None, None]:
            with Session(self.engine) as session:
                yield session

        api.dependency_overrides[get_session] = session_dependency
        self.client = TypedTestClient(api)

    def test_create_update_and_reject_stale_version(self) -> None:
        endpoint = (
            "/v1/projects/project-a/answers/answer-a/"
            "evidence-directions/a-source"
        )
        created = self.client.put(
            endpoint,
            json={"direction": "supporting", "expectedVersion": 0},
        )
        assert created.status_code == 200, created.text
        assert created.json()["direction"] == "supporting"
        assert created.json()["rowVersion"] == 1

        updated = self.client.put(
            endpoint,
            json={"direction": "mixed", "expectedVersion": 1},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["direction"] == "mixed"
        assert updated.json()["rowVersion"] == 2

        stale = self.client.put(
            endpoint,
            json={"direction": "insufficient", "expectedVersion": 1},
        )
        assert stale.status_code == 409

    def test_answer_and_source_must_belong_to_project(self) -> None:
        cross_answer = self.client.put(
            "/v1/projects/project-a/answers/answer-b/evidence-directions/a-source",
            json={"direction": "supporting", "expectedVersion": 0},
        )
        cross_source = self.client.put(
            "/v1/projects/project-a/answers/answer-a/evidence-directions/b-source",
            json={"direction": "supporting", "expectedVersion": 0},
        )
        missing_answer = self.client.get(
            "/v1/projects/project-a/answers/missing/evidence-directions"
        )
        assert cross_answer.status_code == 404
        assert cross_source.status_code == 404
        assert missing_answer.status_code == 404

    def test_only_indexed_pdf_sources_are_judged(self) -> None:
        for source_id in ("a-dataset", "a-pending"):
            response = self.client.put(
                f"/v1/projects/project-a/answers/answer-a/evidence-directions/{source_id}",
                json={"direction": "supporting", "expectedVersion": 0},
            )
            assert response.status_code == 422, (source_id, response.text)

    def test_payload_is_strict(self) -> None:
        endpoint = (
            "/v1/projects/project-a/answers/answer-a/"
            "evidence-directions/a-source"
        )
        for payload in (
            {"direction": "contradicting", "expectedVersion": 0},
            {"direction": "supporting", "expectedVersion": "0"},
            {"direction": "supporting", "expectedVersion": 0, "reason": "model said so"},
        ):
            response = self.client.put(endpoint, json=payload)
            assert response.status_code == 422, (payload, response.text)

    def test_list_is_answer_scoped_and_persists_across_sessions(self) -> None:
        first = self.client.put(
            "/v1/projects/project-a/answers/answer-a/evidence-directions/a-source",
            json={"direction": "supporting", "expectedVersion": 0},
        )
        second = self.client.put(
            "/v1/projects/project-a/answers/answer-a/evidence-directions/a-second",
            json={"direction": "insufficient", "expectedVersion": 0},
        )
        assert first.status_code == 200
        assert second.status_code == 200

        listed = self.client.get(
            "/v1/projects/project-a/answers/answer-a/evidence-directions"
        )
        assert listed.status_code == 200
        assert {
            (item["sourceId"], item["direction"]) for item in listed.json()
        } == {
            ("a-source", "supporting"),
            ("a-second", "insufficient"),
        }
        assert (
            self.client.get(
                "/v1/projects/project-b/answers/answer-b/evidence-directions"
            ).json()
            == []
        )
