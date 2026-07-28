from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import TypedDict
from unittest.mock import patch

import open_science_core.dataset_inspector as dataset_inspector
from open_science_core.dataset_inspector import (
    DatasetInspectionError,
    dataset_profile_sha256,
    exact_correlation_preflight_csv_dataset,
    exact_two_group_preflight_csv_dataset,
    inspect_csv_dataset,
)


def _write_dataset(workspace: Path, name: str, content: bytes) -> tuple[Path, str]:
    path = workspace / "data" / "raw" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o444)
    return path, hashlib.sha256(content).hexdigest()


class _InspectionArguments(TypedDict):
    workspace_root: Path
    dataset_path: Path
    source_id: str
    expected_content_hash: str
    max_sample_rows: int


class _InspectionOverrides(TypedDict, total=False):
    source_id: str
    expected_content_hash: str
    max_sample_rows: int
    seed: int


class DatasetInspectorTest(unittest.TestCase):
    def test_profiles_small_utf8_bom_csv_without_mutating_source(self) -> None:
        content = (
            "\ufeffsubject_id;group;group;measured_at;outcome;note\r\n"
            "s-1;control;A;2026-01-01;1.5;ok\r\n"
            "s-2;treated;B;2026-01-02;2;ok\r\n"
            's-3;;B;not-a-date;oops;=HYPERLINK("x")\r\n'
            "s-4;treated\r\n"
        ).encode()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            path, content_hash = _write_dataset(workspace, "experiment.csv", content)
            before_bytes = path.read_bytes()
            before_stat = path.stat()

            result = inspect_csv_dataset(
                workspace_root=workspace,
                dataset_path=path,
                source_id="dataset-source-1",
                expected_content_hash=content_hash,
                max_sample_rows=50,
            )

            profile = result.profile
            self.assertEqual(profile.filename, "experiment.csv")
            self.assertEqual(profile.content_hash, content_hash)
            self.assertEqual(profile.file_size_bytes, len(content))
            self.assertEqual(profile.encoding, "utf-8-sig")
            self.assertEqual(profile.delimiter, ";")
            self.assertEqual(profile.row_count, 4)
            self.assertEqual(profile.column_count, 6)
            self.assertEqual(profile.sampling.rows_profiled, 4)
            self.assertEqual(profile.sampling.seed, int(content_hash[:8], 16))
            self.assertEqual([column.index for column in profile.columns], list(range(6)))
            self.assertEqual(profile.columns[1].name, "group")
            self.assertEqual(profile.columns[2].name, "group")
            self.assertEqual(profile.columns[0].inferred_type, "categorical")
            self.assertTrue(profile.columns[0].potential_id)
            self.assertEqual(profile.columns[1].missing_count, 1)
            self.assertEqual(profile.columns[1].unique_count, 2)
            self.assertEqual(profile.columns[3].inferred_type, "mixed")
            self.assertTrue(profile.columns[3].potential_date)
            self.assertEqual(profile.columns[4].inferred_type, "mixed")
            self.assertIsNone(profile.columns[4].numeric_range)
            note_cardinality = profile.columns[5].low_cardinality
            assert note_cardinality is not None
            assert note_cardinality is not None
            self.assertEqual(note_cardinality.values[-1], "ok")
            self.assertTrue(
                any(value.startswith("'") for value in note_cardinality.values)
            )
            warning_codes = [warning.code for warning in profile.warnings]
            self.assertIn("duplicate-column-name", warning_codes)
            self.assertIn("malformed-row", warning_codes)
            self.assertIn("mixed-column-type", warning_codes)
            self.assertIn("other", warning_codes)
            self.assertNotIn("sample-limited", warning_codes)

            canonical = json.dumps(
                profile.model_dump(mode="json", by_alias=True, exclude_none=False),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            self.assertEqual(result.profile_sha256, hashlib.sha256(canonical).hexdigest())
            self.assertEqual(result.profile_sha256, dataset_profile_sha256(profile))
            self.assertEqual(path.read_bytes(), before_bytes)
            after_stat = path.stat()
            self.assertEqual(before_stat.st_ino, after_stat.st_ino)
            self.assertEqual(before_stat.st_mtime_ns, after_stat.st_mtime_ns)

    def test_sampling_is_bounded_deterministic_and_explicit(self) -> None:
        rows = ["id,value,group"] + [f"row-{index},{index},{index % 3}" for index in range(1_000)]
        content = ("\n".join(rows) + "\n").encode()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            path, content_hash = _write_dataset(workspace, "large.csv", content)
            arguments: _InspectionArguments = {
                "workspace_root": workspace,
                "dataset_path": path,
                "source_id": "dataset-source-2",
                "expected_content_hash": content_hash,
                "max_sample_rows": 25,
            }

            first = inspect_csv_dataset(**arguments)
            second = inspect_csv_dataset(**arguments)
            explicit = inspect_csv_dataset(**arguments, seed=7)

            self.assertEqual(first, second)
            self.assertEqual(first.profile.row_count, 1_000)
            self.assertEqual(first.profile.sampling.rows_profiled, 25)
            self.assertEqual(first.profile.sampling.max_sample_rows, 25)
            self.assertEqual(first.profile.sampling.seed, int(content_hash[:8], 16))
            self.assertEqual(explicit.profile.sampling.seed, 7)
            self.assertNotEqual(first.profile_sha256, explicit.profile_sha256)
            sample_warning = next(
                warning for warning in first.profile.warnings if warning.code == "sample-limited"
            )
            self.assertIn("row_count is the full streamed count", sample_warning.message)
            self.assertLessEqual(first.profile.columns[0].unique_count, 25)

    def test_falls_back_for_non_utf8_and_bounds_saved_display_values(self) -> None:
        long_value = "@" + "é" * 300
        content = f"label,amount\r\ncafé,1\r\n{long_value},2\r\n".encode("cp1252")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            path, content_hash = _write_dataset(workspace, "legacy.csv", content)

            result = inspect_csv_dataset(
                workspace_root=workspace,
                dataset_path=path,
                source_id="dataset-source-3",
                expected_content_hash=content_hash,
            )

            self.assertEqual(result.profile.encoding, "cp1252")
            self.assertEqual(result.profile.row_count, 2)
            low_cardinality = result.profile.columns[0].low_cardinality
            assert low_cardinality is not None
            assert low_cardinality is not None
            values = low_cardinality.values
            self.assertEqual(len(values), 2)
            self.assertTrue(all(len(value) <= 200 for value in values))
            self.assertTrue(any(value.startswith("'@") for value in values))
            serialized = result.profile.model_dump_json(by_alias=True)
            self.assertNotIn(long_value, serialized)
            self.assertIn(
                "encoding-fallback",
                [warning.code for warning in result.profile.warnings],
            )

    def test_rejects_hash_mismatch_escape_symlinks_and_non_regular_files(self) -> None:
        content = b"name,value\na,1\n"
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            workspace = Path(directory).resolve()
            path, content_hash = _write_dataset(workspace, "safe.csv", content)
            outside_path = Path(outside).resolve() / "outside.csv"
            outside_path.write_bytes(content)

            with self.assertRaisesRegex(DatasetInspectionError, "content hash"):
                inspect_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=path,
                    source_id="dataset-source-4",
                    expected_content_hash="0" * 64,
                )
            with self.assertRaisesRegex(DatasetInspectionError, "escapes the workspace"):
                inspect_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=outside_path,
                    source_id="dataset-source-4",
                    expected_content_hash=content_hash,
                )
            with self.assertRaisesRegex(DatasetInspectionError, "parent traversal"):
                inspect_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=Path("data/raw/../../outside.csv"),
                    source_id="dataset-source-4",
                    expected_content_hash=content_hash,
                )
            with self.assertRaisesRegex(DatasetInspectionError, "regular file"):
                inspect_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=path.parent,
                    source_id="dataset-source-4",
                    expected_content_hash=content_hash,
                )

            symlink = path.parent / "linked.csv"
            symlink.symlink_to(path)
            with self.assertRaisesRegex(DatasetInspectionError, "symbolic links"):
                inspect_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=symlink,
                    source_id="dataset-source-4",
                    expected_content_hash=content_hash,
                )
            if hasattr(os, "mkfifo"):
                fifo = path.parent / "dataset.fifo"
                os.mkfifo(fifo)
                with self.assertRaisesRegex(DatasetInspectionError, "regular file"):
                    inspect_csv_dataset(
                        workspace_root=workspace,
                        dataset_path=fifo,
                        source_id="dataset-source-4",
                        expected_content_hash=content_hash,
                    )
            linked_directory = workspace / "linked-data"
            linked_directory.symlink_to(path.parent, target_is_directory=True)
            with self.assertRaisesRegex(DatasetInspectionError, "symbolic links"):
                inspect_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=linked_directory / path.name,
                    source_id="dataset-source-4",
                    expected_content_hash=content_hash,
                )

            linked_workspace = workspace.parent / f"{workspace.name}-link"
            linked_workspace.symlink_to(workspace, target_is_directory=True)
            try:
                with self.assertRaisesRegex(DatasetInspectionError, "Workspace root.*symbolic"):
                    inspect_csv_dataset(
                        workspace_root=linked_workspace,
                        dataset_path=linked_workspace / "data" / "raw" / path.name,
                        source_id="dataset-source-4",
                        expected_content_hash=content_hash,
                    )
            finally:
                linked_workspace.unlink(missing_ok=True)

    def test_rejects_unsafe_inputs_and_oversized_record(self) -> None:
        content = b"name,value\na,1\n"
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            path, content_hash = _write_dataset(workspace, "safe.csv", content)
            invalid_arguments: list[_InspectionOverrides] = [
                {"source_id": "", "expected_content_hash": content_hash},
                {"source_id": "source", "expected_content_hash": content_hash.upper()},
                {
                    "source_id": "source",
                    "expected_content_hash": content_hash,
                    "max_sample_rows": 0,
                },
                {
                    "source_id": "source",
                    "expected_content_hash": content_hash,
                    "seed": 2**32,
                },
            ]
            for overrides in invalid_arguments:
                with self.subTest(overrides=overrides), self.assertRaises(DatasetInspectionError):
                    inspect_csv_dataset(
                        workspace_root=workspace,
                        dataset_path=path,
                        **overrides,
                    )

            path.chmod(0o644)
            oversized = b"name\n" + b"x" * (4 * 1024 * 1024 + 1) + b"\n"
            path.write_bytes(oversized)
            path.chmod(0o444)
            oversized_hash = hashlib.sha256(oversized).hexdigest()
            with self.assertRaisesRegex(DatasetInspectionError, "safe .*inspection limit"):
                inspect_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=path,
                    source_id="source",
                    expected_content_hash=oversized_hash,
                )

            path.chmod(0o644)
            nul_content = b"name,value\n" + b"a,1\n" * 20_000 + b"b,\x00\n"
            path.write_bytes(nul_content)
            path.chmod(0o444)
            with self.assertRaisesRegex(DatasetInspectionError, "NUL bytes"):
                inspect_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=path,
                    source_id="source",
                    expected_content_hash=hashlib.sha256(nul_content).hexdigest(),
                )

    def test_rejects_sparse_file_and_bounds_wide_dataset_sample(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            sparse_path = workspace / "sparse.csv"
            with sparse_path.open("wb") as handle:
                handle.truncate(1_025)
            sparse_path.chmod(0o444)
            with self.assertRaisesRegex(DatasetInspectionError, "inspection size limit"):
                inspect_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=sparse_path,
                    source_id="dataset-source-6",
                    expected_content_hash="0" * 64,
                    max_file_bytes=1_024,
                )

            headers = [f"column_{index}" for index in range(100)]
            rows = [",".join(headers)]
            rows.extend(
                ",".join(f"value-{row_index}-{column_index}" for column_index in range(100))
                for row_index in range(500)
            )
            content = ("\n".join(rows) + "\n").encode()
            path, content_hash = _write_dataset(workspace, "wide.csv", content)
            result = inspect_csv_dataset(
                workspace_root=workspace,
                dataset_path=path,
                source_id="dataset-source-6",
                expected_content_hash=content_hash,
                max_sample_rows=500,
            )

            self.assertEqual(result.profile.row_count, 500)
            self.assertEqual(result.profile.sampling.rows_profiled, 200)
            self.assertLessEqual(
                result.profile.sampling.rows_profiled * result.profile.column_count,
                20_000,
            )
            self.assertIn(
                "sample-limited",
                [warning.code for warning in result.profile.warnings],
            )

    def test_detects_same_size_rewrite_with_restored_mtime(self) -> None:
        original = b"name,value\na,1\n"
        replacement = b"name,value\nb,2\n"
        self.assertEqual(len(original), len(replacement))
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            path, content_hash = _write_dataset(workspace, "mutable.csv", original)
            original_stat = path.stat()
            original_parser = dataset_inspector.parse_with_encoding_fallback

            def parse_then_tamper(
                descriptor: int,
                prefix: bytes,
                *,
                max_sample_rows: int,
                seed: int,
            ) -> object:
                result = original_parser(
                    descriptor,
                    prefix,
                    max_sample_rows=max_sample_rows,
                    seed=seed,
                )
                path.chmod(0o644)
                path.write_bytes(replacement)
                path.chmod(0o444)
                os.utime(
                    path,
                    ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                )
                return result

            with (
                patch.object(
                    dataset_inspector,
                    "parse_with_encoding_fallback",
                    side_effect=parse_then_tamper,
                ),
                self.assertRaisesRegex(DatasetInspectionError, "changed while"),
            ):
                inspect_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=path,
                    source_id="dataset-source-7",
                    expected_content_hash=content_hash,
                )

    def test_rejects_workspace_root_swap_to_symlink_before_open(self) -> None:
        content = b"name,value\na,1\n"
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            workspace = Path(directory).resolve()
            path, content_hash = _write_dataset(workspace, "mutable-root.csv", content)
            outside_root = Path(outside).resolve()
            _write_dataset(outside_root, "mutable-root.csv", content)
            moved_workspace = workspace.with_name(f"{workspace.name}-original")
            original_open = dataset_inspector.open_workspace_file

            def swap_root_before_open(
                root: Path,
                relative: Path,
                *,
                expected_root_identity: tuple[int, int, int],
            ) -> int:
                workspace.rename(moved_workspace)
                workspace.symlink_to(outside_root, target_is_directory=True)
                try:
                    return original_open(
                        root,
                        relative,
                        expected_root_identity=expected_root_identity,
                    )
                finally:
                    workspace.unlink(missing_ok=True)
                    moved_workspace.rename(workspace)

            with (
                patch.object(
                    dataset_inspector,
                    "open_workspace_file",
                    side_effect=swap_root_before_open,
                ),
                self.assertRaisesRegex(DatasetInspectionError, "Workspace root.*safely"),
            ):
                inspect_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=path,
                    source_id="dataset-source-root-race",
                    expected_content_hash=content_hash,
                )

    def test_rejects_workspace_root_swap_to_another_real_directory(self) -> None:
        original_content = b"name,value\na,1\n"
        replacement_content = b"name,value\nb,2\n"
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            path, content_hash = _write_dataset(
                workspace,
                "real-root-race.csv",
                original_content,
            )
            moved_workspace = workspace.with_name(f"{workspace.name}-original")
            replacement_workspace = workspace.with_name(f"{workspace.name}-replacement")
            replacement_path, _replacement_hash = _write_dataset(
                replacement_workspace,
                "real-root-race.csv",
                replacement_content,
            )
            self.assertEqual(
                path.relative_to(workspace),
                replacement_path.relative_to(replacement_workspace),
            )
            original_open = dataset_inspector.open_workspace_file

            def swap_real_root_before_open(
                root: Path,
                relative: Path,
                *,
                expected_root_identity: tuple[int, int, int],
            ) -> int:
                workspace.rename(moved_workspace)
                replacement_workspace.rename(workspace)
                try:
                    return original_open(
                        root,
                        relative,
                        expected_root_identity=expected_root_identity,
                    )
                finally:
                    workspace.rename(replacement_workspace)
                    moved_workspace.rename(workspace)

            try:
                with (
                    patch.object(
                        dataset_inspector,
                        "open_workspace_file",
                        side_effect=swap_real_root_before_open,
                    ),
                    self.assertRaisesRegex(DatasetInspectionError, "Workspace root changed"),
                ):
                    inspect_csv_dataset(
                        workspace_root=workspace,
                        dataset_path=path,
                        source_id="dataset-source-real-root-race",
                        expected_content_hash=content_hash,
                    )
            finally:
                if replacement_workspace.exists():
                    for child in sorted(replacement_workspace.rglob("*"), reverse=True):
                        if child.is_file():
                            child.unlink()
                        elif child.is_dir():
                            child.rmdir()
                    replacement_workspace.rmdir()

    def test_enforces_serialized_profile_budget(self) -> None:
        content = b"name,value\na,1\n"
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            path, content_hash = _write_dataset(workspace, "safe.csv", content)
            with (
                patch.object(dataset_inspector, "_MAX_SERIALIZED_PROFILE_BYTES", 100),
                self.assertRaisesRegex(DatasetInspectionError, "profile exceeds"),
            ):
                inspect_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=path,
                    source_id="dataset-source-8",
                    expected_content_hash=content_hash,
                )

    def test_rejects_hard_link_to_workspace_file(self) -> None:
        if os.name == "nt":
            self.skipTest("hard-link semantics differ on Windows")
        content = b"name,value\na,1\n"
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            path, content_hash = _write_dataset(workspace, "safe.csv", content)
            hard_link = workspace / "data" / "raw" / "copy.csv"
            os.link(path, hard_link)
            with self.assertRaisesRegex(DatasetInspectionError, "hard links"):
                inspect_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=path,
                    source_id="dataset-source-5",
                    expected_content_hash=content_hash,
                )

    def test_exact_two_group_preflight_handles_cp1252_semicolon_and_special_values(
        self,
    ) -> None:
        content = (
            "group;score\r\n"
            "=contrôle;1\r\n"
            "=contrôle;NA\r\n"
            '"traité;B";3\r\n'
            "other;9\r\n"
            ";5\r\n"
        ).encode("cp1252")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            path, content_hash = _write_dataset(workspace, "groups.csv", content)

            result = exact_two_group_preflight_csv_dataset(
                workspace_root=workspace,
                dataset_path=path,
                expected_content_hash=content_hash,
                outcome_column="score",
                group_column="group",
                groups=("=contrôle", "traité;B"),
            )

            self.assertEqual(result.encoding, "cp1252")
            self.assertEqual(result.delimiter, ";")
            self.assertEqual(result.rows_read, 5)
            self.assertEqual(dict(result.valid_counts), {"=contrôle": 1, "traité;B": 1})
            self.assertEqual(dict(result.missing_counts), {"=contrôle": 1, "traité;B": 0})
            self.assertEqual(
                dict(result.non_constant_groups),
                {"=contrôle": False, "traité;B": False},
            )
            self.assertEqual(result.excluded_row_count, 2)

    def test_exact_correlation_preflight_counts_complete_and_missing_pairs(self) -> None:
        content = b"x|y|note\n+1|2|a\nNA|3|b\n4|null|c\n1e2|6|d\n"
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            path, content_hash = _write_dataset(workspace, "correlation.csv", content)

            result = exact_correlation_preflight_csv_dataset(
                workspace_root=workspace,
                dataset_path=path,
                expected_content_hash=content_hash,
                x_column="x",
                y_column="y",
            )

            self.assertEqual(result.delimiter, "|")
            self.assertEqual(result.rows_read, 4)
            self.assertEqual(result.valid_pair_count, 2)
            self.assertEqual(result.missing_pair_count, 2)

    def test_exact_preflight_streams_every_row_without_sampling(self) -> None:
        rows = ["group,score"]
        rows.extend(
            f"{'a' if index % 2 == 0 else 'b'},{index}"
            for index in range(2_001)
        )
        content = ("\n".join(rows) + "\n").encode()
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            path, content_hash = _write_dataset(workspace, "complete.csv", content)

            result = exact_two_group_preflight_csv_dataset(
                workspace_root=workspace,
                dataset_path=path,
                expected_content_hash=content_hash,
                outcome_column="score",
                group_column="group",
                groups=("a", "b"),
            )

            self.assertEqual(result.rows_read, 2_001)
            self.assertEqual(dict(result.valid_counts), {"a": 1_001, "b": 1_000})
            self.assertEqual(dict(result.missing_counts), {"a": 0, "b": 0})
            self.assertEqual(
                dict(result.non_constant_groups), {"a": True, "b": True}
            )
            self.assertEqual(result.excluded_row_count, 0)

    def test_exact_preflight_rejects_missing_duplicate_and_non_numeric_columns(
        self,
    ) -> None:
        fixtures = {
            "missing.csv": b"group,score\na,1\nb,2\n",
            "duplicate.csv": b"group,score,score\na,1,2\nb,2,3\n",
            "non-numeric.csv": b"group,score\na,1\nb,not-a-number\n",
        }
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            written = {
                name: _write_dataset(workspace, name, content)
                for name, content in fixtures.items()
            }

            missing_path, missing_hash = written["missing.csv"]
            with self.assertRaisesRegex(DatasetInspectionError, "not present"):
                exact_correlation_preflight_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=missing_path,
                    expected_content_hash=missing_hash,
                    x_column="score",
                    y_column="absent",
                )

            duplicate_path, duplicate_hash = written["duplicate.csv"]
            with self.assertRaisesRegex(DatasetInspectionError, "duplicate"):
                exact_two_group_preflight_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=duplicate_path,
                    expected_content_hash=duplicate_hash,
                    outcome_column="score",
                    group_column="group",
                    groups=("a", "b"),
                )

            non_numeric_path, non_numeric_hash = written["non-numeric.csv"]
            with self.assertRaisesRegex(DatasetInspectionError, "non-numeric"):
                exact_two_group_preflight_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=non_numeric_path,
                    expected_content_hash=non_numeric_hash,
                    outcome_column="score",
                    group_column="group",
                    groups=("a", "b"),
                )

    def test_exact_preflight_rejects_hash_mismatch_and_file_change(self) -> None:
        original = b"x,y\n1,2\n3,4\n"
        replacement = b"x,y\n5,6\n7,8\n"
        self.assertEqual(len(original), len(replacement))
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            path, content_hash = _write_dataset(workspace, "mutable.csv", original)

            with self.assertRaisesRegex(DatasetInspectionError, "content hash"):
                exact_correlation_preflight_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=path,
                    expected_content_hash="0" * 64,
                    x_column="x",
                    y_column="y",
                )

            original_stat = path.stat()
            original_parser = dataset_inspector.exact_preflight_with_encoding_fallback

            def parse_then_tamper(
                descriptor: int,
                prefix: bytes,
                *,
                request: object,
            ) -> object:
                result = original_parser(
                    descriptor,
                    prefix,
                    request=request,  # type: ignore[arg-type]
                )
                path.chmod(0o644)
                path.write_bytes(replacement)
                path.chmod(0o444)
                os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
                return result

            with (
                patch.object(
                    dataset_inspector,
                    "exact_preflight_with_encoding_fallback",
                    side_effect=parse_then_tamper,
                ),
                self.assertRaisesRegex(DatasetInspectionError, "changed while"),
            ):
                exact_correlation_preflight_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=path,
                    expected_content_hash=content_hash,
                    x_column="x",
                    y_column="y",
                )

    def test_exact_preflight_enforces_record_bound(self) -> None:
        content = b"group,score\na," + (b"1" * 100) + b"\n"
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            path, content_hash = _write_dataset(workspace, "oversized.csv", content)

            with (
                patch.object(dataset_inspector, "_MAX_RECORD_CHARS", 32),
                self.assertRaisesRegex(DatasetInspectionError, "4 MiB inspection limit"),
            ):
                exact_two_group_preflight_csv_dataset(
                    workspace_root=workspace,
                    dataset_path=path,
                    expected_content_hash=content_hash,
                    outcome_column="score",
                    group_column="group",
                    groups=("a", "b"),
                )


if __name__ == "__main__":
    unittest.main()
