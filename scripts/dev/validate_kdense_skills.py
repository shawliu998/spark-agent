#!/usr/bin/env python3
"""Validate Spark's pinned, curated K-Dense upstream skill selection."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "runtime" / "skills" / "kdense-curated-manifest.json"
CORE_MANIFEST = ROOT / "runtime" / "skills" / "manifest.json"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_manifest(manifest_path: Path = DEFAULT_MANIFEST) -> tuple[list[str], dict[str, Any] | None]:
    try:
        manifest = load_json(manifest_path)
    except (OSError, json.JSONDecodeError) as error:
        return [f"manifest: {error}"], None
    issues: list[str] = []
    source = manifest.get("source")
    if not isinstance(source, dict):
        return ["manifest: source must be an object"], None
    if source.get("repository") != "https://github.com/K-Dense-AI/scientific-agent-skills":
        issues.append("manifest: unexpected upstream repository")
    if not isinstance(source.get("commit"), str) or not COMMIT_RE.fullmatch(source["commit"]):
        issues.append("manifest: source commit must be a full SHA-1")
    if not isinstance(source.get("archiveSha256"), str) or not SHA256_RE.fullmatch(source["archiveSha256"]):
        issues.append("manifest: source archiveSha256 must be a SHA-256")
    if source.get("license") != "MIT" or source.get("licensePath") != "LICENSE.md":
        issues.append("manifest: source MIT license provenance is incomplete")
    if not isinstance(manifest.get("selectionPolicy"), str) or not manifest["selectionPolicy"].strip():
        issues.append("manifest: selection policy is required")
    entries = manifest.get("skills")
    if not isinstance(entries, list) or len(entries) != 30:
        return issues + ["manifest: expected exactly 30 curated skills"], manifest
    try:
        core_names = {entry["name"] for entry in load_json(CORE_MANIFEST)["skills"]}
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        return issues + [f"manifest: cannot read core skill names: {error}"], manifest
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            issues.append("manifest: skill entry must be an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            issues.append(f"manifest: invalid skill name {name!r}")
            continue
        if name in names:
            issues.append(f"manifest: duplicate skill {name}")
        names.add(name)
        if name in core_names:
            issues.append(f"manifest: K-Dense skill collides with Spark core skill {name}")
        if entry.get("path") != f"skills/{name}":
            issues.append(f"{name}: upstream path must be skills/{name}")
        if not isinstance(entry.get("category"), str) or not entry["category"].strip():
            issues.append(f"{name}: category is required")
        if not isinstance(entry.get("upstreamSkillLicense"), str) or not entry["upstreamSkillLicense"].strip():
            issues.append(f"{name}: declared upstream skill license is required")
    return issues, manifest


def validate_pack(pack: Path, manifest: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not pack.is_dir():
        return [f"pack: not a directory: {pack}"]
    lock_path = pack / ".spark-kdense-source.json"
    try:
        lock = load_json(lock_path)
    except (OSError, json.JSONDecodeError) as error:
        return [f"pack: missing or invalid source lock: {error}"]
    if lock != manifest["source"]:
        issues.append("pack: source lock does not match curated manifest")
    if not (pack / "LICENSE.md").is_file():
        issues.append("pack: missing upstream LICENSE.md")
    expected = {entry["name"] for entry in manifest["skills"]}
    actual = {path.name for path in pack.iterdir() if path.is_dir() and (path / "SKILL.md").is_file()}
    if actual != expected:
        issues.append(f"pack: selected skills differ (expected {len(expected)}, found {len(actual)})")
    for name in expected:
        skill = pack / name / "SKILL.md"
        if not skill.is_file():
            issues.append(f"pack: {name} is missing SKILL.md")
        else:
            frontmatter = skill.read_text(encoding="utf-8", errors="replace").split("---", 2)
            if len(frontmatter) < 3 or frontmatter[0].strip():
                issues.append(f"pack: {name} has invalid SKILL.md frontmatter")
            elif not re.search(rf"^name:\s*{re.escape(name)}\s*$", frontmatter[1], re.MULTILINE):
                issues.append(f"pack: {name} frontmatter name does not match directory")
        if any(path.is_symlink() for path in (pack / name).rglob("*")):
            issues.append(f"pack: {name} contains a symlink")
    return issues


def main(argv: list[str]) -> int:
    manifest_path = DEFAULT_MANIFEST
    pack: Path | None = None
    args = iter(argv[1:])
    for arg in args:
        if arg == "--manifest":
            manifest_path = Path(next(args, ""))
        elif arg == "--pack":
            pack = Path(next(args, ""))
        else:
            print(f"usage: {argv[0]} [--manifest PATH] [--pack PATH]", file=sys.stderr)
            return 64
    issues, manifest = validate_manifest(manifest_path)
    if manifest is not None and pack is not None:
        issues.extend(validate_pack(pack, manifest))
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}", file=sys.stderr)
        return 1
    print(f"validated {len(manifest['skills'])} curated K-Dense skills")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
