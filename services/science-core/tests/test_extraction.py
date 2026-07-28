# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from typing import Any, cast
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from open_science_core.api.extraction import (
    _begin_immediate,
    get_session,
    router,
    seed_default_columns,
)
from open_science_core.app import (
    app as production_app,
)
from open_science_core.app import (
    get_session as app_get_session,
)
from open_science_core.app import (
    require_token,
    settings,
)
from open_science_core.db import Base
from open_science_core.models import (
    AnswerRecord,
    EvidenceSpanRecord,
    ExtractionCellEvidenceRecord,
    ExtractionCellRecord,
    ProjectRecord,
    ScreeningDecisionRecord,
    SourcePageRecord,
    SourceRecord,
)
from open_science_core.workflow._handlers.sources import source_page_manifest_hash


class _RequestClient:
    def request(self, method: str, url: str, **kwargs: Any) -> Response: ...


class TypedTestClient(TestClient):
    def get(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("POST", url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("PUT", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("DELETE", url, **kwargs)

    def options(self, url: str, **kwargs: Any) -> Response:
        return cast(_RequestClient, self).request("OPTIONS", url, **kwargs)


class _CodedSqliteOperationalError(sqlite3.OperationalError):
    sqlite_errorcode: int

    def __init__(self, code: int) -> None:
        super().__init__(f"sqlite failure {code}")
        self.sqlite_errorcode = code


class _FailingSession:
    def __init__(self, error: OperationalError) -> None:
        self.error = error
        self.rollback_called = False

    def execute(self, _statement: object) -> None:
        raise self.error

    def rollback(self) -> None:
        self.rollback_called = True


def _project(project_id: str) -> ProjectRecord:
    return ProjectRecord(
        id=project_id,
        title=project_id,
        description="",
        project_path=f"/tmp/{project_id}",
        research_domain=None,
        execution_mode="safe",
    )


def _source(source_id: str, project_id: str) -> SourceRecord:
    return SourceRecord(
        id=source_id,
        project_id=project_id,
        title=source_id,
        source_kind="pdf",
        authors=[],
        local_path=f"/tmp/{project_id}/{source_id}",
        ingestion_status="ready",
        content_hash=hashlib.sha256(source_id.encode()).hexdigest(),
        page_count=2,
    )


def _page(source_id: str, page_index: int, text: str) -> SourcePageRecord:
    words: list[dict[str, Any]] = []
    x = 10.0
    for value in text.split():
        width = max(8.0, float(len(value) * 5))
        words.append({"text": value, "x0": x, "y0": 20.0, "x1": x + width, "y1": 32.0})
        x += width + 4.0
    return SourcePageRecord(
        source_id=source_id,
        page_index=page_index,
        page_label=str(page_index + 1),
        width=600,
        height=800,
        text=text,
        words=words,
    )


class TestExtractionApi:
    def setup_method(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )

        def enable_foreign_keys(connection: object, _record: object) -> None:
            connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]

        event.listen(self.engine, "connect", enable_foreign_keys)
        Base.metadata.create_all(self.engine)
        with Session(self.engine) as session:
            session.add_all([_project("project-a"), _project("project-b")])
            session.flush()
            session.add_all([_source("source-a", "project-a"), _source("source-b", "project-b")])
            session.flush()
            quote = "This exact local passage supports the confirmed extraction result."
            session.add_all(
                [
                    _page("source-a", 0, f"Introduction. {quote} Closing context."),
                    _page("source-a", 1, f"{quote} Other text. {quote}"),
                    _page("source-b", 0, "A different project page with enough searchable text."),
                ]
            )
            seed_default_columns(session, "project-a")
            seed_default_columns(session, "project-b")
            session.add(
                EvidenceSpanRecord(
                    id="evidence-a",
                    source_id="source-a",
                    page_index=0,
                    page_label="1",
                    text="A local quote",
                    bbox=None,
                    quote_hash="a" * 64,
                    extraction_method="test",
                    confidence=1,
                    verified=True,
                )
            )
            session.add(
                EvidenceSpanRecord(
                    id="evidence-unverified",
                    source_id="source-a",
                    page_index=0,
                    page_label="1",
                    text="An unverified local quote",
                    bbox=None,
                    quote_hash="b" * 64,
                    extraction_method="test",
                    confidence=0.2,
                    verified=False,
                )
            )
            session.commit()
        api = FastAPI()
        api.include_router(router)

        def session_dependency() -> Generator[Session, None, None]:
            with Session(self.engine) as session:
                yield session

        api.dependency_overrides[get_session] = session_dependency
        self.client = TypedTestClient(api)

    def _column(self, project_id: str = "project-a") -> str:
        matrix = self.client.get(f"/v1/projects/{project_id}/extraction").json()
        assert [item["name"] for item in matrix["columns"]][:3] == [
            "Summary",
            "Population",
            "Outcome",
        ]
        return str(matrix["columns"][0]["id"])

    def test_matrix_is_deterministic_and_default_columns_have_no_cells(self) -> None:
        self._column()
        assert self.client.get("/v1/projects/project-a/extraction").json()["cells"] == []

    def test_exact_quote_creates_verified_evidence_and_confirmed_cell(self) -> None:
        quote = "This exact local passage supports the confirmed extraction result."
        with Session(self.engine) as session:
            source = session.get(SourceRecord, "source-a")
            manifest = source_page_manifest_hash(session, "source-a")
            assert source is not None and manifest is not None
            source_hash = source.content_hash
        created = self.client.post(
            "/v1/projects/project-a/sources/source-a/evidence-spans",
            headers={"Idempotency-Key": "quote-1"},
            json={
                "pageIndex": 0,
                "quoteText": quote,
                "expectedSourceContentHash": source_hash,
                "expectedPageManifestHash": manifest[0],
            },
        )
        assert created.status_code == 201, created.text
        evidence = created.json()
        assert evidence["verified"] is True
        assert evidence["pageIndex"] == 0
        assert evidence["text"] == quote
        assert evidence["bbox"] is not None
        column_id = self._column()
        cell = self.client.put(
            f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
            json={
                "value": quote,
                "reviewStatus": "confirmed",
                "evidenceIds": [evidence["id"]],
                "expectedVersion": 0,
            },
        )
        assert cell.status_code == 200
        assert cell.json()["reviewStatus"] == "confirmed"
        assert cell.json()["evidenceIds"] == [evidence["id"]]

    def test_confirmed_extraction_builds_idempotent_cited_brief_with_original_evidence(
        self,
    ) -> None:
        quote = "This exact local passage supports the confirmed extraction result."
        with Session(self.engine) as session:
            source = session.get(SourceRecord, "source-a")
            manifest = source_page_manifest_hash(session, "source-a")
            assert source is not None and manifest is not None
            source_hash = source.content_hash
        evidence_response = self.client.post(
            "/v1/projects/project-a/sources/source-a/evidence-spans",
            headers={"Idempotency-Key": "brief-evidence"},
            json={
                "pageIndex": 0,
                "quoteText": quote,
                "expectedSourceContentHash": source_hash,
                "expectedPageManifestHash": manifest[0],
            },
        )
        evidence = evidence_response.json()
        column_id = self._column()
        cell = self.client.put(
            f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
            json={
                "value": quote,
                "reviewStatus": "confirmed",
                "evidenceIds": [evidence["id"]],
                "expectedVersion": 0,
            },
        )
        assert cell.status_code == 200
        first = self.client.post(
            "/v1/projects/project-a/extraction/cited-brief",
            headers={"Idempotency-Key": "brief-create-1"},
        )
        second = self.client.post(
            "/v1/projects/project-a/extraction/cited-brief",
            headers={"Idempotency-Key": "brief-create-2"},
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 201
        assert first.json() == second.json()
        result = first.json()
        assert result["generator"] == "confirmed-extraction-cited-brief-v1"
        assert result["integrityStatus"] == "unfrozen"
        cited = result["claims"][0]["evidence"][0]
        assert cited["evidenceId"] == evidence["id"]
        assert cited["sourceId"] == "source-a"
        assert cited["pageIndex"] == 0
        assert cited["text"] == quote
        refreshed_cell = self.client.put(
            f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
            json={
                "value": f"Updated finding: {quote}",
                "reviewStatus": "confirmed",
                "evidenceIds": [evidence["id"]],
                "expectedVersion": 1,
            },
        )
        assert refreshed_cell.status_code == 200
        refreshed = self.client.post(
            "/v1/projects/project-a/extraction/cited-brief",
            headers={"Idempotency-Key": "brief-refresh"},
        )
        assert refreshed.status_code == 201
        assert refreshed.json()["answerId"] != result["answerId"]
        assert refreshed.json()["claims"][0]["statement"].startswith("Summary: Updated finding:")
        with Session(self.engine) as session:
            assert int(session.scalar(select(func.count(AnswerRecord.id))) or 0) == 2

    def test_cited_brief_fails_closed_without_eligible_or_verified_evidence(self) -> None:
        empty = self.client.post(
            "/v1/projects/project-a/extraction/cited-brief",
            headers={"Idempotency-Key": "brief-empty"},
        )
        assert empty.status_code == 422
        column_id = self._column()
        unverified = self.client.put(
            f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
            json={
                "value": "Human-confirmed text with unverified supporting evidence",
                "reviewStatus": "confirmed",
                "evidenceIds": ["evidence-unverified"],
                "expectedVersion": 0,
            },
        )
        assert unverified.status_code == 200
        rejected = self.client.post(
            "/v1/projects/project-a/extraction/cited-brief",
            headers={"Idempotency-Key": "brief-unverified"},
        )
        assert rejected.status_code == 409
        with Session(self.engine) as session:
            assert int(session.scalar(select(func.count(AnswerRecord.id))) or 0) == 0

    def test_exact_quote_rejects_wrong_missing_ambiguous_and_stale_inputs_without_write(
        self,
    ) -> None:
        quote = "This exact local passage supports the confirmed extraction result."
        with Session(self.engine) as session:
            source = session.get(SourceRecord, "source-a")
            manifest = source_page_manifest_hash(session, "source-a")
            assert source is not None and manifest is not None
            source_hash = source.content_hash
            initial = int(session.scalar(select(func.count(EvidenceSpanRecord.id))) or 0)
        base = {
            "quoteText": quote,
            "expectedSourceContentHash": source_hash,
            "expectedPageManifestHash": manifest[0],
        }
        cases = [
            {**base, "pageIndex": 9},
            {**base, "pageIndex": 0, "quoteText": "This exact quote is absent from the requested source page."},
            {**base, "pageIndex": 1},
            {**base, "pageIndex": 0, "expectedSourceContentHash": "f" * 64},
            {**base, "pageIndex": 0, "expectedPageManifestHash": "e" * 64},
        ]
        for index, payload in enumerate(cases):
            response = self.client.post(
                "/v1/projects/project-a/sources/source-a/evidence-spans",
                headers={"Idempotency-Key": f"reject-{index}"},
                json=payload,
            )
            assert response.status_code in {409, 422}
        with Session(self.engine) as session:
            assert int(session.scalar(select(func.count(EvidenceSpanRecord.id))) or 0) == initial

    def test_create_column_and_cell_confirm_evidence_and_delete_cas(self) -> None:
        created = self.client.post(
            "/v1/projects/project-a/extraction/columns",
            json={"name": " Design ", "instructions": " compare "},
        )
        assert created.status_code == 201
        assert created.json()["orderIndex"] == 3
        column_id = self._column()
        response = self.client.put(
            f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
            json={
                "value": "  adults ",
                "reviewStatus": "confirmed",
                "evidenceIds": ["evidence-a"],
                "expectedVersion": 0,
            },
        )
        assert response.status_code == 200
        cell = response.json()
        assert cell["value"] == "adults" and cell["reviewStatus"] == "confirmed"
        assert cell["evidenceIds"] == ["evidence-a"] and cell["rowVersion"] == 1
        assert (
            self.client.delete(
                f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
                json={"expectedVersion": 2},
            ).status_code
            == 409
        )
        assert (
            self.client.delete(
                f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
                json={"expectedVersion": 1},
            ).status_code
            == 204
        )

    def test_rejects_cross_project_and_cross_source_evidence_and_excluded_writes(self) -> None:
        column_id = self._column()
        cross = self.client.put(
            f"/v1/projects/project-a/extraction/cells/source-b/{column_id}",
            json={"value": "x", "expectedVersion": 0},
        )
        assert cross.status_code == 404
        bad_evidence = self.client.put(
            f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
            json={"value": "x", "evidenceIds": ["missing"], "expectedVersion": 0},
        )
        assert bad_evidence.status_code == 422
        with Session(self.engine) as session:
            session.add(
                ScreeningDecisionRecord(
                    id="excluded",
                    project_id="project-a",
                    source_id="source-a",
                    decision="exclude",
                    reason=None,
                    criteria_version="screening-v1",
                    row_version=1,
                )
            )
            session.commit()
        excluded = self.client.put(
            f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
            json={"value": "x", "expectedVersion": 0},
        )
        assert excluded.status_code == 422

    def test_rejects_strict_payloads_and_non_ready_sources(self) -> None:
        column_id = self._column()
        malformed = self.client.put(
            f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
            json={"value": "x", "expectedVersion": True},
        )
        assert malformed.status_code == 422
        extra = self.client.put(
            f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
            json={"value": "x", "expectedVersion": 0, "invented": "no"},
        )
        assert extra.status_code == 422
        with Session(self.engine) as session:
            source = session.get(SourceRecord, "source-a")
            assert source is not None
            source.ingestion_status = "parsing"
            session.commit()
        assert (
            self.client.put(
                f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
                json={"value": "x", "expectedVersion": 0},
            ).status_code
            == 422
        )

    def test_stale_update_and_failed_evidence_replacement_preserve_canonical_cell(self) -> None:
        column_id = self._column()
        created = self.client.put(
            f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
            json={"value": "original", "evidenceIds": ["evidence-a"], "expectedVersion": 0},
        )
        assert created.status_code == 200
        stale = self.client.put(
            f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
            json={"value": "stale", "expectedVersion": 0},
        )
        assert stale.status_code == 409
        invalid_evidence = self.client.put(
            f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
            json={"value": "changed", "evidenceIds": ["missing"], "expectedVersion": 1},
        )
        assert invalid_evidence.status_code == 422
        matrix = self.client.get("/v1/projects/project-a/extraction").json()
        assert matrix["cells"] == [
            {
                **created.json(),
            }
        ]

    def test_human_confirmation_is_independent_from_evidence_verification(self) -> None:
        column_id = self._column()
        response = self.client.put(
            f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
            json={
                "value": "human checked, but evidence remains unverified",
                "reviewStatus": "confirmed",
                "evidenceIds": ["evidence-unverified"],
                "expectedVersion": 0,
            },
        )
        assert response.status_code == 200
        assert response.json()["reviewStatus"] == "confirmed"
        with Session(self.engine) as session:
            evidence = session.get(EvidenceSpanRecord, "evidence-unverified")
            assert evidence is not None and evidence.verified is False

    def test_database_links_unlink_when_evidence_is_deleted_without_deleting_cell(self) -> None:
        column_id = self._column()
        response = self.client.put(
            f"/v1/projects/project-a/extraction/cells/source-a/{column_id}",
            json={"value": "x", "evidenceIds": ["evidence-a"], "expectedVersion": 0},
        )
        assert response.status_code == 200
        with Session(self.engine) as session:
            session.delete(session.get(EvidenceSpanRecord, "evidence-a"))
            session.commit()
            assert session.scalar(select(ExtractionCellRecord)) is not None
            assert session.scalar(select(ExtractionCellEvidenceRecord)) is None

    def test_database_rejects_cross_project_column_pair(self) -> None:
        column_id = self._column("project-b")
        with Session(self.engine) as session:
            session.add(
                ExtractionCellRecord(
                    id="bad",
                    project_id="project-a",
                    source_id="source-a",
                    column_id=column_id,
                    value="bad",
                    review_status="unreviewed",
                    row_version=1,
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
            else:
                raise AssertionError("Cross-project extraction column ownership was not enforced")


def test_tauri_origin_delete_preflight_is_permitted() -> None:
    """The SDK's DELETE request must not fail before bearer auth at browser preflight."""
    cors = next(
        middleware
        for middleware in production_app.user_middleware
        if middleware.cls is CORSMiddleware
    )
    assert "DELETE" in cast(list[str], cors.kwargs["allow_methods"])
    client = TypedTestClient(production_app, raise_server_exceptions=False)
    response = client.options(
        "/v1/projects/project-a/extraction/cells/source-a/column-a",
        headers={
            "Origin": "tauri://localhost",
            "Access-Control-Request-Method": "DELETE",
        },
    )
    assert response.status_code == 200
    assert "DELETE" in response.headers["access-control-allow-methods"]


def test_project_create_api_seeds_exactly_three_empty_extraction_columns() -> None:
    """Exercise the production project transaction, then its production matrix route."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def session_dependency() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    original_overrides = dict(production_app.dependency_overrides)
    try:
        production_app.dependency_overrides[app_get_session] = session_dependency
        production_app.dependency_overrides[get_session] = session_dependency
        production_app.dependency_overrides[require_token] = lambda: None
        with (
            TemporaryDirectory() as directory,
            patch("open_science_core.app.settings", replace(settings, data_dir=Path(directory))),
        ):
            client = TypedTestClient(production_app, raise_server_exceptions=False)
            created = client.post("/v1/projects", json={"title": "New project"})
            assert created.status_code == 200, created.text
            project_id = created.json()["id"]
            matrix = client.get(f"/v1/projects/{project_id}/extraction")
            assert matrix.status_code == 200, matrix.text
            assert [column["name"] for column in matrix.json()["columns"]] == [
                "Summary",
                "Population",
                "Outcome",
            ]
            assert matrix.json()["cells"] == []
    finally:
        production_app.dependency_overrides = original_overrides
        engine.dispose()


def _wal_client(database_path: Path) -> tuple[TypedTestClient, Engine]:
    engine = create_engine(
        f"sqlite:///{database_path}", connect_args={"check_same_thread": False, "timeout": 2.0}
    )

    def configure_connection(connection: object, _record: object) -> None:
        connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[attr-defined]
        connection.execute("PRAGMA journal_mode=WAL")  # type: ignore[attr-defined]
        connection.execute("PRAGMA busy_timeout=2000")  # type: ignore[attr-defined]

    event.listen(engine, "connect", configure_connection)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(_project("project-wal"))
        session.flush()
        session.add(_source("source-wal", "project-wal"))
        seed_default_columns(session, "project-wal")
        session.commit()
    api = FastAPI()
    api.include_router(router)

    def session_dependency() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    api.dependency_overrides[get_session] = session_dependency
    return TypedTestClient(api), engine


def _race_cell(
    client: TypedTestClient,
    barrier: Barrier,
    value: str,
    expected_version: int,
    method: str = "PUT",
) -> Response:
    matrix = client.get("/v1/projects/project-wal/extraction").json()
    column_id = matrix["columns"][0]["id"]
    barrier.wait()
    if method == "DELETE":
        return client.delete(
            f"/v1/projects/project-wal/extraction/cells/source-wal/{column_id}",
            json={"expectedVersion": expected_version},
        )
    return client.put(
        f"/v1/projects/project-wal/extraction/cells/source-wal/{column_id}",
        json={"value": value, "expectedVersion": expected_version},
    )


def test_wal_create_update_and_delete_races_have_one_cas_winner() -> None:
    with TemporaryDirectory() as directory:
        client, engine = _wal_client(Path(directory) / "extraction.sqlite3")
        for expected_version in (0, 1):
            barrier = Barrier(2)
            with ThreadPoolExecutor(max_workers=2) as executor:
                responses = [
                    future.result(timeout=5)
                    for future in [
                        executor.submit(_race_cell, client, barrier, value, expected_version)
                        for value in ("one", "two")
                    ]
                ]
            assert sorted(response.status_code for response in responses) == [200, 409]
        matrix = client.get("/v1/projects/project-wal/extraction").json()
        assert matrix["cells"][0]["rowVersion"] == 2
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = [
                future.result(timeout=5)
                for future in [
                    executor.submit(_race_cell, client, barrier, "", 2, "DELETE"),
                    executor.submit(_race_cell, client, barrier, "three", 2),
                ]
            ]
        assert sorted(response.status_code for response in responses) in ([200, 409], [204, 409])
        engine.dispose()


def test_begin_immediate_maps_only_sqlite_busy_and_locked_to_conflict() -> None:
    for code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_BUSY_SNAPSHOT, sqlite3.SQLITE_LOCKED):
        session = _FailingSession(
            OperationalError("BEGIN IMMEDIATE", {}, _CodedSqliteOperationalError(code))
        )
        try:
            _begin_immediate(cast(Session, session))
        except HTTPException as error:
            assert error.status_code == 409 and session.rollback_called
        else:
            raise AssertionError("SQLite lock was not mapped to 409")
    for original in (
        _CodedSqliteOperationalError(sqlite3.SQLITE_IOERR),
        RuntimeError("not sqlite"),
    ):
        session = _FailingSession(OperationalError("BEGIN IMMEDIATE", {}, original))
        try:
            _begin_immediate(cast(Session, session))
        except OperationalError:
            assert session.rollback_called
        else:
            raise AssertionError("Non-lock error was incorrectly mapped to 409")
