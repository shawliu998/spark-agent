#!/usr/bin/env python3
"""Deterministic tests for the literature live-gate validator and helper."""

from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LiteratureLiveOfflineTest(unittest.TestCase):
    def test_validator_accepts_realistic_multi_source_artifacts_without_fixed_prose(self) -> None:
        live = load(ROOT / "scripts/dev/literature_live.py", "literature_live")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "arxiv.json").write_text(json.dumps({"source": "arxiv", "records": [{"paper_id": "2401.00001", "title": "A reproducible result", "authors": ["A. Author"], "abstract": "An abstract."}]}))
            (root / "pubmed.json").write_text(json.dumps({"source": "pubmed", "records": [{"paper_id": "12345678", "title": "A clinical result", "authors": ["B. Author"], "abstract": "Another abstract."}]}))
            live.literature_bundle.build([root / "arxiv.json", root / "pubmed.json"], root, "A question")
            records = live.validate_artifacts(root)
            self.assertEqual({item["arXiv ID"] for item in records if item["arXiv ID"]}, {"2401.00001"})
            self.assertEqual({item["PMID"] for item in records if item["PMID"]}, {"12345678"})

    def test_validator_rejects_missing_stable_identifier(self) -> None:
        live = load(ROOT / "scripts/dev/literature_live.py", "literature_live_missing_id")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for source in ("arxiv", "pubmed"):
                (root / f"{source}.json").write_text(json.dumps({"source": source, "records": [{"title": source}]}))
            live.literature_bundle.build([root / "arxiv.json", root / "pubmed.json"], root, "A question")
            with self.assertRaisesRegex(RuntimeError, "stable identifier"):
                live.validate_artifacts(root)

    def test_helper_maps_connector_paper_ids_to_source_specific_fields(self) -> None:
        bundle = load(ROOT / "runtime/skills/core/literature-review/literature_bundle.py", "literature_bundle_ids")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "arxiv.json").write_text(json.dumps({"source": "arxiv", "records": [{"paper_id": "2401.00001", "title": "A"}]}))
            (root / "pubmed.json").write_text(json.dumps({"source": "pubmed", "records": [{"paper_id": "12345678", "title": "B"}]}))
            output = root / "out"
            bundle.build([root / "arxiv.json", root / "pubmed.json"], output, "Q")
            with (output / "references/corpus.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            by_title = {row["title"]: row for row in rows}
            self.assertEqual(by_title["A"]["arXiv ID"], "2401.00001")
            self.assertEqual(by_title["B"]["PMID"], "12345678")


if __name__ == "__main__":
    unittest.main()
