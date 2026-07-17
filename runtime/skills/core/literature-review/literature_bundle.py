#!/usr/bin/env python3
"""Build citation-safe literature artifacts from credential-free search exports."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

CSV_FIELDS = [
    "title", "authors", "year", "source", "DOI", "PMID", "arXiv ID",
    "URL", "abstract or summary", "selection status",
]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(filter(None, (_text(item) for item in value)))
    if isinstance(value, dict):
        return _text(
            value.get("name")
            or value.get("display_name")
            or value.get("author")
            or " ".join(filter(None, [value.get("given"), value.get("family")]))
        )
    return str(value).strip()


def _first(record: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = _text(record.get(key))
        if value:
            return value
    return ""


def _authors(record: dict[str, Any]) -> str:
    value = record.get("authors") or record.get("author") or record.get("authorships")
    if not isinstance(value, list):
        return _text(value)
    names = []
    for item in value:
        if isinstance(item, dict):
            item = item.get("author") or item.get("name") or item.get("display_name")
        if _text(item):
            names.append(_text(item))
    return "; ".join(names)


def _year(record: dict[str, Any]) -> str:
    value = _first(record, "year", "publication_year", "published_year", "date", "publication_date")
    match = re.search(r"\b(19|20)\d{2}\b", value)
    return match.group(0) if match else ""


def _doi(value: str) -> str:
    value = re.sub(r"^https?://doi\.org/", "", value.strip(), flags=re.I)
    return value.lower() if value.startswith("10.") else value


def _arxiv(value: str) -> str:
    value = re.sub(r"^https?://arxiv\.org/(abs|pdf)/", "", value.strip(), flags=re.I)
    return re.sub(r"\.pdf$", "", value, flags=re.I)


def _source_records(payload: Any, fallback: str) -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(payload, list):
        return ((fallback, item) for item in payload if isinstance(item, dict))
    if not isinstance(payload, dict):
        return ()
    source = _first(payload, "source", "provider", "database") or fallback
    records = payload.get("records") or payload.get("results") or payload.get("items") or payload.get("data")
    if isinstance(records, list):
        return ((source, item) for item in records if isinstance(item, dict))
    return ((source, payload),)


def _normalize(source: str, record: dict[str, Any]) -> dict[str, str]:
    source_name = _first(record, "source", "provider", "database") or source
    source_lower = source_name.lower()
    paper_id = _first(record, "paper_id", "id")
    identifier_key = "arXiv ID" if "arxiv" in source_lower else "PMID" if "pubmed" in source_lower else ""
    abstract = _first(record, "abstract", "summary", "description", "snippet")
    inverted = record.get("abstract_inverted_index")
    if not abstract and isinstance(inverted, dict):
        tokens = {position: token for token, positions in inverted.items() for position in positions}
        abstract = " ".join(tokens[position] for position in sorted(tokens))
    return {
        "title": _first(record, "title", "name", "display_name"),
        "authors": _authors(record),
        "year": _year(record),
        "source": source_name,
        "DOI": _doi(_first(record, "doi", "DOI")),
        "PMID": _first(record, "pmid", "PMID", "pubmed_id", "uid") or (paper_id if identifier_key == "PMID" else ""),
        "arXiv ID": _arxiv(_first(record, "arxiv_id", "arxiv", "arXiv ID")) or (_arxiv(paper_id) if identifier_key == "arXiv ID" else ""),
        "URL": _first(record, "url", "URL", "landing_page_url", "link"),
        "abstract or summary": abstract,
        "selection status": _first(record, "selection_status", "status") or "included",
    }


def _dedupe_key(item: dict[str, str]) -> str:
    for field in ("DOI", "PMID", "arXiv ID"):
        if item[field]:
            return f"{field}:{item[field].lower()}"
    title = re.sub(r"\W+", " ", item["title"].lower()).strip()
    return f"title:{title}:{item['year']}"


def load_records(paths: list[Path]) -> list[dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for path in paths:
        raw = path.read_text(encoding="utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = [json.loads(line) for line in raw.splitlines() if line.strip()]
        for source, record in _source_records(payload, path.stem):
            item = _normalize(source, record)
            if not item["title"]:
                continue
            key = _dedupe_key(item)
            previous = merged.get(key)
            if previous is None:
                merged[key] = item
                continue
            sources = {part.strip() for part in previous["source"].split(";") if part.strip()}
            sources.add(item["source"])
            previous["source"] = "; ".join(sorted(sources))
            for field in CSV_FIELDS:
                if not previous[field] and item[field]:
                    previous[field] = item[field]
    return sorted(merged.values(), key=lambda item: (item["year"], item["title"].lower()), reverse=True)


def _bib_escape(value: str) -> str:
    return value.replace("\\", "\\textbackslash{}").replace("{", "\\{").replace("}", "\\}")


def write_bibtex(records: list[dict[str, str]], path: Path) -> None:
    entries = []
    for item in records:
        author = re.sub(r"\W+", "", item["authors"].split(";")[0].split(",")[0].lower()) or "record"
        digest = hashlib.sha1(item["title"].encode("utf-8")).hexdigest()[:6]
        key = f"{author}{item['year'] or 'nd'}{digest}"
        entry_type = "misc" if "arxiv" in item["source"].lower() else "article"
        fields = [("title", item["title"]), ("author", item["authors"]), ("year", item["year"])]
        for name, field in (("doi", "DOI"), ("pmid", "PMID"), ("eprint", "arXiv ID"), ("url", "URL")):
            if item[field]:
                fields.append((name, item[field]))
        fields.append(("note", f"Source: {item['source']}; status: {item['selection status']}"))
        body = ",\n".join(f"  {name} = {{{_bib_escape(value)}}}" for name, value in fields if value)
        entries.append(f"@{entry_type}{{{key},\n{body}\n}}")
    path.write_text("\n\n".join(entries) + ("\n" if entries else ""), encoding="utf-8")


def write_report(records: list[dict[str, str]], path: Path, question: str) -> None:
    sources = sorted({part.strip() for item in records for part in item["source"].split(";") if part.strip()})
    counts = Counter(item["source"] for item in records)
    lines = ["# Literature review", "", f"**Question:** {question}", "", f"**Records retained:** {len(records)}", f"**Sources represented:** {', '.join(sources) if sources else 'none'}", "", "## Coverage and limitations", ""]
    if len(sources) >= 2:
        lines.append("At least two source identities are represented in the supplied search exports.")
    elif len(sources) == 1:
        lines.append("Only one source identity was available; broader coverage was not verified.")
    else:
        lines.append("No source identity was supplied; search provenance is incomplete.")
    lines.extend(["", "Source record counts: " + ", ".join(f"{name} ({count})" for name, count in sorted(counts.items())), "", "## Evidence table", "", "| # | Title | Year | Source | Stable identifiers | Selection |", "|---:|---|---:|---|---|---|"])
    for index, item in enumerate(records, 1):
        ids = "; ".join(filter(None, [f"DOI: {item['DOI']}" if item["DOI"] else "", f"PMID: {item['PMID']}" if item["PMID"] else "", f"arXiv: {item['arXiv ID']}" if item["arXiv ID"] else ""])) or "none"
        title = item["title"].replace("|", "\\|")
        lines.append(f"| {index} | {title} | {item['year'] or 'n/a'} | {item['source']} | {ids} | {item['selection status']} |")
    lines.extend(["", "## Synthesis notes", "", "The table is a normalized evidence inventory. Interpret methods and findings from verified abstracts or full text, not from identifiers alone. Add claim-level synthesis and disagreements here after reviewing the retained records.", "", "## Generated artifacts", "", "- `references/corpus.csv`", "- `references/references.bib`", "- `reports/literature-review.md`"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build(inputs: list[Path], output_dir: Path, question: str) -> tuple[Path, Path, Path]:
    records = load_records(inputs)
    references, reports = output_dir / "references", output_dir / "reports"
    references.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    corpus = references / "corpus.csv"
    with corpus.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(records)
    bibliography = references / "references.bib"
    write_bibtex(records, bibliography)
    report = reports / "literature-review.md"
    write_report(records, report, question)
    return corpus, bibliography, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, required=True, help="JSON or JSONL source export; repeat for each source")
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    parser.add_argument("--question", default="Literature review")
    args = parser.parse_args()
    missing = [str(path) for path in args.input if not path.is_file()]
    if missing:
        parser.error("input file not found: " + ", ".join(missing))
    for path in build(args.input, args.output_dir, args.question):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
