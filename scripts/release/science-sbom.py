#!/usr/bin/env python3
"""Generate and strictly verify deterministic Science image dependency SBOMs.

These SPDX documents inventory the Python application dependencies selected
from the hash-locked requirements for a Linux CPython 3.12 target. They are not
an operating-system or whole-container filesystem inventory.
"""

from __future__ import annotations

import argparse
import ast
from contextlib import contextmanager
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
GENERATOR_NAME = "spark-agent-science-sbom"
GENERATOR_VERSION = "1.0.0"
CREATED = "2000-01-01T00:00:00Z"
SCOPE = "application-dependencies"
MAX_METADATA_BYTES = 1024 * 1024
SBOM_NAMES = {"manifest.json", "science-core.spdx.json", "science-runtime.spdx.json"}
RUNTIME_NAMES = {
    "compose.yaml",
    "manifest.json",
    "science-core.oci.tar",
    "science-runtime.oci.tar",
}
HEX_64 = re.compile(r"[0-9a-f]{64}")
IMAGE_ID = re.compile(r"sha256:([0-9a-f]{64})")
PACKAGE_LINE = re.compile(r"^([A-Za-z0-9_.-]+)==([^ ;\\]+)(?:\s*;\s*(.*?))?\s*(\\)?$")
HASH_LINE = re.compile(r"^\s*--hash=sha256:([0-9a-f]{64})\s*(\\)?$")


class GateError(RuntimeError):
    pass


@dataclass(frozen=True)
class LockedPackage:
    name: str
    version: str
    hashes: tuple[str, ...]


@dataclass(frozen=True)
class Service:
    key: str
    package_name: str
    archive: str
    sbom: str
    lockfile: str
    project: str


SERVICES = (
    Service(
        key="science-core",
        package_name="spark-agent-core",
        archive="science-core.oci.tar",
        sbom="science-core.spdx.json",
        lockfile="services/science-core/requirements.lock",
        project="services/science-core/pyproject.toml",
    ),
    Service(
        key="science-runtime",
        package_name="spark-agent-runtime",
        archive="science-runtime.oci.tar",
        sbom="science-runtime.spdx.json",
        lockfile="services/science-runtime/requirements.lock",
        project="services/science-runtime/pyproject.toml",
    ),
)


def fail(message: str) -> None:
    raise GateError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@contextmanager
def regular_stream(path: Path, maximum: int):
    try:
        before = path.lstat()
    except FileNotFoundError:
        fail(f"missing required file: {path.name}")
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        fail(f"required file is not a regular non-symlink: {path.name}")
    if before.st_nlink != 1:
        fail(f"required file has multiple hard links: {path.name}")
    if before.st_size > maximum:
        fail(f"required file exceeds size limit: {path.name}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        fail(
            f"required file could not be opened safely: {path.name} ({error.strerror})"
        )
    opened = os.fstat(descriptor)
    stream = os.fdopen(descriptor, "rb", closefd=True)
    try:
        yield stream
    finally:
        stream.close()
    after = path.lstat()
    identities = {(value.st_dev, value.st_ino) for value in (before, opened, after)}
    if (
        len(identities) != 1
        or before.st_size != opened.st_size
        or opened.st_size != after.st_size
    ):
        fail(f"required file changed during validation: {path.name}")


def read_regular(path: Path, maximum: int = 64 * 1024 * 1024) -> bytes:
    with regular_stream(path, maximum) as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        fail(f"required file exceeds size limit: {path.name}")
    return data


def sha256_regular(path: Path, maximum: int = 3 * 1024 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with regular_stream(path, maximum) as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def exact_directory(path: Path, expected: set[str]) -> tuple[int, int]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        fail(f"missing required directory: {path.name}")
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        fail(f"required directory is not a real directory: {path.name}")
    names = {entry.name for entry in os.scandir(path)}
    if names != expected:
        missing = sorted(expected - names)
        extra = sorted(names - expected)
        fail(
            f"directory contract differs for {path.name}: missing={missing} extra={extra}"
        )
    identities: set[tuple[int, int]] = set()
    for name in sorted(expected):
        item = path / name
        item_stat = item.lstat()
        if not stat.S_ISREG(item_stat.st_mode) or stat.S_ISLNK(item_stat.st_mode):
            fail(f"directory entry is not a regular non-symlink: {name}")
        if item_stat.st_nlink != 1:
            fail(f"directory entry has multiple hard links: {name}")
        identity = (item_stat.st_dev, item_stat.st_ino)
        if identity in identities:
            fail(f"directory entries share one inode: {name}")
        identities.add(identity)
    after = path.lstat()
    if (
        (after.st_dev, after.st_ino) != (metadata.st_dev, metadata.st_ino)
        or not stat.S_ISDIR(after.st_mode)
        or stat.S_ISLNK(after.st_mode)
    ):
        fail(f"required directory changed during validation: {path.name}")
    return metadata.st_dev, metadata.st_ino


def require_directory_identity(path: Path, expected: tuple[int, int]) -> None:
    current = path.lstat()
    if (
        (current.st_dev, current.st_ino) != expected
        or not stat.S_ISDIR(current.st_mode)
        or stat.S_ISLNK(current.st_mode)
    ):
        fail(f"required directory identity changed: {path.name}")


def json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"invalid JSON for {label}: {error}")
    if not isinstance(value, dict):
        fail(f"JSON root must be an object for {label}")
    return value


def exact_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    if set(value) != keys:
        fail(f"unexpected fields for {label}")


def marker_value(node: ast.AST, environment: dict[str, str]) -> str | bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in environment:
        return environment[node.id]
    if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
        values = [bool(marker_value(item, environment)) for item in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and len(node.comparators) == 1
    ):
        left = marker_value(node.left, environment)
        right = marker_value(node.comparators[0], environment)
        if isinstance(node.ops[0], ast.Eq):
            return left == right
        if isinstance(node.ops[0], ast.NotEq):
            return left != right
    fail("requirements lock contains an unsupported environment marker")


def marker_applies(marker: str | None, architecture: str) -> bool:
    if not marker:
        return True
    machine = "aarch64" if architecture == "arm64" else "x86_64"
    environment = {
        "implementation_name": "cpython",
        "platform_machine": machine,
        "platform_python_implementation": "CPython",
        "sys_platform": "linux",
    }
    try:
        expression = ast.parse(marker, mode="eval")
    except SyntaxError:
        fail("requirements lock contains an invalid environment marker")
    return bool(marker_value(expression.body, environment))


def normalize_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(path: Path, architecture: str) -> list[LockedPackage]:
    data = read_regular(path).decode("utf-8")
    packages: list[LockedPackage] = []
    current: tuple[str, str, bool] | None = None
    hashes: list[str] = []

    def finish() -> None:
        nonlocal current, hashes
        if current is None:
            return
        name, version, selected = current
        if not hashes or len(hashes) != len(set(hashes)):
            fail(f"lock entry has missing or duplicate hashes: {name}")
        if selected:
            packages.append(
                LockedPackage(name=name, version=version, hashes=tuple(hashes))
            )
        current = None
        hashes = []

    for line_number, line in enumerate(data.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            if current is not None:
                fail(f"lock hash continuation was interrupted at line {line_number}")
            continue
        if stripped.startswith("#"):
            if current is not None:
                fail(f"lock hash continuation was interrupted at line {line_number}")
            continue
        package_match = PACKAGE_LINE.fullmatch(line)
        if package_match:
            if current is not None:
                fail(
                    f"lock requirement interrupted a hash continuation at line {line_number}"
                )
            name, version, marker, continuation = package_match.groups()
            if continuation != "\\":
                fail(f"lock requirement has no hash continuation at line {line_number}")
            current = (
                normalize_package_name(name),
                version,
                marker_applies(marker, architecture),
            )
            continue
        hash_match = HASH_LINE.fullmatch(line)
        if hash_match:
            if current is None:
                fail(f"orphan lock hash at line {line_number}")
            digest, continuation = hash_match.groups()
            hashes.append(digest)
            if continuation is None:
                finish()
            continue
        fail(f"unknown effective lock line {line_number}: {stripped[:80]}")
    if current is not None:
        fail("lock ended during a hash continuation")
    names = [normalize_package_name(package.name) for package in packages]
    if not packages or len(names) != len(set(names)):
        fail(f"lock inventory is empty or has duplicate packages: {path}")
    return packages


def project_metadata(path: Path) -> tuple[str, str]:
    text = read_regular(path).decode("utf-8")
    project_section = text.partition("[project]")[2].partition("\n[")[0]
    name_match = re.search(r'^name\s*=\s*"([^"]+)"\s*$', project_section, re.MULTILINE)
    version_match = re.search(
        r'^version\s*=\s*"([^"]+)"\s*$', project_section, re.MULTILINE
    )
    if not name_match or not version_match:
        fail(f"could not read project name/version: {path}")
    return name_match.group(1), version_match.group(1)


def tar_regular_member(
    archive: tarfile.TarFile, name: str, *, required: bool = True
) -> bytes | None:
    matches = [member for member in archive.getmembers() if member.name == name]
    if not matches:
        if required:
            fail(f"Docker archive metadata is missing: {name}")
        return None
    if len(matches) != 1:
        fail(f"Docker archive metadata is duplicated: {name}")
    member = matches[0]
    if (
        not member.isreg()
        or member.issym()
        or member.islnk()
        or member.size < 0
        or member.size > MAX_METADATA_BYTES
    ):
        fail(f"Docker archive metadata is unsafe or oversized: {name}")
    stream = archive.extractfile(member)
    if stream is None:
        fail(f"Docker archive metadata is unreadable: {name}")
    data = stream.read(MAX_METADATA_BYTES + 1)
    if len(data) != member.size or len(data) > MAX_METADATA_BYTES:
        fail(f"Docker archive metadata size differs: {name}")
    return data


def docker_archive_subject(path: Path, expected_tag: str) -> tuple[set[str], str]:
    archive_sha = sha256_regular(path)
    try:
        with regular_stream(path, 3 * 1024 * 1024 * 1024) as archive_file:
            with tarfile.open(fileobj=archive_file, mode="r:") as archive:
                manifest_bytes = tar_regular_member(archive, "manifest.json")
                assert manifest_bytes is not None
                manifest_value = json.loads(manifest_bytes)
                if not isinstance(manifest_value, list) or len(manifest_value) != 1:
                    fail("Docker archive must contain exactly one image")
                entry = manifest_value[0]
                if not isinstance(entry, dict) or entry.get("RepoTags") != [
                    expected_tag
                ]:
                    fail("Docker archive tag differs from runtime manifest")
                config_path = entry.get("Config")
                if not isinstance(config_path, str):
                    fail("Docker archive image config is missing")
                config_match = re.fullmatch(
                    r"(?:blobs/sha256/)?([0-9a-f]{64})(?:\.json)?", config_path
                )
                if config_match is None:
                    fail("Docker archive config path is non-canonical")
                config_bytes = tar_regular_member(archive, config_path)
                assert config_bytes is not None
                if sha256_bytes(config_bytes) != config_match.group(1):
                    fail("Docker archive config digest differs")
                config = json_object(config_bytes, "Docker image config")
                architecture = config.get("architecture")
                if architecture not in {"arm64", "amd64"}:
                    fail("Docker image config has unsupported architecture")
                accepted_ids = {f"sha256:{config_match.group(1)}"}
                index_bytes = tar_regular_member(archive, "index.json", required=False)
                if index_bytes is not None:
                    index = json_object(index_bytes, "Docker archive index")
                    descriptors = index.get("manifests")
                    if (
                        index.get("schemaVersion") != 2
                        or not isinstance(descriptors, list)
                        or len(descriptors) != 1
                    ):
                        fail("Docker archive index must have exactly one manifest")
                    descriptor = descriptors[0]
                    digest = (
                        descriptor.get("digest")
                        if isinstance(descriptor, dict)
                        else None
                    )
                    if (
                        not isinstance(digest, str)
                        or IMAGE_ID.fullmatch(digest) is None
                    ):
                        fail("Docker archive index digest is invalid")
                    descriptor_bytes = tar_regular_member(
                        archive, f"blobs/sha256/{digest.removeprefix('sha256:')}"
                    )
                    assert descriptor_bytes is not None
                    if sha256_bytes(descriptor_bytes) != digest.removeprefix("sha256:"):
                        fail("Docker archive indexed manifest digest differs")
                    indexed_manifest = json_object(
                        descriptor_bytes, "Docker indexed manifest"
                    )
                    indexed_config = indexed_manifest.get("config")
                    if (
                        not isinstance(indexed_config, dict)
                        or indexed_config.get("digest") not in accepted_ids
                    ):
                        fail("Docker indexed manifest points to a different config")
                    accepted_ids.add(digest)
    except (OSError, KeyError, json.JSONDecodeError, tarfile.TarError):
        fail("Docker archive is invalid or unsafe")
    return accepted_ids, architecture + ":" + archive_sha


def runtime_subjects(runtime_dir: Path, expected_arch: str) -> list[dict[str, Any]]:
    directory_identity = exact_directory(runtime_dir, RUNTIME_NAMES)
    runtime_manifest = json_object(
        read_regular(runtime_dir / "manifest.json"), "runtime manifest"
    )
    exact_keys(
        runtime_manifest,
        {"schemaVersion", "composeSha256", "images"},
        "runtime manifest",
    )
    if runtime_manifest.get("schemaVersion") != 2:
        fail("runtime manifest schema version differs")
    compose_sha = runtime_manifest.get("composeSha256")
    if compose_sha != sha256_bytes(read_regular(runtime_dir / "compose.yaml")):
        fail("runtime Compose hash differs")
    images = runtime_manifest.get("images")
    if not isinstance(images, list) or len(images) != 2:
        fail("runtime manifest must contain exactly two images")
    by_archive = {
        item.get("archive"): item for item in images if isinstance(item, dict)
    }
    if set(by_archive) != {service.archive for service in SERVICES}:
        fail("runtime manifest image subjects differ")
    subjects: list[dict[str, Any]] = []
    for service in SERVICES:
        item = by_archive[service.archive]
        exact_keys(item, {"archive", "image", "imageIds", "sha256"}, service.key)
        archive_path = runtime_dir / service.archive
        archive_sha = sha256_regular(archive_path)
        if item.get("sha256") != archive_sha:
            fail(f"runtime archive hash differs: {service.key}")
        image = item.get("image")
        image_ids = item.get("imageIds")
        expected_image = (
            f"io.github.shawliu998.sparkagent/{service.key}:0.2.1"
        )
        if (
            image != expected_image
            or not isinstance(image_ids, list)
            or not 1 <= len(image_ids) <= 2
            or any(
                not isinstance(image_id, str)
                or IMAGE_ID.fullmatch(image_id) is None
                for image_id in image_ids
            )
            or len(set(image_ids)) != len(image_ids)
        ):
            fail(f"runtime image subject is invalid: {service.key}")
        accepted_ids, archive_identity = docker_archive_subject(archive_path, image)
        architecture, parsed_archive_sha = archive_identity.split(":", 1)
        if parsed_archive_sha != archive_sha or set(image_ids) != accepted_ids:
            fail(f"runtime image ID is not bound to its archive: {service.key}")
        if architecture != expected_arch:
            fail(f"runtime archive architecture differs: {service.key}")
        subjects.append(
            {
                "service": service,
                "image": image,
                "imageId": image_ids[0],
                "archiveSha256": archive_sha,
            }
        )
    if len({subject["imageId"] for subject in subjects}) != len(subjects):
        fail("runtime images must have distinct canonical image IDs")
    require_directory_identity(runtime_dir, directory_identity)
    return subjects


def spdx_id(name: str, index: int) -> str:
    safe = re.sub(r"[^A-Za-z0-9.-]+", "-", name).strip("-") or "package"
    return f"SPDXRef-Package-{index:03d}-{safe}"


def build_spdx(
    root: Path, subject: dict[str, Any], architecture: str
) -> tuple[dict[str, Any], int, str]:
    service: Service = subject["service"]
    lock_path = root / service.lockfile
    project_path = root / service.project
    locked = parse_lock(lock_path, architecture)
    project_name, project_version = project_metadata(project_path)
    if project_name != service.package_name:
        fail(f"service package metadata differs: {service.key}")
    root_id = spdx_id(project_name, 0)
    packages: list[dict[str, Any]] = [
        {
            "SPDXID": root_id,
            "name": project_name,
            "versionInfo": project_version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
            "primaryPackagePurpose": "APPLICATION",
            "comment": (
                f"Spark service subject; imageId={subject['imageId']}; "
                f"architecture={architecture}; archiveSha256={subject['archiveSha256']}"
            ),
        }
    ]
    relationships: list[dict[str, str]] = []
    for index, package in enumerate(locked, start=1):
        package_id = spdx_id(package.name, index)
        packages.append(
            {
                "SPDXID": package_id,
                "name": package.name,
                "versionInfo": package.version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "primaryPackagePurpose": "LIBRARY",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": f"pkg:pypi/{package.name}@{package.version}",
                    }
                ],
                "comment": "Locked distribution SHA-256 set: "
                + ",".join(package.hashes),
            }
        )
        relationships.append(
            {
                "spdxElementId": root_id,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": package_id,
            }
        )
    namespace = (
        "https://spark-agent.local/spdx/1/"
        f"{architecture}/{service.key}/{subject['imageId'].removeprefix('sha256:')}/"
        f"{subject['archiveSha256']}"
    )
    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"Spark Agent {service.key} application dependency SBOM",
        "documentNamespace": namespace,
        "creationInfo": {
            "created": CREATED,
            "creators": [f"Tool: {GENERATOR_NAME}-{GENERATOR_VERSION}"],
            "comment": (
                "Deterministic application-dependency inventory from the hash lock; "
                "the fixed timestamp supports reproducible output. This is not an OS or "
                "whole-container filesystem inventory."
            ),
        },
        "documentDescribes": [root_id],
        "packages": packages,
        "relationships": relationships,
        "annotations": [
            {
                "annotationType": "OTHER",
                "annotator": f"Tool: {GENERATOR_NAME}-{GENERATOR_VERSION}",
                "annotationDate": CREATED,
                "comment": (
                    f"scope={SCOPE};imageId={subject['imageId']};architecture={architecture};"
                    f"archiveSha256={subject['archiveSha256']}"
                ),
            }
        ],
    }
    return document, len(packages), sha256_bytes(read_regular(lock_path))


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode()


def expected_sboms(
    root: Path, runtime_dir: Path, architecture: str
) -> tuple[dict[str, bytes], dict[str, Any]]:
    subjects = runtime_subjects(runtime_dir, architecture)
    documents: dict[str, bytes] = {}
    manifest_subjects: list[dict[str, Any]] = []
    for subject in subjects:
        service: Service = subject["service"]
        document, package_count, lock_sha = build_spdx(root, subject, architecture)
        document_bytes = canonical_json(document)
        documents[service.sbom] = document_bytes
        _, project_version = project_metadata(root / service.project)
        manifest_subjects.append(
            {
                "service": service.key,
                "image": subject["image"],
                "imageId": subject["imageId"],
                "architecture": architecture,
                "archive": service.archive,
                "archiveSha256": subject["archiveSha256"],
                "lockfile": service.lockfile,
                "lockfileSha256": lock_sha,
                "packageName": service.package_name,
                "packageVersion": project_version,
                "packageCount": package_count,
                "sbom": service.sbom,
                "sbomSha256": sha256_bytes(document_bytes),
            }
        )
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "inventoryScope": SCOPE,
        "architecture": architecture,
        "generator": {"name": GENERATOR_NAME, "version": GENERATOR_VERSION},
        "subjects": manifest_subjects,
    }
    return documents, manifest


def generate(root: Path, runtime_dir: Path, output: Path, architecture: str) -> None:
    if output.exists() or output.is_symlink():
        fail("SBOM output already exists")
    documents, manifest = expected_sboms(root, runtime_dir, architecture)
    output.mkdir(mode=0o700)
    try:
        for name, data in documents.items():
            (output / name).write_bytes(data)
        (output / "manifest.json").write_bytes(canonical_json(manifest))
        for path in output.iterdir():
            path.chmod(0o644)
        verify(root, runtime_dir, output, architecture)
    except BaseException:
        shutil.rmtree(output)
        raise


def verify(root: Path, runtime_dir: Path, sbom_dir: Path, architecture: str) -> None:
    if architecture not in {"arm64", "amd64"}:
        fail("architecture must be arm64 or amd64")
    directory_identity = exact_directory(sbom_dir, SBOM_NAMES)
    expected_documents, expected_manifest = expected_sboms(
        root, runtime_dir, architecture
    )
    manifest_bytes = read_regular(sbom_dir / "manifest.json")
    actual_manifest = json_object(manifest_bytes, "SBOM manifest")
    if actual_manifest != expected_manifest or manifest_bytes != canonical_json(
        expected_manifest
    ):
        fail("SBOM manifest differs from its runtime/lock subjects")
    for name, expected in expected_documents.items():
        actual = read_regular(sbom_dir / name)
        if actual != expected:
            fail(
                f"SPDX document differs from deterministic dependency inventory: {name}"
            )
        document = json_object(actual, name)
        if document.get("spdxVersion") != "SPDX-2.3" or not document.get("packages"):
            fail(f"SPDX document is incomplete: {name}")
    require_directory_identity(sbom_dir, directory_identity)


def write_fixture_archive(path: Path, tag: str, architecture: str) -> tuple[str, str]:
    import io

    config = canonical_json(
        {
            "architecture": architecture,
            "os": "linux",
            "config": {
                "Labels": {"io.github.shawliu998.sparkagent.fixture-tag": tag}
            },
        }
    )
    config_sha = sha256_bytes(config)
    manifest = canonical_json(
        [{"Config": f"{config_sha}.json", "RepoTags": [tag], "Layers": []}]
    )
    with tarfile.open(path, "w") as archive:
        for name, data in ((f"{config_sha}.json", config), ("manifest.json", manifest)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return f"sha256:{config_sha}", sha256_bytes(path.read_bytes())


def make_runtime_fixture(root: Path, architecture: str) -> Path:
    runtime = root / "runtime"
    runtime.mkdir()
    compose = b"services: {}\n"
    (runtime / "compose.yaml").write_bytes(compose)
    images = []
    for service in SERVICES:
        tag = f"io.github.shawliu998.sparkagent/{service.key}:0.2.1"
        image_id, archive_sha = write_fixture_archive(
            runtime / service.archive, tag, architecture
        )
        images.append(
            {
                "archive": service.archive,
                "image": tag,
                "imageIds": [image_id],
                "sha256": archive_sha,
            }
        )
    (runtime / "manifest.json").write_bytes(
        canonical_json(
            {
                "schemaVersion": 2,
                "composeSha256": sha256_bytes(compose),
                "images": images,
            }
        )
    )
    return runtime


def copy_runtime_fixture(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination)
    return destination


def update_fixture_archive_hash(runtime: Path, archive_name: str) -> None:
    manifest_path = runtime / "manifest.json"
    manifest = json_object(manifest_path.read_bytes(), "fixture runtime manifest")
    for image in manifest["images"]:
        if image["archive"] == archive_name:
            image["sha256"] = sha256_bytes((runtime / archive_name).read_bytes())
            break
    manifest_path.write_bytes(canonical_json(manifest))


def write_invalid_metadata_archive(path: Path, kind: str) -> None:
    import io

    with tarfile.open(path, "w") as archive:
        if kind == "oversized":
            data = b"x" * (MAX_METADATA_BYTES + 1)
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        elif kind == "link":
            info = tarfile.TarInfo("manifest.json")
            info.type = tarfile.SYMTYPE
            info.linkname = "elsewhere"
            archive.addfile(info)
        elif kind == "duplicate":
            for data in (b"[]", b"[]"):
                info = tarfile.TarInfo("manifest.json")
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        else:
            raise AssertionError(f"unknown invalid archive fixture: {kind}")


def copy_lock_root(source: Path, destination: Path) -> Path:
    for service in SERVICES:
        for relative in (service.lockfile, service.project):
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)
    return destination


def expect_failure(label: str, function: Any) -> None:
    try:
        function()
    except (GateError, OSError, ValueError, json.JSONDecodeError, tarfile.TarError):
        print(f"Fixture rejected as required: {label}")
        return
    fail(f"negative fixture unexpectedly passed: {label}")


def run_fixtures(root: Path) -> None:
    with tempfile.TemporaryDirectory(
        prefix="spark-science-sbom-fixtures."
    ) as temporary:
        fixture_root = Path(temporary)
        runtime = make_runtime_fixture(fixture_root, "arm64")
        valid = fixture_root / "valid-sbom"
        generate(root, runtime, valid, "arm64")
        verify(root, runtime, valid, "arm64")

        def candidate(name: str) -> Path:
            path = fixture_root / name
            shutil.copytree(valid, path)
            return path

        missing = candidate("missing")
        (missing / "science-runtime.spdx.json").unlink()
        expect_failure("missing SBOM", lambda: verify(root, runtime, missing, "arm64"))

        extra = candidate("extra")
        (extra / "unexpected.json").write_text("{}\n", encoding="utf-8")
        expect_failure("extra SBOM file", lambda: verify(root, runtime, extra, "arm64"))

        symlink = candidate("symlink")
        (symlink / "science-core.spdx.json").unlink()
        (symlink / "science-core.spdx.json").symlink_to(
            valid / "science-core.spdx.json"
        )
        expect_failure("SBOM symlink", lambda: verify(root, runtime, symlink, "arm64"))

        hardlink = candidate("hardlink")
        os.link(hardlink / "manifest.json", fixture_root / "hardlink-alias")
        expect_failure(
            "SBOM hardlink", lambda: verify(root, runtime, hardlink, "arm64")
        )

        tamper = candidate("tamper")
        tampered_document = json_object(
            (tamper / "science-core.spdx.json").read_bytes(), "tamper fixture"
        )
        tampered_document["name"] = "tampered"
        tampered_bytes = canonical_json(tampered_document)
        (tamper / "science-core.spdx.json").write_bytes(tampered_bytes)
        tamper_manifest = json_object(
            (tamper / "manifest.json").read_bytes(), "tamper manifest"
        )
        tamper_manifest["subjects"][0]["sbomSha256"] = sha256_bytes(tampered_bytes)
        (tamper / "manifest.json").write_bytes(canonical_json(tamper_manifest))
        expect_failure(
            "tampered SPDX inventory", lambda: verify(root, runtime, tamper, "arm64")
        )

        mismatch = candidate("subject-mismatch")
        mismatch_manifest = json_object(
            (mismatch / "manifest.json").read_bytes(), "mismatch"
        )
        mismatch_manifest["subjects"][0]["imageId"] = "sha256:" + "f" * 64
        (mismatch / "manifest.json").write_bytes(canonical_json(mismatch_manifest))
        expect_failure(
            "subject mismatch", lambda: verify(root, runtime, mismatch, "arm64")
        )

        expect_failure(
            "architecture mismatch", lambda: verify(root, runtime, valid, "amd64")
        )

        hash_mismatch = candidate("hash-mismatch")
        hash_manifest = json_object(
            (hash_mismatch / "manifest.json").read_bytes(), "hash mismatch"
        )
        hash_manifest["subjects"][1]["archiveSha256"] = "0" * 64
        (hash_mismatch / "manifest.json").write_bytes(canonical_json(hash_manifest))
        expect_failure(
            "archive hash mismatch",
            lambda: verify(root, runtime, hash_mismatch, "arm64"),
        )

        unknown_root = copy_lock_root(root, fixture_root / "unknown-lock-root")
        unknown_lock = unknown_root / SERVICES[0].lockfile
        with unknown_lock.open("a", encoding="utf-8") as stream:
            stream.write("--unknown-effective-option\n")
        expect_failure(
            "unknown effective lock line",
            lambda: verify(unknown_root, runtime, valid, "arm64"),
        )

        for kind in ("oversized", "link", "duplicate"):
            invalid_runtime = copy_runtime_fixture(
                runtime, fixture_root / f"metadata-{kind}-runtime"
            )
            write_invalid_metadata_archive(invalid_runtime / SERVICES[0].archive, kind)
            update_fixture_archive_hash(invalid_runtime, SERVICES[0].archive)
            expect_failure(
                f"{kind} Docker metadata",
                lambda invalid_runtime=invalid_runtime: verify(
                    root, invalid_runtime, valid, "arm64"
                ),
            )

        image_id_runtime = copy_runtime_fixture(
            runtime, fixture_root / "image-id-runtime"
        )
        image_id_manifest_path = image_id_runtime / "manifest.json"
        image_id_manifest = json_object(
            image_id_manifest_path.read_bytes(), "image ID fixture"
        )
        image_id_manifest["images"][0]["imageIds"][0] = "sha256:" + "e" * 64
        image_id_manifest_path.write_bytes(canonical_json(image_id_manifest))
        expect_failure(
            "runtime image ID tamper",
            lambda: verify(root, image_id_runtime, valid, "arm64"),
        )

        archive_sha_runtime = copy_runtime_fixture(
            runtime, fixture_root / "archive-sha-runtime"
        )
        archive_sha_manifest_path = archive_sha_runtime / "manifest.json"
        archive_sha_manifest = json_object(
            archive_sha_manifest_path.read_bytes(), "archive SHA fixture"
        )
        archive_sha_manifest["images"][0]["sha256"] = "0" * 64
        archive_sha_manifest_path.write_bytes(canonical_json(archive_sha_manifest))
        expect_failure(
            "runtime archive SHA tamper",
            lambda: verify(root, archive_sha_runtime, valid, "arm64"),
        )

        changed_root = copy_lock_root(root, fixture_root / "changed-lock-root")
        changed_lock = changed_root / SERVICES[0].lockfile
        with changed_lock.open("a", encoding="utf-8") as stream:
            stream.write("# fixture changes the approved lock bytes\n")
        expect_failure(
            "lock bytes change",
            lambda: verify(changed_root, runtime, valid, "arm64"),
        )

        root_symlink = fixture_root / "sbom-root-symlink"
        root_symlink.symlink_to(valid, target_is_directory=True)
        expect_failure(
            "SBOM root symlink", lambda: verify(root, runtime, root_symlink, "arm64")
        )

    print("Science SBOM gate passed: 1 positive and 16 negative fixtures.")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)
    for command in ("generate", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument("--root", required=True, type=Path)
        child.add_argument("--runtime-dir", required=True, type=Path)
        child.add_argument(
            "--sbom-dir" if command == "verify" else "--output",
            required=True,
            type=Path,
        )
        child.add_argument("--arch", required=True, choices=("arm64", "amd64"))
    fixtures = subparsers.add_parser("fixtures")
    fixtures.add_argument("--root", required=True, type=Path)
    return result


def main() -> int:
    arguments = parser().parse_args()
    root = arguments.root.resolve(strict=True)
    if arguments.command == "generate":
        generate(
            root,
            arguments.runtime_dir.resolve(strict=True),
            arguments.output,
            arguments.arch,
        )
    elif arguments.command == "verify":
        verify(
            root,
            arguments.runtime_dir.resolve(strict=True),
            arguments.sbom_dir.resolve(strict=True),
            arguments.arch,
        )
    else:
        run_fixtures(root)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GateError as error:
        print(f"Science SBOM gate failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
