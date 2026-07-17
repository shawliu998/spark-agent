#!/usr/bin/env python3
"""Offline tests for the pinned K-Dense selection and provenance contract."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts" / "dev" / "validate_kdense_skills.py"
EXTRACTOR = ROOT / "scripts" / "dev" / "extract_kdense_skills.py"
MANIFEST = ROOT / "runtime" / "skills" / "kdense-curated-manifest.json"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class KDenseSelectionTest(unittest.TestCase):
    def test_curated_selection_is_pinned_and_loadable(self) -> None:
        validator = load_module(VALIDATOR, "validate_kdense_skills")
        issues, manifest = validator.validate_manifest()
        self.assertEqual(issues, [])
        assert manifest is not None
        self.assertEqual(len(manifest["skills"]), 30)
        self.assertEqual(manifest["source"]["license"], "MIT")
        self.assertEqual(manifest["source"]["commit"], "3f825caafe149b7853ec8c4d1dd7f4553ea6b2a5")

    def test_duplicate_or_core_collision_fails_closed(self) -> None:
        validator = load_module(VALIDATOR, "validate_kdense_skills")
        manifest = json.loads(MANIFEST.read_text())
        manifest["skills"][1]["name"] = "anndata"
        manifest["skills"][1]["path"] = "skills/anndata"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(json.dumps(manifest))
            issues, _ = validator.validate_manifest(path)
        self.assertTrue(any("duplicate skill anndata" in issue for issue in issues))

    def test_verified_archive_extracts_only_the_selection(self) -> None:
        extractor = load_module(EXTRACTOR, "extract_kdense_skills")
        validator = load_module(VALIDATOR, "validate_kdense_skills")
        manifest = json.loads(MANIFEST.read_text())
        manifest["source"]["commit"] = "a" * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "source.tar.gz"
            archive_root = f"scientific-agent-skills-{manifest['source']['commit']}"
            source = root / archive_root
            (source / "LICENSE.md").parent.mkdir(parents=True)
            (source / "LICENSE.md").write_text("MIT\n")
            for entry in manifest["skills"]:
                (source / entry["path"] / "SKILL.md").parent.mkdir(parents=True)
                (source / entry["path"] / "SKILL.md").write_text(f"---\nname: {entry['name']}\n---\n")
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(source, arcname=archive_root)
            manifest["source"]["archiveSha256"] = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            output = root / "output"
            extractor.extract(archive_path, manifest_path, output)
            self.assertEqual(validator.validate_pack(output, manifest), [])
            self.assertFalse((output / "skills").exists())
            self.assertEqual(
                {path.name for path in output.iterdir() if path.is_dir()},
                {entry["name"] for entry in manifest["skills"]},
            )

    def test_source_lock_matches_release_integrity_manifest(self) -> None:
        manifest = json.loads(MANIFEST.read_text())
        source = manifest["source"]
        integrity = (ROOT / "scripts" / "dev" / "sidecar-integrity.sh").read_text()
        expected = f"kdense-scientific-agent-skills|{source['commit']}|{source['archiveSha256']}"
        self.assertIn(expected, integrity)


if __name__ == "__main__":
    unittest.main()
