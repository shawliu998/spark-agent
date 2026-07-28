from __future__ import annotations

import hashlib
import importlib
import json
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import pytest
import requests

_VENDOR_DIR = Path(__file__).parents[1] / "vendor" / "paper-search-mcp"
_WHEEL = _VENDOR_DIR / "paper_search_mcp-0.1.4+spark.3-py3-none-any.whl"
_PROVENANCE = _VENDOR_DIR / "provenance.json"


class _Response:
    def __init__(
        self,
        status_code: int,
        payload: object,
        *,
        headers: Mapping[str, str] | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = dict(headers or {})
        self.stream_error = stream_error
        self.closed = False

    def iter_content(self, *, chunk_size: int) -> list[bytes]:
        del chunk_size
        if self.stream_error is not None:
            raise self.stream_error
        if isinstance(self._payload, bytes):
            return [self._payload]
        if isinstance(self._payload, Exception):
            raise self._payload
        return [json.dumps(self._payload).encode("utf-8")]

    def close(self) -> None:
        self.closed = True


class _Session:
    def __init__(self, response: _Response | Exception) -> None:
        self.response = response
        self.headers: dict[str, str] = {}
        self.calls: list[tuple[str, Mapping[str, object], object]] = []

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, object],
        timeout: object,
        stream: bool,
    ) -> _Response:
        self.calls.append((url, params, (timeout, stream)))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _fork_types() -> tuple[type[Any], type[Exception]]:
    crossref = importlib.import_module(
        "paper_search_mcp.academic_platforms.crossref"
    )
    errors = importlib.import_module("paper_search_mcp.provider_errors")
    return cast(type[Any], crossref.CrossRefSearcher), cast(
        type[Exception],
        errors.ProviderError,
    )


def _search(response: _Response | Exception) -> tuple[list[Any], _Session]:
    searcher_type, _ = _fork_types()
    session = _Session(response)
    searcher = searcher_type(session=session)
    return cast(list[Any], searcher.search("bounded question", max_results=2)), session


def _openalex_types() -> tuple[type[Any], type[Exception]]:
    openalex = importlib.import_module(
        "paper_search_mcp.academic_platforms.openalex"
    )
    errors = importlib.import_module("paper_search_mcp.provider_errors")
    return cast(type[Any], openalex.OpenAlexSearcher), cast(
        type[Exception], errors.ProviderError
    )


def _search_openalex(
    response: _Response | Exception,
    *,
    query: str = "bounded question",
) -> tuple[list[Any], _Session]:
    searcher_type, _ = _openalex_types()
    session = _Session(response)
    searcher = searcher_type(session=session)
    return cast(list[Any], searcher.search(query, max_results=2)), session


def test_vendored_wheel_matches_bound_provenance_and_metadata() -> None:
    provenance = cast(
        dict[str, object],
        json.loads(_PROVENANCE.read_text(encoding="utf-8")),
    )
    expected_hashes = {
        "paper_search_mcp-0.1.4.tar.gz": provenance["upstreamSdistSha256"],
        "spark.patch": provenance["patchSha256"],
        "LICENSE": provenance["licenseSha256"],
        _WHEEL.name: provenance["wheelSha256"],
    }
    assert {
        name: hashlib.sha256((_VENDOR_DIR / name).read_bytes()).hexdigest()
        for name in expected_hashes
    } == expected_hashes
    with zipfile.ZipFile(_WHEEL) as archive:
        metadata = archive.read(
            "paper_search_mcp-0.1.4+spark.3.dist-info/METADATA"
        ).decode("utf-8")
        names = set(archive.namelist())
    assert "Version: 0.1.4+spark.3" in metadata
    assert "paper_search_mcp/provider_errors.py" in names
    assert "paper_search_mcp-0.1.4+spark.3.dist-info/licenses/LICENSE" in names


def test_crossref_valid_empty_response_is_the_only_empty_success() -> None:
    papers, session = _search(
        _Response(200, {"status": "ok", "message": {"items": []}})
    )

    assert papers == []
    assert session.calls == [
        (
            "https://api.crossref.org/works",
            {
                "query": "bounded question",
                "rows": 2,
                "sort": "relevance",
                "order": "desc",
            },
            ((10, 30), True),
        )
    ]


def test_crossref_valid_result_preserves_existing_mcp_shape() -> None:
    papers, _ = _search(
        _Response(
            200,
            {
                "status": "ok",
                "message": {
                    "items": [
                        {
                            "DOI": "10.1000/test",
                            "title": ["Bounded result"],
                            "author": [{"given": "Ada", "family": "Lovelace"}],
                            "issued": {"date-parts": [[2024, 1, 2]]},
                            "URL": "https://doi.org/10.1000/test",
                        }
                    ]
                },
            },
        )
    )

    assert len(papers) == 1
    assert papers[0].to_dict()["paper_id"] == "10.1000/test"
    assert papers[0].to_dict()["title"] == "Bounded result"


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (requests.Timeout(), "connector-unavailable"),
        (requests.ConnectionError(), "connector-unavailable"),
        (_Response(429, {}), "rate-limited"),
        (_Response(503, {}), "connector-unavailable"),
        (_Response(403, {}), "provider-rejected"),
        (_Response(200, b"{"), "provider-response-invalid"),
        (
            _Response(200, {"status": "error", "message": {}}),
            "provider-response-invalid",
        ),
        (
            _Response(
                200,
                {
                    "status": "ok",
                    "message": {"items": [{"title": cast(list[object], [])}]},
                },
            ),
            "provider-response-invalid",
        ),
    ],
)
def test_crossref_failures_never_collapse_to_empty_results(
    response: _Response | Exception,
    code: str,
) -> None:
    _, provider_error = _fork_types()
    with pytest.raises(provider_error) as error:
        _search(response)
    assert getattr(error.value, "code") == code
    assert str(error.value) == f"PAPER_SEARCH_MCP_ERROR:{code}"


@pytest.mark.parametrize(
    "response",
    [
        _Response(200, {}, headers={"Content-Length": str(4 * 1024 * 1024 + 1)}),
        _Response(200, {}, headers={"Content-Length": "invalid"}),
        _Response(200, b"x" * (4 * 1024 * 1024 + 1)),
    ],
)
def test_crossref_rejects_oversized_or_untrusted_response_lengths(
    response: _Response,
) -> None:
    _, provider_error = _fork_types()
    with pytest.raises(provider_error) as error:
        _search(response)
    assert getattr(error.value, "code") == "provider-response-invalid"
    assert response.closed is True


def test_crossref_stream_failure_is_not_an_empty_success() -> None:
    response = _Response(200, {}, stream_error=requests.ConnectionError())
    _, provider_error = _fork_types()
    with pytest.raises(provider_error) as error:
        _search(response)
    assert getattr(error.value, "code") == "connector-unavailable"
    assert response.closed is True


def test_openalex_valid_result_preserves_existing_mcp_shape() -> None:
    papers, session = _search_openalex(
        _Response(
            200,
            {
                "results": [
                    {
                        "id": "https://openalex.org/W123",
                        "title": "Bounded result",
                        "authorships": [
                            {"author": {"display_name": "Ada Lovelace"}}
                        ],
                        "doi": "https://doi.org/10.1000/test",
                        "publication_date": "2024-01-02",
                        "primary_location": {
                            "landing_page_url": "https://example.test/paper",
                            "pdf_url": "https://example.test/paper.pdf",
                        },
                        "concepts": [{"display_name": "Computing"}],
                        "cited_by_count": 7,
                    }
                ]
            },
        )
    )

    assert [paper.to_dict()["paper_id"] for paper in papers] == ["W123"]
    assert papers[0].to_dict()["title"] == "Bounded result"
    assert session.headers["User-Agent"].startswith("Spark-Agent/")
    assert session.calls == [
        (
            "https://api.openalex.org/works",
            {"search": "bounded question", "per_page": 2},
            ((10, 30), True),
        )
    ]


def test_openalex_valid_empty_response_is_the_only_empty_success() -> None:
    papers, session = _search_openalex(_Response(200, {"results": []}))

    assert papers == []
    assert session.calls[0][2] == ((10, 30), True)


def test_openalex_removes_unsupported_wildcards_from_research_questions() -> None:
    papers, session = _search_openalex(
        _Response(200, {"results": []}),
        query="How are hallucinations evaluated?  * ",
    )

    assert papers == []
    assert session.calls[0][1] == {
        "search": "How are hallucinations evaluated",
        "per_page": 2,
    }


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (requests.Timeout(), "connector-unavailable"),
        (_Response(429, {}), "rate-limited"),
        (_Response(503, {}), "connector-unavailable"),
        (_Response(403, {}), "provider-rejected"),
        (_Response(200, b"{"), "provider-response-invalid"),
        (_Response(200, {}), "provider-response-invalid"),
        (_Response(200, {"results": {}}), "provider-response-invalid"),
        (_Response(200, {"results": [{"id": "W123"}]}), "provider-response-invalid"),
        (_Response(200, {}, stream_error=requests.ConnectionError()), "connector-unavailable"),
        (
            _Response(200, b"x" * (4 * 1024 * 1024 + 1)),
            "provider-response-invalid",
        ),
        (
            _Response(200, {}, headers={"Content-Length": str(4 * 1024 * 1024 + 1)}),
            "provider-response-invalid",
        ),
    ],
)
def test_openalex_failures_never_collapse_to_empty_results(
    response: _Response | Exception,
    code: str,
) -> None:
    _, provider_error = _openalex_types()
    with pytest.raises(provider_error) as error:
        _search_openalex(response)
    assert getattr(error.value, "code") == code
    assert str(error.value) == f"PAPER_SEARCH_MCP_ERROR:{code}"
    if isinstance(response, _Response):
        assert response.closed is True
