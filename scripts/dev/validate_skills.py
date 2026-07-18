#!/usr/bin/env python3
"""Validate the bundled core skill manifest and loadable skill metadata."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = ROOT / "runtime" / "skills"
CORE_ROOT = SKILLS_ROOT / "core"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing opening frontmatter delimiter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise ValueError("missing closing frontmatter delimiter") from error
    metadata: dict[str, Any] = {}
    current_list: str | None = None
    for line in lines[1:end]:
        if not line.strip():
            continue
        if line.startswith("  - ") and current_list:
            metadata.setdefault(current_list, []).append(line[4:].strip().strip("\"'"))
            continue
        match = re.match(r"^([a-zA-Z][\w-]*):(?:\s*(.*))?$", line)
        if not match:
            raise ValueError(f"unsupported frontmatter line: {line}")
        key, value = match.groups()
        current_list = key if value in (None, "") else None
        if value in (None, ""):
            metadata[key] = []
        else:
            metadata[key] = value.strip().strip("\"'")
    return metadata, "\n".join(lines[end + 1 :])


def validate_manifest(manifest_path: Path = SKILLS_ROOT / "manifest.json") -> list[str]:
    issues: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"manifest: {error}"]
    entries = manifest.get("skills")
    if not isinstance(entries, list):
        return ["manifest: skills must be a list"]
    if len(entries) < 20:
        issues.append(f"manifest: expected at least 20 skills, found {len(entries)}")
    names = [entry.get("name") for entry in entries]
    paths = [entry.get("path") for entry in entries]
    for value, label in ((names, "name"), (paths, "path")):
        duplicates = sorted({item for item in value if value.count(item) > 1})
        if duplicates:
            issues.append(f"manifest: duplicate {label}: {', '.join(duplicates)}")

    deployable = {path.parent.name for path in CORE_ROOT.glob("*/SKILL.md")}
    listed = set(names)
    for name in sorted(deployable - listed):
        issues.append(f"manifest: deployable skill is unlisted: {name}")
    for name in sorted(listed - deployable):
        issues.append(f"manifest: listed skill is not loadable: {name}")

    skills_root = SKILLS_ROOT.resolve()
    for entry in entries:
        name = entry.get("name")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            issues.append(f"{name or '<unnamed>'}: invalid skill name")
            continue
        relative = PurePosixPath(str(entry.get("path", "")))
        if relative.is_absolute() or ".." in relative.parts:
            issues.append(f"{name}: path escapes skills root")
            continue
        directory = (SKILLS_ROOT / Path(*relative.parts)).resolve()
        if not directory.is_relative_to(skills_root) or not directory.is_dir():
            issues.append(f"{name}: missing or unsafe skill directory")
            continue
        skill_file = directory / "SKILL.md"
        if not skill_file.is_file() or directory.name != name:
            issues.append(f"{name}: path/name does not point to SKILL.md")
            continue
        try:
            metadata, body = parse_frontmatter(skill_file)
        except (OSError, ValueError) as error:
            issues.append(f"{name}: {error}")
            continue
        if metadata.get("name") != name:
            issues.append(f"{name}: frontmatter name mismatch")
        if not isinstance(metadata.get("description"), str) or not metadata["description"].strip():
            issues.append(f"{name}: missing description")
        for reference in metadata.get("references", []):
            reference_path = Path(str(reference))
            if reference_path.is_absolute() or ".." in reference_path.parts:
                issues.append(f"{name}: unsafe reference {reference}")
            elif not (directory / reference_path).is_file():
                issues.append(f"{name}: missing reference {reference}")
        if entry.get("reuseType") == "adapted":
            required = ("upstreamRepository", "upstreamPath", "upstreamCommit", "attribution")
            for key in required:
                if not entry.get(key):
                    issues.append(f"{name}: adapted skill missing {key}")
            if entry.get("license") != "Apache-2.0":
                issues.append(f"{name}: adapted skill must retain Apache-2.0 license")
        if any(child.is_symlink() for child in directory.rglob("*")):
            issues.append(f"{name}: symlink in skill directory")
        if "TODO: fix" in body:
            issues.append(f"{name}: unresolved TODO marker")
    return issues


def main() -> int:
    issues = validate_manifest()
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}", file=sys.stderr)
        return 1
    manifest = json.loads((SKILLS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    print(f"validated {len(manifest['skills'])} bundled core skills")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
