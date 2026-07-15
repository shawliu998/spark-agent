from __future__ import annotations

import hashlib
import os
import shutil
import stat
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..analysis import RuntimeServiceError
from ..config import settings
from ..models import ArtifactRecord, ProjectRecord
from .errors import AnalysisServiceError

DirectoryIdentity = tuple[int, int]


@dataclass(slots=True)
class _DirectoryChain:
    root: Path
    component_names: tuple[str, ...]
    descriptors: list[int]
    identities: tuple[tuple[int, ...], ...]

    @property
    def leaf_descriptor(self) -> int:
        return self.descriptors[-1]

    def close(self) -> None:
        for descriptor in reversed(self.descriptors):
            os.close(descriptor)


@dataclass(slots=True)
class _DestinationFile:
    chain: _DirectoryChain
    descriptor: int
    name: str
    identity: tuple[int, ...]


def copy_dataset_from_safe_descriptor(
    *,
    workspace_root: Path,
    source_path: Path,
    destinations: Sequence[tuple[Path, int]],
    expected_content_hash: str,
    expected_destination_directories: dict[Path, DirectoryIdentity],
) -> None:
    source_descriptor = open_workspace_file_without_symlinks(workspace_root, source_path)
    opened_destinations: list[_DestinationFile] = []
    try:
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise RuntimeServiceError("dataset-integrity-invalid")
        for destination, mode in destinations:
            root = _destination_root(workspace_root, destination)
            parent = Path(os.path.abspath(destination.parent))
            chain = _open_directory_chain(root, parent)
            try:
                expected_parent = expected_destination_directories.get(parent)
                if (
                    expected_parent is None
                    or _descriptor_identity(chain.leaf_descriptor) != expected_parent
                ):
                    raise RuntimeServiceError("dataset-destination-identity-mismatch")
                descriptor = _create_file_at(chain.leaf_descriptor, destination.name, mode)
                os.fchmod(descriptor, mode)
                opened_destinations.append(
                    _DestinationFile(
                        chain=chain,
                        descriptor=descriptor,
                        name=destination.name,
                        identity=stat_identity(os.fstat(descriptor)),
                    )
                )
            except Exception:
                chain.close()
                raise

        digest = hashlib.sha256()
        while chunk := os.read(source_descriptor, 1024 * 1024):
            digest.update(chunk)
            for destination in opened_destinations:
                _write_all(destination.descriptor, chunk)
        for destination in opened_destinations:
            os.fsync(destination.descriptor)
            current = os.fstat(destination.descriptor)
            entry = os.stat(
                destination.name,
                dir_fd=destination.chain.leaf_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or stat_identity(entry) != stat_identity(current)
            ):
                raise RuntimeServiceError("dataset-destination-changed")
            _verify_directory_chain(destination.chain)
        after = os.fstat(source_descriptor)
        if stat_identity(before) != stat_identity(after):
            raise RuntimeServiceError("dataset-changed-during-copy")
        if digest.hexdigest() != expected_content_hash:
            raise RuntimeServiceError("dataset-content-hash-mismatch")
    except Exception:
        for destination in opened_destinations:
            unlink_if_same_inode(
                destination.chain.leaf_descriptor,
                destination.name,
                destination.identity,
            )
        raise
    finally:
        for destination in opened_destinations:
            os.close(destination.descriptor)
            destination.chain.close()
        os.close(source_descriptor)


def open_workspace_file_without_symlinks(
    workspace_root: Path,
    source_path: Path,
    *,
    expected_root_identity: DirectoryIdentity | None = None,
) -> int:
    lexical_root = Path(os.path.abspath(workspace_root))
    lexical_source = Path(os.path.abspath(source_path))
    try:
        relative = lexical_source.relative_to(lexical_root)
    except ValueError as error:
        raise RuntimeServiceError("dataset-path-outside-workspace") from error
    if not relative.parts or relative == Path(".") or ".." in relative.parts:
        raise RuntimeServiceError("dataset-path-invalid")

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
        file_flags |= os.O_NOFOLLOW
    try:
        current_descriptor = os.open(lexical_root, directory_flags)
    except OSError as error:
        raise RuntimeServiceError("workspace-open-failed") from error
    try:
        if (
            expected_root_identity is not None
            and _descriptor_identity(current_descriptor) != expected_root_identity
        ):
            raise RuntimeServiceError("workspace-root-identity-mismatch")
        for component in relative.parts[:-1]:
            _reject_symlink_component(current_descriptor, component)
            next_descriptor = os.open(component, directory_flags, dir_fd=current_descriptor)
            os.close(current_descriptor)
            current_descriptor = next_descriptor
        filename = relative.parts[-1]
        _reject_symlink_component(current_descriptor, filename)
        return os.open(filename, file_flags, dir_fd=current_descriptor)
    except OSError as error:
        raise RuntimeServiceError("dataset-open-failed") from error
    finally:
        os.close(current_descriptor)


def _open_directory_chain(
    root: Path,
    target: Path,
    *,
    create_intermediates: bool = False,
    create_leaf: bool = False,
    leaf_exist_ok: bool = True,
    mode: int = 0o700,
) -> _DirectoryChain:
    lexical_root = Path(os.path.abspath(root))
    lexical_target = Path(os.path.abspath(target))
    try:
        relative = lexical_target.relative_to(lexical_root)
    except ValueError as error:
        raise RuntimeServiceError("directory-path-outside-anchor") from error
    if ".." in relative.parts:
        raise RuntimeServiceError("directory-path-invalid")

    flags = _directory_open_flags()
    try:
        root_descriptor = os.open(lexical_root, flags)
    except OSError as error:
        raise RuntimeServiceError("directory-anchor-open-failed") from error
    descriptors = [root_descriptor]
    identities = [stat_identity(os.fstat(root_descriptor))]
    names: list[str] = []
    try:
        root_entry = os.stat(lexical_root, follow_symlinks=False)
        if _object_identity(root_entry) != _object_identity(os.fstat(root_descriptor)):
            raise RuntimeServiceError("directory-anchor-identity-mismatch")
        for index, component in enumerate(relative.parts):
            is_leaf = index == len(relative.parts) - 1
            try:
                entry = os.stat(
                    component,
                    dir_fd=descriptors[-1],
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                may_create = (is_leaf and create_leaf) or (not is_leaf and create_intermediates)
                if not may_create:
                    raise RuntimeServiceError("directory-component-missing") from None
                try:
                    os.mkdir(component, mode=mode, dir_fd=descriptors[-1])
                except FileExistsError:
                    if is_leaf and not leaf_exist_ok:
                        raise RuntimeServiceError("directory-leaf-already-exists") from None
                except OSError as error:
                    raise RuntimeServiceError("directory-create-failed") from error
                entry = os.stat(
                    component,
                    dir_fd=descriptors[-1],
                    follow_symlinks=False,
                )
            except OSError as error:
                raise RuntimeServiceError("directory-component-inspection-failed") from error
            else:
                if is_leaf and create_leaf and not leaf_exist_ok:
                    raise RuntimeServiceError("directory-leaf-already-exists")
            if not stat.S_ISDIR(entry.st_mode):
                raise RuntimeServiceError("directory-component-not-directory")
            try:
                descriptor = os.open(
                    component,
                    flags,
                    dir_fd=descriptors[-1],
                )
            except OSError as error:
                raise RuntimeServiceError("directory-component-open-failed") from error
            opened = os.fstat(descriptor)
            if _object_identity(entry) != _object_identity(opened):
                os.close(descriptor)
                raise RuntimeServiceError("directory-component-identity-mismatch")
            descriptors.append(descriptor)
            identities.append(stat_identity(opened))
            names.append(component)
        chain = _DirectoryChain(
            root=lexical_root,
            component_names=tuple(names),
            descriptors=descriptors,
            identities=tuple(identities),
        )
        _verify_directory_chain(chain)
        return chain
    except Exception:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        raise


def _verify_directory_chain(chain: _DirectoryChain) -> None:
    try:
        root_entry = os.stat(chain.root, follow_symlinks=False)
    except OSError as error:
        raise RuntimeServiceError("directory-anchor-unavailable") from error
    expected_root = _stored_object_identity(chain.identities[0])
    if (
        _object_identity(root_entry) != expected_root
        or _object_identity(os.fstat(chain.descriptors[0])) != expected_root
    ):
        raise RuntimeServiceError("directory-anchor-identity-mismatch")
    for index, component in enumerate(chain.component_names):
        try:
            entry = os.stat(
                component,
                dir_fd=chain.descriptors[index],
                follow_symlinks=False,
            )
        except OSError as error:
            raise RuntimeServiceError("directory-component-unavailable") from error
        expected = _stored_object_identity(chain.identities[index + 1])
        if (
            _object_identity(entry) != expected
            or _object_identity(os.fstat(chain.descriptors[index + 1])) != expected
        ):
            raise RuntimeServiceError("directory-component-identity-mismatch")


def create_anchored_directory(
    root: Path,
    target: Path,
    *,
    mode: int,
    create_intermediates: bool,
) -> DirectoryIdentity:
    chain = _open_directory_chain(
        root,
        target,
        create_intermediates=create_intermediates,
        create_leaf=True,
        leaf_exist_ok=False,
        mode=mode,
    )
    try:
        os.fchmod(chain.leaf_descriptor, mode)
        identities = list(chain.identities)
        identities[-1] = stat_identity(os.fstat(chain.leaf_descriptor))
        chain.identities = tuple(identities)
        _verify_directory_chain(chain)
        return _descriptor_identity(chain.leaf_descriptor)
    finally:
        chain.close()


def _destination_root(workspace_root: Path, destination: Path) -> Path:
    lexical_destination = Path(os.path.abspath(destination))
    candidates = (
        Path(os.path.abspath(workspace_root)),
        Path(os.path.abspath(settings.runtime_exchange_dir)),
    )
    for candidate in candidates:
        try:
            lexical_destination.relative_to(candidate)
        except ValueError:
            continue
        return candidate
    raise RuntimeServiceError("dataset-destination-outside-approved-roots")


def _create_file_at(directory_descriptor: int, name: str, mode: int) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise RuntimeServiceError("safe-file-creation-unavailable")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow | getattr(os, "O_CLOEXEC", 0)
    try:
        return os.open(name, flags, mode, dir_fd=directory_descriptor)
    except OSError as error:
        raise RuntimeServiceError("destination-file-create-failed") from error


def _directory_open_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        raise RuntimeServiceError("safe-directory-traversal-unavailable")
    return os.O_RDONLY | directory | nofollow | getattr(os, "O_CLOEXEC", 0)


def _reject_symlink_component(directory_descriptor: int, component: str) -> None:
    metadata = os.stat(component, dir_fd=directory_descriptor, follow_symlinks=False)
    if stat.S_ISLNK(metadata.st_mode):
        raise RuntimeServiceError("dataset-path-symlink")


def _write_all(descriptor: int, chunk: bytes) -> None:
    view = memoryview(chunk)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise OSError("short write")
        written += count


def stat_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _object_identity(value: os.stat_result) -> tuple[int, int, int]:
    return value.st_dev, value.st_ino, value.st_mode


def _stored_object_identity(value: tuple[int, ...]) -> tuple[int, int, int]:
    return value[0], value[1], value[2]


def _descriptor_identity(descriptor: int) -> DirectoryIdentity:
    value = os.fstat(descriptor)
    return value.st_dev, value.st_ino


def unlink_if_same_inode(
    directory_descriptor: int,
    name: str,
    expected: tuple[int, ...],
) -> None:
    try:
        current = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except OSError:
        return
    if (current.st_dev, current.st_ino) != (expected[0], expected[1]):
        return
    try:
        os.unlink(name, dir_fd=directory_descriptor)
    except OSError:
        return


def assert_runtime_input_unchanged(
    *,
    exchange_run_dir: Path,
    runtime_dataset_path: Path,
    dataset_content_hash: str,
    expected_exchange_run_identity: DirectoryIdentity,
) -> None:
    descriptor = open_workspace_file_without_symlinks(
        exchange_run_dir,
        runtime_dataset_path,
        expected_root_identity=expected_exchange_run_identity,
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise RuntimeServiceError("runtime-input-integrity-invalid")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        after = os.fstat(descriptor)
        if stat_identity(before) != stat_identity(after):
            raise RuntimeServiceError("runtime-input-changed")
        if digest.hexdigest() != dataset_content_hash:
            raise RuntimeServiceError("runtime-input-content-hash-mismatch")
    finally:
        os.close(descriptor)


def cleanup_stale_exchange_entries(*, reject_recent: bool) -> None:
    exchange_runs_dir = child_path(settings.runtime_exchange_dir, "runs")
    exchange_runs_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    stale_before = time.time() - (settings.execution_timeout_seconds + 30)
    for entry in exchange_runs_dir.iterdir():
        try:
            modified_at = entry.lstat().st_mtime
        except OSError as error:
            if reject_recent:
                raise AnalysisServiceError(
                    409,
                    "Runtime exchange is not inspectable",
                    code="runtime-exchange-unavailable",
                ) from error
            continue
        if modified_at > stale_before:
            if reject_recent:
                raise AnalysisServiceError(
                    409,
                    "Runtime exchange contains a recent unclaimed execution",
                    code="runtime-exchange-busy",
                )
            continue
        try:
            if entry.is_symlink() or entry.is_file():
                entry.unlink(missing_ok=True)
            elif entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink(missing_ok=True)
        except OSError as error:
            if reject_recent:
                raise AnalysisServiceError(
                    409,
                    "Could not remove a stale runtime exchange entry",
                    code="runtime-exchange-unavailable",
                ) from error


def cleanup_stale_analysis_exchange() -> None:
    cleanup_stale_exchange_entries(reject_recent=False)


def clear_run_outputs(
    workspace_root: Path,
    run_dir: Path,
    expected_identity: DirectoryIdentity | None,
) -> None:
    if expected_identity is None:
        return
    chain = _open_directory_chain(workspace_root, run_dir)
    try:
        if _descriptor_identity(chain.leaf_descriptor) != expected_identity:
            raise RuntimeServiceError("run-directory-identity-mismatch")
        _clear_directory_contents(
            chain.leaf_descriptor,
            preserved_names={"input.csv"},
        )
        _verify_directory_chain(chain)
    finally:
        chain.close()


def clear_recovered_run_outputs(workspace_root: Path, run_id: str) -> None:
    relative_run_id = Path(run_id)
    if (
        relative_run_id.is_absolute()
        or len(relative_run_id.parts) != 1
        or relative_run_id.name in {"", ".", ".."}
    ):
        raise RuntimeServiceError("recovery-run-id-invalid")
    runs_dir = Path(os.path.abspath(workspace_root / "runs"))
    chain = _open_directory_chain(workspace_root, runs_dir)
    try:
        try:
            entry = os.stat(
                run_id,
                dir_fd=chain.leaf_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        except OSError as error:
            raise RuntimeServiceError("recovery-run-inspection-failed") from error
        identity = stat_identity(entry)
        if not stat.S_ISDIR(entry.st_mode):
            unlink_if_same_inode(chain.leaf_descriptor, run_id, identity)
            _verify_directory_chain(chain)
            return
        try:
            descriptor = os.open(
                run_id,
                _directory_open_flags(),
                dir_fd=chain.leaf_descriptor,
            )
        except OSError as error:
            raise RuntimeServiceError("recovery-run-open-failed") from error
        try:
            opened = os.fstat(descriptor)
            if stat_identity(entry) != stat_identity(opened):
                raise RuntimeServiceError("recovery-run-identity-mismatch")
            _clear_directory_contents(descriptor, preserved_names=set())
            current = os.stat(
                run_id,
                dir_fd=chain.leaf_descriptor,
                follow_symlinks=False,
            )
            after_cleanup = os.fstat(descriptor)
            if stat_identity(current) != stat_identity(after_cleanup):
                raise RuntimeServiceError("recovery-run-identity-mismatch")
            _verify_directory_chain(chain)
        finally:
            os.close(descriptor)
    finally:
        chain.close()


def write_run_error_log(
    workspace_root: Path,
    run_dir: Path,
    expected_identity: DirectoryIdentity | None,
    content: bytes,
) -> tuple[str, str, int]:
    if expected_identity is None:
        raise RuntimeServiceError("run-directory-identity-unavailable")
    chain = _open_directory_chain(workspace_root, run_dir)
    descriptor: int | None = None
    identity: tuple[int, ...] | None = None
    name = "core-execution-error.log"
    try:
        if _descriptor_identity(chain.leaf_descriptor) != expected_identity:
            raise RuntimeServiceError("run-directory-identity-mismatch")
        descriptor = _create_file_at(chain.leaf_descriptor, name, 0o400)
        os.fchmod(descriptor, 0o400)
        identity = stat_identity(os.fstat(descriptor))
        _write_all(descriptor, content)
        os.fsync(descriptor)
        after = os.fstat(descriptor)
        entry = os.stat(name, dir_fd=chain.leaf_descriptor, follow_symlinks=False)
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or stat_identity(entry) != stat_identity(after)
        ):
            raise RuntimeServiceError("run-error-log-identity-mismatch")
        _verify_directory_chain(chain)
        relative_path = (Path(os.path.abspath(run_dir)) / name).relative_to(
            Path(os.path.abspath(workspace_root))
        )
        return relative_path.as_posix(), hashlib.sha256(content).hexdigest(), len(content)
    except Exception:
        if identity is not None:
            unlink_if_same_inode(chain.leaf_descriptor, name, identity)
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        chain.close()


def remove_exchange_run(
    exchange_run_dir: Path,
    expected_identity: DirectoryIdentity | None = None,
) -> None:
    exchange_root = Path(os.path.abspath(settings.runtime_exchange_dir))
    candidate = Path(os.path.abspath(exchange_run_dir))
    try:
        relative = candidate.relative_to(exchange_root)
    except ValueError:
        return
    if not relative.parts or relative == Path("."):
        return
    try:
        parent_chain = _open_directory_chain(exchange_root, candidate.parent)
    except RuntimeServiceError:
        return
    try:
        name = candidate.name
        try:
            entry = os.stat(
                name,
                dir_fd=parent_chain.leaf_descriptor,
                follow_symlinks=False,
            )
        except OSError:
            return
        if expected_identity is not None and (entry.st_dev, entry.st_ino) != expected_identity:
            return
        if stat.S_ISLNK(entry.st_mode):
            if expected_identity is None:
                unlink_if_same_inode(
                    parent_chain.leaf_descriptor,
                    name,
                    stat_identity(entry),
                )
            return
        if not stat.S_ISDIR(entry.st_mode):
            return
        try:
            descriptor = os.open(
                name,
                _directory_open_flags(),
                dir_fd=parent_chain.leaf_descriptor,
            )
        except OSError:
            return
        try:
            opened = os.fstat(descriptor)
            if stat_identity(entry) != stat_identity(opened):
                return
            try:
                _clear_directory_contents(descriptor, preserved_names=set())
                current = os.stat(
                    name,
                    dir_fd=parent_chain.leaf_descriptor,
                    follow_symlinks=False,
                )
            except (OSError, RuntimeServiceError):
                return
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                return
            try:
                os.rmdir(name, dir_fd=parent_chain.leaf_descriptor)
            except OSError:
                return
        finally:
            os.close(descriptor)
    finally:
        parent_chain.close()


def _clear_directory_contents(
    directory_descriptor: int,
    *,
    preserved_names: set[str],
    depth: int = 0,
    entry_budget: list[int] | None = None,
) -> None:
    if depth > 32:
        raise RuntimeServiceError("cleanup-directory-depth-exceeded")
    budget = entry_budget if entry_budget is not None else [1_000]
    names: list[str] = []
    try:
        with os.scandir(directory_descriptor) as entries:
            for entry in entries:
                budget[0] -= 1
                if budget[0] < 0:
                    raise RuntimeServiceError("cleanup-entry-count-exceeded")
                names.append(entry.name)
    except OSError as error:
        raise RuntimeServiceError("cleanup-directory-inspection-failed") from error
    for name in names:
        if name in preserved_names:
            continue
        try:
            entry = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise RuntimeServiceError("cleanup-entry-inspection-failed") from error
        identity = stat_identity(entry)
        if not stat.S_ISDIR(entry.st_mode):
            unlink_if_same_inode(directory_descriptor, name, identity)
            continue
        try:
            child_descriptor = os.open(
                name,
                _directory_open_flags(),
                dir_fd=directory_descriptor,
            )
        except OSError as error:
            raise RuntimeServiceError("cleanup-directory-open-failed") from error
        try:
            opened = os.fstat(child_descriptor)
            if stat_identity(entry) != stat_identity(opened):
                raise RuntimeServiceError("cleanup-directory-identity-mismatch")
            _clear_directory_contents(
                child_descriptor,
                preserved_names=set(),
                depth=depth + 1,
                entry_budget=budget,
            )
            current = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                continue
            try:
                os.rmdir(name, dir_fd=directory_descriptor)
            except OSError as error:
                raise RuntimeServiceError("cleanup-directory-remove-failed") from error
        finally:
            os.close(child_descriptor)


def read_named_artifact(
    project: ProjectRecord,
    artifacts: Sequence[ArtifactRecord],
    preferred_paths: Sequence[str],
) -> str:
    by_path = {artifact.path: artifact for artifact in artifacts}
    for artifact_path in preferred_paths:
        artifact = by_path.get(artifact_path)
        if artifact is None:
            continue
        path = Path(project.project_path) / artifact.path
        try:
            descriptor = open_workspace_file_without_symlinks(
                Path(project.project_path),
                path,
            )
        except RuntimeServiceError:
            continue
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                continue
            digest = hashlib.sha256()
            preview = bytearray()
            max_bytes = 2 * 1024 * 1024
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
                if len(preview) <= max_bytes:
                    preview.extend(chunk[: max_bytes + 1 - len(preview)])
            after = os.fstat(descriptor)
            if (
                stat_identity(before) != stat_identity(after)
                or digest.hexdigest() != artifact.content_hash
            ):
                continue
            if len(preview) > max_bytes:
                return preview[:max_bytes].decode("utf-8", errors="replace") + "\n[truncated]"
            return bytes(preview).decode("utf-8", errors="replace")
        finally:
            os.close(descriptor)
    return ""


def child_path(parent: Path, child: str) -> Path:
    parent = parent.resolve()
    candidate = (parent / child).resolve()
    _assert_beneath(parent, candidate)
    return candidate


def _assert_beneath(parent: Path, candidate: Path) -> None:
    try:
        candidate.relative_to(parent.resolve())
    except ValueError as error:
        raise AnalysisServiceError(
            403,
            "Path escapes the project directory",
            code="path-outside-project",
        ) from error
