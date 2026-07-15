from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, NoReturn
from urllib.parse import quote

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask
from starlette.types import Receive, Scope, Send

CHUNK_SIZE = 1024 * 1024
_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class SecureDownloadError(RuntimeError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class DownloadErrorDetails:
    missing: str
    unsafe: str
    changed: str
    hash_mismatch: str


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int
    mode: int
    link_count: int
    user_id: int
    group_id: int
    size: int
    modified_ns: int
    changed_ns: int


class _SecureStreamingResponse(StreamingResponse):
    def __init__(
        self,
        snapshot: BinaryIO,
        *,
        media_type: str,
        headers: dict[str, str],
    ) -> None:
        self._snapshot = snapshot
        super().__init__(
            _stream_snapshot(snapshot),
            media_type=media_type,
            headers=headers,
            background=BackgroundTask(snapshot.close),
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._snapshot.close()


def secure_download_response(
    *,
    project_root: Path,
    source_path: Path,
    expected_sha256: str,
    media_type: str,
    filename: str,
    content_disposition_type: Literal["attachment", "inline"],
    errors: DownloadErrorDetails,
) -> StreamingResponse:
    relative_parts = _relative_file_parts(project_root, source_path)
    descriptors: list[int] = []
    snapshot: BinaryIO | None = None
    try:
        root_descriptor = _open_project_root(project_root, errors)
        descriptors.append(root_descriptor)
        parent_descriptor = root_descriptor
        for component in relative_parts[:-1]:
            parent_descriptor = _open_directory_component(parent_descriptor, component, errors)
            descriptors.append(parent_descriptor)

        source_descriptor = _open_file_component(
            parent_descriptor,
            relative_parts[-1],
            errors,
        )
        descriptors.append(source_descriptor)
        before = _validated_file_identity(source_descriptor, errors)

        snapshot = tempfile.TemporaryFile(mode="w+b")
        content_hash, copied_bytes = _copy_and_hash(source_descriptor, snapshot)
        after = _file_identity(os.fstat(source_descriptor))
        if after != before or copied_bytes != before.size:
            raise SecureDownloadError(409, errors.changed)
        if content_hash != expected_sha256:
            raise SecureDownloadError(409, errors.hash_mismatch)

        snapshot.flush()
        snapshot.seek(0)
        response = _SecureStreamingResponse(
            snapshot,
            media_type=media_type,
            headers={
                "content-disposition": _content_disposition(
                    content_disposition_type,
                    filename,
                ),
                "content-length": str(copied_bytes),
            },
        )
        snapshot = None
        return response
    except SecureDownloadError:
        raise
    except OSError as error:
        raise SecureDownloadError(409, errors.changed) from error
    finally:
        if snapshot is not None:
            snapshot.close()
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _relative_file_parts(project_root: Path, source_path: Path) -> tuple[str, ...]:
    try:
        root = Path(os.path.abspath(os.fspath(project_root)))
        source = (
            Path(os.path.abspath(os.fspath(source_path)))
            if source_path.is_absolute()
            else Path(os.path.abspath(os.fspath(root / source_path)))
        )
        relative = source.relative_to(root)
    except (OSError, ValueError) as error:
        raise SecureDownloadError(403, "Path escapes the project directory") from error

    raw_parts = source_path.parts
    if any(part in {".", ".."} for part in raw_parts):
        raise SecureDownloadError(403, "Path escapes the project directory")
    if not relative.parts:
        raise SecureDownloadError(403, "Path escapes the project directory")
    if any(part in {"", ".", ".."} or "/" in part or "\x00" in part for part in relative.parts):
        raise SecureDownloadError(403, "Path escapes the project directory")
    return relative.parts


def _open_project_root(project_root: Path, errors: DownloadErrorDetails) -> int:
    root = Path(os.path.abspath(os.fspath(project_root)))
    try:
        descriptor = os.open(root, _DIRECTORY_OPEN_FLAGS)
    except OSError as error:
        _raise_open_error(error, errors)
    identity = os.fstat(descriptor)
    if not stat.S_ISDIR(identity.st_mode):
        os.close(descriptor)
        raise SecureDownloadError(409, errors.unsafe)
    return descriptor


def _open_directory_component(
    parent_descriptor: int,
    component: str,
    errors: DownloadErrorDetails,
) -> int:
    try:
        descriptor = os.open(
            component,
            _DIRECTORY_OPEN_FLAGS,
            dir_fd=parent_descriptor,
        )
    except OSError as error:
        _raise_open_error(error, errors)
    identity = os.fstat(descriptor)
    if not stat.S_ISDIR(identity.st_mode):
        os.close(descriptor)
        raise SecureDownloadError(409, errors.unsafe)
    return descriptor


def _open_file_component(
    parent_descriptor: int,
    component: str,
    errors: DownloadErrorDetails,
) -> int:
    try:
        return os.open(
            component,
            _FILE_OPEN_FLAGS,
            dir_fd=parent_descriptor,
        )
    except OSError as error:
        _raise_open_error(error, errors)


def _raise_open_error(error: OSError, details: DownloadErrorDetails) -> NoReturn:
    if isinstance(error, FileNotFoundError):
        raise SecureDownloadError(404, details.missing) from error
    raise SecureDownloadError(409, details.unsafe) from error


def _validated_file_identity(descriptor: int, errors: DownloadErrorDetails) -> _FileIdentity:
    raw_identity = os.fstat(descriptor)
    if not stat.S_ISREG(raw_identity.st_mode) or raw_identity.st_nlink != 1:
        raise SecureDownloadError(409, errors.unsafe)
    return _file_identity(raw_identity)


def _file_identity(raw_identity: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=raw_identity.st_dev,
        inode=raw_identity.st_ino,
        mode=raw_identity.st_mode,
        link_count=raw_identity.st_nlink,
        user_id=raw_identity.st_uid,
        group_id=raw_identity.st_gid,
        size=raw_identity.st_size,
        modified_ns=raw_identity.st_mtime_ns,
        changed_ns=raw_identity.st_ctime_ns,
    )


def _copy_and_hash(
    source_descriptor: int,
    snapshot: BinaryIO,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    copied_bytes = 0
    while True:
        chunk = os.read(source_descriptor, CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
        view = memoryview(chunk)
        while view:
            written = snapshot.write(view)
            if written <= 0:
                raise OSError("Unable to write the download snapshot")
            view = view[written:]
        copied_bytes += len(chunk)
    return digest.hexdigest(), copied_bytes


def _stream_snapshot(snapshot: BinaryIO) -> Generator[bytes, None, None]:
    try:
        snapshot.seek(0)
        while chunk := snapshot.read(CHUNK_SIZE):
            yield chunk
    finally:
        snapshot.close()


def _content_disposition(
    content_disposition_type: Literal["attachment", "inline"],
    filename: str,
) -> str:
    safe_name = _safe_download_filename(filename)
    fallback = "".join(
        character if character.isascii() and (character.isalnum() or character in " ._-") else "_"
        for character in safe_name
    ).strip(" .")
    if not fallback:
        fallback = "download"
    encoded = quote(safe_name, safe="")
    return (
        f'{content_disposition_type}; filename="{fallback}"; '
        f"filename*=UTF-8''{encoded}"
    )


def _safe_download_filename(filename: str) -> str:
    normalized = filename.replace("/", "_").replace("\\", "_")
    normalized = "".join(
        character if 32 <= ord(character) != 127 else "_" for character in normalized
    )
    normalized = normalized.strip(" .")[:180]
    return normalized if normalized not in {"", ".", ".."} else "download"
