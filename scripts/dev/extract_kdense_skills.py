#!/usr/bin/env python3
"""Extract only the reviewed K-Dense skill directories from a verified archive."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any


def fail(message: str) -> None:
    raise ValueError(message)


def safe_member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        fail(f"unsafe archive member path: {name}")
    return path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_member(archive: tarfile.TarFile, member: tarfile.TarInfo, target: Path) -> None:
    if member.isdir():
        target.mkdir(parents=True, exist_ok=True)
        return
    if not member.isfile():
        fail(f"unsupported selected archive member: {member.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    source = archive.extractfile(member)
    if source is None:
        fail(f"cannot read archive member: {member.name}")
    with source, target.open("wb") as destination:
        shutil.copyfileobj(source, destination)
    os.chmod(target, member.mode & 0o777)


def extract(archive_path: Path, manifest_path: Path, output: Path) -> None:
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = manifest["source"]
    actual_digest = sha256(archive_path)
    if actual_digest != source["archiveSha256"]:
        fail("archive SHA-256 does not match the curated source lock")
    root = f"scientific-agent-skills-{source['commit']}"
    selected = {entry["path"] for entry in manifest["skills"]}
    prefixes = {f"{root}/{path}/": Path(path).name for path in selected}
    license_member = f"{root}/{source['licensePath']}"
    output.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive_path, "r:gz") as archive:
        found: set[str] = set()
        license_found = False
        for member in archive.getmembers():
            path = safe_member_path(member.name)
            if member.name == license_member:
                if not member.isfile():
                    fail("upstream license is not a regular file")
                copy_member(archive, member, output / "LICENSE.md")
                license_found = True
                continue
            for prefix, name in prefixes.items():
                if member.name.startswith(prefix):
                    relative = PurePosixPath(member.name).relative_to(prefix)
                    if relative.parts:
                        copy_member(archive, member, output / name / Path(*relative.parts))
                    found.add(name)
                    break
        expected = {entry["name"] for entry in manifest["skills"]}
        if found != expected or not license_found:
            fail("archive does not contain the complete curated source selection")
    (output / ".spark-kdense-source.json").write_text(
        json.dumps(source, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(f"usage: {argv[0]} ARCHIVE MANIFEST OUTPUT", file=sys.stderr)
        return 64
    try:
        extract(Path(argv[1]), Path(argv[2]), Path(argv[3]))
    except (OSError, ValueError, tarfile.TarError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
