from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

import open_science_core._analysis_service.filesystem as filesystem_module
from open_science_core._analysis_service.filesystem import (
    assert_runtime_input_unchanged,
    clear_recovered_run_outputs,
    clear_run_outputs,
    copy_dataset_from_safe_descriptor,
    create_anchored_directory,
    remove_exchange_run,
    write_run_error_log,
)
from open_science_core.analysis import RuntimeServiceError
from open_science_core.config import settings


def _directory_identity(path: Path) -> tuple[int, int]:
    metadata = path.stat()
    return metadata.st_dev, metadata.st_ino


def test_create_anchored_leaf_allows_expected_parent_metadata_change(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "runs").mkdir(parents=True)
    run_dir = workspace / "runs" / "run-1"

    identity = create_anchored_directory(
        workspace,
        run_dir,
        mode=0o700,
        create_intermediates=False,
    )
    assert identity == _directory_identity(run_dir)


def test_clear_run_outputs_rejects_run_directory_symlink_replacement(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    identity = _directory_identity(run_dir)
    original = workspace / "runs" / "run-original"
    run_dir.rename(original)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    run_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeServiceError):
        clear_run_outputs(workspace, run_dir, identity)
    assert victim.read_text(encoding="utf-8") == "keep"


def test_clear_run_outputs_preserves_replacement_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "input.csv").write_text("value\n1\n", encoding="utf-8")
    output = run_dir / "output.txt"
    output.write_text("original", encoding="utf-8")
    original_unlink = filesystem_module.unlink_if_same_inode
    replaced = False

    def unlink_after_replacement(
        directory_descriptor: int,
        name: str,
        expected: tuple[int, ...],
    ) -> None:
        nonlocal replaced
        if name == "output.txt" and not replaced:
            output.unlink()
            output.write_text("attacker-replacement", encoding="utf-8")
            replaced = True
        original_unlink(directory_descriptor, name, expected)

    monkeypatch.setattr(filesystem_module, "unlink_if_same_inode", unlink_after_replacement)

    clear_run_outputs(workspace, run_dir, _directory_identity(run_dir))
    assert replaced
    assert output.read_text(encoding="utf-8") == "attacker-replacement"
    assert (run_dir / "input.csv").exists()


def test_recovery_cleanup_never_follows_replaced_run_symlink(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    run_dir.rename(workspace / "runs" / "run-original")
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    run_dir.symlink_to(outside, target_is_directory=True)

    clear_recovered_run_outputs(workspace, "run-1")

    assert victim.read_text(encoding="utf-8") == "keep"
    assert not run_dir.exists()


def test_remove_exchange_run_never_follows_symlink_to_sibling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchange = tmp_path / "exchange"
    runs = exchange / "runs"
    current = runs / "current"
    sibling = runs / "sibling"
    current.mkdir(parents=True)
    sibling.mkdir()
    identity = _directory_identity(current)
    current.rename(runs / "current-original")
    victim = sibling / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    current.symlink_to(sibling, target_is_directory=True)
    monkeypatch.setattr(
        filesystem_module,
        "settings",
        replace(settings, runtime_exchange_dir=exchange),
    )

    remove_exchange_run(current, identity)
    assert victim.read_text(encoding="utf-8") == "keep"
    assert current.is_symlink()


def test_error_log_write_rejects_replaced_run_directory(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    identity = _directory_identity(run_dir)
    run_dir.rename(workspace / "runs" / "run-original")
    outside = tmp_path / "outside"
    outside.mkdir()
    run_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeServiceError):
        write_run_error_log(workspace, run_dir, identity, b"safe error\n")
    assert not (outside / "core-execution-error.log").exists()


def test_dataset_copy_rejects_replaced_destination_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "data" / "dataset.csv"
    source.parent.mkdir(parents=True)
    content = b"value\n1\n"
    source.write_bytes(content)
    run_dir = workspace / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    run_identity = _directory_identity(run_dir)
    exchange = tmp_path / "exchange"
    exchange_run = exchange / "runs" / "run-1"
    exchange_run.mkdir(parents=True)
    exchange_identity = _directory_identity(exchange_run)
    run_dir.rename(workspace / "runs" / "run-original")
    outside = tmp_path / "outside"
    outside.mkdir()
    run_dir.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        filesystem_module,
        "settings",
        replace(settings, runtime_exchange_dir=exchange),
    )

    with pytest.raises(RuntimeServiceError):
        copy_dataset_from_safe_descriptor(
            workspace_root=workspace,
            source_path=source,
            destinations=((run_dir / "input.csv", 0o400), (exchange_run / "input.csv", 0o444)),
            expected_content_hash=hashlib.sha256(content).hexdigest(),
            expected_destination_directories={
                run_dir: run_identity,
                exchange_run: exchange_identity,
            },
        )
    assert not (outside / "input.csv").exists()


def test_runtime_input_check_rejects_real_directory_replacement(
    tmp_path: Path,
) -> None:
    exchange_run = tmp_path / "exchange" / "runs" / "run-1"
    exchange_run.mkdir(parents=True)
    content = b"value\n1\n"
    (exchange_run / "input.csv").write_bytes(content)
    identity = _directory_identity(exchange_run)
    exchange_run.rename(exchange_run.parent / "run-original")
    exchange_run.mkdir()
    replacement = exchange_run / "input.csv"
    replacement.write_bytes(content)

    with pytest.raises(RuntimeServiceError, match="identity"):
        assert_runtime_input_unchanged(
            exchange_run_dir=exchange_run,
            runtime_dataset_path=replacement,
            dataset_content_hash=hashlib.sha256(content).hexdigest(),
            expected_exchange_run_identity=identity,
        )
