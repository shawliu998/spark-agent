from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, BinaryIO, cast

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.requests import ClientDisconnect
from starlette.types import Message, Scope

from open_science_core import secure_download as download_module
from open_science_core.app import artifact_file, source_file
from open_science_core.models import (
    ArtifactRecord,
    ProjectRecord,
    RunRecord,
    SourceRecord,
    TaskRecord,
)
from open_science_core.secure_download import (
    DownloadErrorDetails,
    SecureDownloadError,
    secure_download_response,
)

ERRORS = DownloadErrorDetails(
    missing="File is missing",
    unsafe="File path is unsafe",
    changed="File changed while preparing the download",
    hash_mismatch="File content hash does not match",
)


def _write_source(project_root: Path, content: bytes) -> Path:
    source = project_root / "nested" / "file.bin"
    source.parent.mkdir(parents=True)
    source.write_bytes(content)
    return source


def _download(project_root: Path, source: Path, content: bytes) -> StreamingResponse:
    return secure_download_response(
        project_root=project_root,
        source_path=source,
        expected_sha256=hashlib.sha256(content).hexdigest(),
        media_type="application/octet-stream",
        filename='unsafe/"name\r\n.bin',
        content_disposition_type="attachment",
        errors=ERRORS,
    )


async def _response_body(response: StreamingResponse) -> bytes:
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunk_object: object = chunk
        if isinstance(chunk_object, bytes):
            chunks.append(chunk_object)
        elif isinstance(chunk_object, str):
            chunks.append(chunk_object.encode())
        else:
            chunks.append(chunk_object.tobytes())
    if response.background is not None:
        await response.background()
    return b"".join(chunks)


def _captured_temporary_files(monkeypatch: pytest.MonkeyPatch) -> list[BinaryIO]:
    original = tempfile.TemporaryFile
    captured: list[BinaryIO] = []

    def tracked_temporary_file(*, mode: str = "w+b") -> BinaryIO:
        snapshot = cast(BinaryIO, original(mode=mode))
        captured.append(snapshot)
        return snapshot

    monkeypatch.setattr(download_module.tempfile, "TemporaryFile", tracked_temporary_file)
    return captured


def test_rejects_intermediate_directory_symlink(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    content = b"outside content"
    (outside / "file.bin").write_bytes(content)
    (project_root / "nested").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SecureDownloadError) as raised:
        _download(project_root, project_root / "nested" / "file.bin", content)

    assert raised.value.status_code == 409
    assert raised.value.detail == ERRORS.unsafe


def test_rejects_symlinked_root_and_final_symlink_hardlink_or_fifo(tmp_path: Path) -> None:
    real_root = tmp_path / "real-project"
    real_root.mkdir()
    content = b"approved content"
    real_source = _write_source(real_root, content)
    linked_root = tmp_path / "linked-project"
    linked_root.symlink_to(real_root, target_is_directory=True)

    unsafe_paths: list[tuple[Path, Path]] = [
        (linked_root, linked_root / "nested" / "file.bin"),
    ]
    final_symlink = real_root / "final-symlink.bin"
    final_symlink.symlink_to(real_source.relative_to(real_root))
    unsafe_paths.append((real_root, final_symlink))
    final_hardlink = real_root / "final-hardlink.bin"
    os.link(real_source, final_hardlink)
    unsafe_paths.append((real_root, final_hardlink))
    final_fifo = real_root / "final-fifo.bin"
    os.mkfifo(final_fifo)
    unsafe_paths.append((real_root, final_fifo))

    for project_root, source_path in unsafe_paths:
        with pytest.raises(SecureDownloadError) as raised:
            _download(project_root, source_path, content)
        assert raised.value.status_code == 409
        assert raised.value.detail == ERRORS.unsafe


@pytest.mark.asyncio
async def test_snapshot_bytes_ignore_path_swap_and_later_inode_changes(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    content = (b"approved bytes" * 1000) + b"\n"
    source = _write_source(project_root, content)
    response = _download(project_root, source, content)

    original = source.with_name("original.bin")
    source.rename(original)
    replacement = source.with_name("replacement.bin")
    replacement.write_bytes(b"attacker replacement")
    source.symlink_to(replacement.name)
    original.write_bytes(b"changed after the snapshot")

    assert await _response_body(response) == content
    assert response.headers["content-length"] == str(len(content))
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "\r" not in disposition and "\n" not in disposition
    assert "%22" in disposition


def test_rejects_same_inode_modification_during_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    content = b"a" * (download_module.CHUNK_SIZE * 2)
    source = _write_source(project_root, content)
    original_read = os.read
    changed = False

    def read_then_change(descriptor: int, size: int) -> bytes:
        nonlocal changed
        chunk = original_read(descriptor, size)
        if chunk and not changed:
            changed = True
            with source.open("ab") as stream:
                stream.write(b"changed")
        return chunk

    snapshots = _captured_temporary_files(monkeypatch)
    monkeypatch.setattr(download_module.os, "read", read_then_change)

    with pytest.raises(SecureDownloadError) as raised:
        _download(project_root, source, content)

    assert raised.value.status_code == 409
    assert raised.value.detail == ERRORS.changed
    assert snapshots and snapshots[0].closed


@pytest.mark.asyncio
async def test_snapshot_closes_after_normal_response_and_disconnect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    content = b"download body"
    source = _write_source(project_root, content)
    snapshots = _captured_temporary_files(monkeypatch)

    normal_response = _download(project_root, source, content)
    assert await _response_body(normal_response) == content
    assert snapshots[0].closed

    disconnected_response = _download(project_root, source, content)

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            raise OSError("client disconnected")

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/download",
        "raw_path": b"/download",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 80),
    }
    with pytest.raises(ClientDisconnect):
        await disconnected_response(scope, receive, send)
    assert snapshots[1].closed


class _RecordSession:
    def __init__(self, records: dict[tuple[type[Any], str], Any]) -> None:
        self.records = records

    def get(self, record_type: type[Any], record_id: str) -> Any:
        return self.records.get((record_type, record_id))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_kind", "expected_media_type", "expected_disposition"),
    [
        ("pdf", "application/pdf", "inline"),
        ("dataset", "text/csv; charset=utf-8", "attachment"),
    ],
)
async def test_source_endpoint_preserves_media_and_disposition_rules(
    tmp_path: Path,
    source_kind: str,
    expected_media_type: str,
    expected_disposition: str,
) -> None:
    content = b"source bytes"
    path = tmp_path / "source.bin"
    path.write_bytes(content)
    project = SimpleNamespace(id="project-1", project_path=str(tmp_path))
    source = SimpleNamespace(
        id="source-1",
        project_id=project.id,
        local_path=str(path),
        content_hash=hashlib.sha256(content).hexdigest(),
        source_kind=source_kind,
        title="Source title",
    )
    session = _RecordSession(
        {
            (SourceRecord, source.id): source,
            (ProjectRecord, project.id): project,
        }
    )

    response = source_file(source.id, cast(Session, session))

    assert response.headers["content-type"] == expected_media_type
    assert response.headers["content-disposition"].startswith(f"{expected_disposition};")
    assert await _response_body(response) == content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mime_type", "expected_disposition"),
    [
        ("image/png", "inline"),
        ("text/plain", "inline"),
        ("application/json", "inline"),
        ("application/pdf", "inline"),
        ("application/octet-stream", "attachment"),
    ],
)
async def test_artifact_endpoint_preserves_inline_rules(
    tmp_path: Path,
    mime_type: str,
    expected_disposition: str,
) -> None:
    content = b"artifact bytes"
    path = tmp_path / "runs" / "run-1" / "artifact.bin"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    project = SimpleNamespace(id="project-1", project_path=str(tmp_path))
    task = SimpleNamespace(id="task-1", project_id=project.id)
    run = SimpleNamespace(
        id="run-1",
        task_id=task.id,
        output_artifacts=["runs/run-1/artifact.bin"],
    )
    artifact = SimpleNamespace(
        id="artifact-1",
        run_id=run.id,
        path="runs/run-1/artifact.bin",
        content_hash=hashlib.sha256(content).hexdigest(),
        mime_type=mime_type,
    )
    session = _RecordSession(
        {
            (ArtifactRecord, artifact.id): artifact,
            (RunRecord, run.id): run,
            (TaskRecord, task.id): task,
            (ProjectRecord, project.id): project,
        }
    )

    response = artifact_file(artifact.id, cast(Session, session))

    expected_media_type = f"{mime_type}; charset=utf-8" if mime_type.startswith("text/") else mime_type
    assert response.headers["content-type"] == expected_media_type
    assert response.headers["content-disposition"].startswith(f"{expected_disposition};")
    assert await _response_body(response) == content


def test_endpoint_keeps_escape_missing_and_hash_mismatch_statuses(tmp_path: Path) -> None:
    project = SimpleNamespace(id="project-1", project_path=str(tmp_path))
    outside = tmp_path.parent / "outside-source.bin"
    outside.write_bytes(b"outside")

    def call(local_path: Path, expected_hash: str) -> int:
        source = SimpleNamespace(
            id="source-1",
            project_id=project.id,
            local_path=str(local_path),
            content_hash=expected_hash,
            source_kind="pdf",
            title="Source title",
        )
        session = _RecordSession(
            {
                (SourceRecord, source.id): source,
                (ProjectRecord, project.id): project,
            }
        )
        with pytest.raises(HTTPException) as raised:
            source_file(source.id, cast(Session, session))
        return raised.value.status_code

    assert call(outside, hashlib.sha256(b"outside").hexdigest()) == 403
    assert call(tmp_path / "missing.pdf", hashlib.sha256(b"missing").hexdigest()) == 404
    inside = tmp_path / "inside.pdf"
    inside.write_bytes(b"inside")
    assert call(inside, hashlib.sha256(b"different").hexdigest()) == 409


def test_artifact_endpoint_rejects_nested_reserved_runtime_name(tmp_path: Path) -> None:
    content = b"forged audit log"
    path = tmp_path / "runs" / "run-1" / "nested" / "execution.log"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    project = SimpleNamespace(id="project-1", project_path=str(tmp_path))
    task = SimpleNamespace(id="task-1", project_id=project.id)
    run = SimpleNamespace(
        id="run-1",
        task_id=task.id,
        output_artifacts=["runs/run-1/nested/execution.log"],
    )
    artifact = SimpleNamespace(
        id="artifact-1",
        run_id=run.id,
        path="runs/run-1/nested/execution.log",
        content_hash=hashlib.sha256(content).hexdigest(),
        mime_type="text/plain",
    )
    session = _RecordSession(
        {
            (ArtifactRecord, artifact.id): artifact,
            (RunRecord, run.id): run,
            (TaskRecord, task.id): task,
            (ProjectRecord, project.id): project,
        }
    )

    with pytest.raises(HTTPException) as raised:
        artifact_file(artifact.id, cast(Session, session))
    assert raised.value.status_code == 409
