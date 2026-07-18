#!/usr/bin/env python3
"""Run the real pinned paper-search MCP literature gate when available."""

from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "runtime/skills/core/literature-review/literature_bundle.py"
sys.path.insert(0, str(BUNDLE.parent))
import literature_bundle  # noqa: E402

PINNED_VERSION = "0.1.4"
SOURCES = ("arxiv", "pubmed")
QUERY = "scientific machine learning"


def _rpc(process: subprocess.Popen[str], request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()
    deadline = __import__("time").monotonic() + 45
    while __import__("time").monotonic() < deadline:
        ready, _, _ = select.select([process.stdout], [], [], 1)
        if not ready:
            continue
        line = process.stdout.readline()
        if not line:
            break
        try:
            response = json.loads(line)
        except json.JSONDecodeError:
            continue
        if response.get("id") == request_id:
            if "error" in response:
                raise RuntimeError(str(response["error"]))
            return response.get("result", {})
    raise RuntimeError(f"MCP request timed out: {method}")


def _notify(process: subprocess.Popen[str], method: str, params: dict[str, Any] | None = None) -> None:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()


def _tool_value(result: dict[str, Any]) -> Any:
    if "structuredContent" in result:
        return result["structuredContent"]
    for content in result.get("content", []):
        if content.get("type") != "text":
            continue
        try:
            return json.loads(content["text"])
        except (KeyError, json.JSONDecodeError):
            continue
    raise RuntimeError("MCP tool returned no JSON content")


def _command() -> list[str] | None:
    override = os.environ.get("PAPER_SEARCH_MCP_COMMAND")
    if override:
        command = override.split()
        if f"paper-search-mcp=={PINNED_VERSION}" not in command:
            return None
        return command
    try:
        from importlib.metadata import version

        if version("paper-search-mcp") != PINNED_VERSION:
            return None
    except Exception:
        return None
    if shutil.which("paper-search-mcp"):
        return ["paper-search-mcp"]
    if shutil.which("paper-search"):
        return ["paper-search"]
    return [sys.executable, "-m", "paper_search_mcp.server"]


def _http_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "Spark-Agent-literature-live/0.1"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def validate_identifier(source: str, identifier: str) -> None:
    if source == "arxiv":
        encoded = urllib.parse.quote(identifier, safe=".")
        xml_url = f"https://export.arxiv.org/api/query?id_list={encoded}"
        request = urllib.request.Request(xml_url, headers={"User-Agent": "Spark-Agent-literature-live/0.1"})
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
        if f"/abs/{identifier}" not in body and f"/abs/{identifier.split('v')[0]}" not in body:
            raise RuntimeError(f"arXiv identifier did not resolve: {identifier}")
    elif source == "pubmed":
        data = _http_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + urllib.parse.urlencode({"db": "pubmed", "id": identifier, "retmode": "json"}))
        if identifier not in data.get("result", {}):
            raise RuntimeError(f"PubMed identifier did not resolve: {identifier}")
    else:
        raise RuntimeError(f"unsupported live source: {source}")


def validate_artifacts(output: Path, minimum_sources: int = 2) -> list[dict[str, str]]:
    corpus, bibliography, report = output / "references/corpus.csv", output / "references/references.bib", output / "reports/literature-review.md"
    if not all(path.is_file() and path.stat().st_size > 0 for path in (corpus, bibliography, report)):
        raise RuntimeError("required literature artifacts are missing or empty")
    records = literature_bundle.load_records([output / "arxiv.json", output / "pubmed.json"])
    sources = {source.strip() for item in records for source in item["source"].split(";") if source.strip()}
    if len(sources) < minimum_sources:
        raise RuntimeError(f"artifact corpus has fewer than {minimum_sources} source identities: {sorted(sources)}")
    if not records:
        raise RuntimeError("artifact corpus contains no records")
    for item in records:
        if not (item["DOI"] or item["PMID"] or item["arXiv ID"]):
            raise RuntimeError(f"record has no stable identifier: {item['title']}")
    if "At least two source identities" not in report.read_text(encoding="utf-8"):
        raise RuntimeError("report does not document multi-source coverage")
    return records


def main() -> int:
    command = _command()
    if command is None:
        print("SKIP: paper-search-mcp==0.1.4 is not installed in the active Python runtime")
        return 0
    try:
        with tempfile.TemporaryDirectory(prefix="spark-literature-live-") as temporary:
            root = Path(temporary)
            process = subprocess.Popen(command, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            try:
                _rpc(process, 1, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "spark-agent-literature-live", "version": "0.1"}})
                _notify(process, "notifications/initialized")
                for index, source in enumerate(SOURCES, 3):
                    result = _tool_value(_rpc(process, index, "tools/call", {"name": f"search_{source}", "arguments": {"query": QUERY, "max_results": 3}}))
                    if not isinstance(result, list) or not result:
                        raise RuntimeError(f"{source} returned no records")
                    (root / f"{source}.json").write_text(json.dumps({"source": source, "records": result}), encoding="utf-8")
                outputs = literature_bundle.build([root / "arxiv.json", root / "pubmed.json"], root, QUERY)
                records = validate_artifacts(root)
                for item in records:
                    source_name = item["source"].lower()
                    source = "arxiv" if item["arXiv ID"] and "arxiv" in source_name else "pubmed" if item["PMID"] and "pubmed" in source_name else ""
                    identifier = item["arXiv ID"] or item["PMID"]
                    validate_identifier(source, identifier)
                print("PASS: real paper-search-mcp==0.1.4 MCP search covered arxiv and pubmed")
                print("PASS: validated real stable identifiers and artifacts: " + ", ".join(str(path.relative_to(root)) for path in outputs))
                return 0
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    except (OSError, urllib.error.URLError, TimeoutError) as error:
        print(f"SKIP: paper-search-mcp live gate unavailable: {error}")
        return 0
    except RuntimeError as error:
        print(f"ERROR: paper-search-mcp live gate failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
