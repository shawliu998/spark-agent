#!/usr/bin/env python3
"""Focused tests for bundled skill metadata and literature artifacts."""

from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = ROOT / "scripts" / "dev" / "validate_skills.py"
BUNDLE_PATH = ROOT / "runtime" / "skills" / "core" / "literature-review" / "literature_bundle.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SkillManifestTest(unittest.TestCase):
    def test_manifest_and_frontmatter_are_loadable(self) -> None:
        validator = load_module(VALIDATOR_PATH, "skill_validator")
        self.assertEqual(validator.validate_manifest(), [])
        manifest = json.loads((ROOT / "runtime/skills/manifest.json").read_text())
        self.assertGreaterEqual(len(manifest["skills"]), 20)
        self.assertEqual(
            manifest["upstreamReference"]["commit"],
            "e9844a49f1f4d93cbf5f88b8f4880c003adc6e61",
        )

    def test_literature_bundle_writes_required_outputs_and_deduplicates(self) -> None:
        bundle = load_module(BUNDLE_PATH, "literature_bundle")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "openalex.json").write_text(json.dumps({"source": "OpenAlex", "records": [{
                "title": "A useful paper", "authors": [{"display_name": "A Researcher"}],
                "publication_year": 2024, "doi": "https://doi.org/10.1000/ABC", "abstract": "Evidence.",
            }]}))
            (root / "pubmed.json").write_text(json.dumps({"source": "PubMed", "records": [{
                "title": "A useful paper", "authors": ["A Researcher"], "year": 2024,
                "doi": "10.1000/abc", "pmid": "12345", "url": "https://pubmed.ncbi.nlm.nih.gov/12345/",
            }, {"title": "Another paper", "year": 2023, "pmid": "67890"}]}))
            output = root / "project"
            paths = bundle.build([root / "openalex.json", root / "pubmed.json"], output, "Test question")
            self.assertEqual([path.relative_to(output).as_posix() for path in paths], [
                "references/corpus.csv", "references/references.bib", "reports/literature-review.md",
            ])
            with (output / "references/corpus.csv").open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["source"], "PubMed")
            self.assertIn("OpenAlex; PubMed", rows[0]["source"])
            self.assertIn("At least two source identities", (output / "reports/literature-review.md").read_text())
            bibliography = (output / "references/references.bib").read_text()
            self.assertIn("doi = {10.1000/abc}", bibliography)
            self.assertIn("pmid = {12345}", bibliography)


if __name__ == "__main__":
    unittest.main()
