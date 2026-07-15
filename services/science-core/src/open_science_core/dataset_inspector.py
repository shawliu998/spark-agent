from __future__ import annotations

import codecs
import csv
import hashlib
import io
import math
import os
import random
import re
import stat
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator, Protocol, TextIO

from .config import settings
from .workflow._service.integrity import canonical_json_bytes
from .workflow.schemas import (
    DatasetColumnInferredType,
    DatasetColumnProfile,
    DatasetInspectionWarning,
    DatasetLowCardinalitySummary,
    DatasetNumericRange,
    DatasetProfile,
    DatasetSamplingRecord,
)

_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_INTEGER_PATTERN = re.compile(r"^[+-]?\d+$")
_NUMBER_PATTERN = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_DATE_PREFIX_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]|$)")
_DATE_NAME_PATTERN = re.compile(r"(?:date|time|timestamp|_at)$", re.IGNORECASE)
_ID_NAME_PATTERN = re.compile(r"(?:^|[_\s-])id(?:$|[_\s-])", re.IGNORECASE)
_MISSING_VALUES = frozenset({"", "na", "n/a", "nan", "none", "null"})
_BOOLEAN_VALUES = frozenset({"true", "false", "yes", "no", "y", "n"})
_DELIMITERS = ",;\t|"
_PREFIX_BYTES = 64 * 1024
_MAX_RECORD_CHARS = 4 * 1024 * 1024
_MAX_PROFILE_CELL_CHARS = 1_024
_MAX_DISPLAY_CHARS = 200
_MAX_SAMPLE_CELLS = 20_000
_MAX_SERIALIZED_PROFILE_BYTES = 8 * 1024 * 1024
_LOW_CARDINALITY_LIMIT = 20
_MAX_MIXED_TYPE_WARNINGS = 100


class DatasetInspectionError(ValueError):
    """Raised when a dataset cannot be inspected without violating safety guarantees."""


@dataclass(frozen=True, slots=True)
class DatasetInspectionResult:
    profile: DatasetProfile
    profile_sha256: str


@dataclass(frozen=True, slots=True)
class _SampleCell:
    value: str
    fingerprint: str
    display: str


@dataclass(frozen=True, slots=True)
class _SampleRow:
    row_index: int
    cells: tuple[_SampleCell, ...]


@dataclass(frozen=True, slots=True)
class _ParsedCsv:
    headers: tuple[str, ...]
    rows_read: int
    sampled_rows: tuple[_SampleRow, ...]
    malformed_rows: int
    duplicate_headers: bool
    header_display_changed: bool
    sample_display_changed: bool
    sample_value_truncated: bool


class _CsvRecordTooLarge(Exception):
    pass


class _CsvReader(Protocol):
    line_num: int

    def __next__(self) -> list[str]: ...


class _BoundedLineIterator(Iterator[str]):
    """Bound memory used by one logical record consumed by ``csv.reader``."""

    def __init__(self, stream: TextIO, max_record_chars: int) -> None:
        self._stream = stream
        self._max_record_chars = max_record_chars
        self._record_chars = 0

    def start_record(self) -> None:
        self._record_chars = 0

    def __iter__(self) -> _BoundedLineIterator:
        return self

    def __next__(self) -> str:
        remaining = self._max_record_chars - self._record_chars
        if remaining <= 0:
            raise _CsvRecordTooLarge
        line = self._stream.readline(remaining + 1)
        if not line:
            raise StopIteration
        self._record_chars += len(line)
        if self._record_chars > self._max_record_chars:
            raise _CsvRecordTooLarge
        return line


def dataset_profile_sha256(profile: DatasetProfile) -> str:
    """Hash the complete camel-case profile using the workflow canonical JSON format."""

    return hashlib.sha256(_canonical_profile_bytes(profile)).hexdigest()


def inspect_csv_dataset(
    *,
    workspace_root: Path,
    dataset_path: Path,
    source_id: str,
    expected_content_hash: str,
    max_sample_rows: int = 500,
    seed: int | None = None,
    max_file_bytes: int | None = None,
) -> DatasetInspectionResult:
    """Inspect an immutable workspace CSV without modifying or fully materializing it.

    ``row_count`` is always streamed over the complete file. Column statistics are
    calculated over the deterministic bounded sample recorded in ``sampling``; when
    sampling is necessary, the profile carries an explicit ``sample-limited`` warning.
    """

    effective_max_file_bytes = (
        settings.max_upload_bytes if max_file_bytes is None else max_file_bytes
    )
    _validate_inputs(
        source_id=source_id,
        expected_content_hash=expected_content_hash,
        max_sample_rows=max_sample_rows,
        seed=seed,
        max_file_bytes=effective_max_file_bytes,
    )
    effective_seed = int(expected_content_hash[:8], 16) if seed is None else seed
    relative_path, lexical_root, root_identity = _workspace_relative_path(
        workspace_root, dataset_path
    )
    descriptor = open_workspace_file(
        lexical_root,
        relative_path,
        expected_root_identity=root_identity,
    )
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise DatasetInspectionError("Dataset path is not a regular file")
        if before.st_nlink != 1:
            raise DatasetInspectionError("Dataset file must not have external hard links")
        if before.st_size <= 0:
            raise DatasetInspectionError("Dataset file is empty")
        if before.st_size > effective_max_file_bytes:
            raise DatasetInspectionError("Dataset exceeds the configured inspection size limit")

        content_hash, prefix, contains_nul = _hash_and_prefix(descriptor)
        after_hash = os.fstat(descriptor)
        _assert_unchanged(before, after_hash)
        if content_hash != expected_content_hash:
            raise DatasetInspectionError("Dataset content hash does not match the expected hash")
        if contains_nul:
            raise DatasetInspectionError("Dataset contains NUL bytes and is not a safe text CSV")

        encoding, parsed = parse_with_encoding_fallback(
            descriptor,
            prefix,
            max_sample_rows=max_sample_rows,
            seed=effective_seed,
        )
        after_parse = os.fstat(descriptor)
        _assert_unchanged(before, after_parse)

        columns, mixed_columns = _profile_columns(parsed.headers, parsed.sampled_rows)
        warnings = _build_warnings(
            encoding=encoding,
            parsed=parsed,
            mixed_columns=mixed_columns,
        )
        filename, filename_changed = _safe_display(relative_path.name, max_length=255)
        if not filename:
            raise DatasetInspectionError("Dataset filename has no safe display representation")
        if filename_changed:
            warnings.append(
                DatasetInspectionWarning(
                    code="other",
                    message="Unsafe or overlong filename characters were escaped in the profile.",
                    column_name=None,
                )
            )

        delimiter = _detect_delimiter(prefix, encoding)
        profile = DatasetProfile(
            schema_version="1",
            dataset_source_id=source_id,
            filename=filename,
            content_hash=content_hash,
            file_size_bytes=before.st_size,
            encoding=encoding,
            delimiter=delimiter,
            row_count=parsed.rows_read,
            column_count=len(parsed.headers),
            columns=columns,
            sampling=DatasetSamplingRecord(
                method="head-and-reservoir-v1",
                rows_read=parsed.rows_read,
                rows_profiled=len(parsed.sampled_rows),
                max_sample_rows=max_sample_rows,
                seed=effective_seed,
            ),
            warnings=warnings,
        )
        canonical_profile = _canonical_profile_bytes(profile)
        if len(canonical_profile) > _MAX_SERIALIZED_PROFILE_BYTES:
            raise DatasetInspectionError("Dataset profile exceeds the safe serialized size limit")
        return DatasetInspectionResult(
            profile=profile,
            profile_sha256=hashlib.sha256(canonical_profile).hexdigest(),
        )
    finally:
        os.close(descriptor)


def _validate_inputs(
    *,
    source_id: str,
    expected_content_hash: str,
    max_sample_rows: int,
    seed: int | None,
    max_file_bytes: int,
) -> None:
    if (
        not source_id
        or len(source_id) > 36
        or any(not character.isprintable() for character in source_id)
    ):
        raise DatasetInspectionError("source_id must be a non-empty identifier of at most 36 chars")
    if _HASH_PATTERN.fullmatch(expected_content_hash) is None:
        raise DatasetInspectionError("expected_content_hash must be lowercase hexadecimal SHA-256")
    if isinstance(max_sample_rows, bool) or not 1 <= max_sample_rows <= 10_000:
        raise DatasetInspectionError("max_sample_rows must be between 1 and 10000")
    if seed is not None and (
        isinstance(seed, bool) or not 0 <= seed <= 2**32 - 1
    ):
        raise DatasetInspectionError("seed must be an unsigned 32-bit integer")
    if (
        isinstance(max_file_bytes, bool)
        or max_file_bytes < 1
    ):
        raise DatasetInspectionError("max_file_bytes must be a positive integer")


def _workspace_relative_path(
    workspace_root: Path,
    dataset_path: Path,
) -> tuple[Path, Path, tuple[int, int, int]]:
    if not workspace_root.is_absolute():
        raise DatasetInspectionError("workspace_root must be absolute")
    if ".." in dataset_path.parts:
        raise DatasetInspectionError("Dataset path may not contain parent traversal")

    lexical_root = Path(os.path.abspath(workspace_root))
    try:
        root_metadata = lexical_root.lstat()
    except OSError as error:
        raise DatasetInspectionError("Workspace root is unavailable") from error
    if stat.S_ISLNK(root_metadata.st_mode):
        raise DatasetInspectionError("Workspace root may not be a symbolic link")
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise DatasetInspectionError("Workspace root is not a directory")
    lexical_candidate = (
        Path(os.path.abspath(dataset_path))
        if dataset_path.is_absolute()
        else Path(os.path.abspath(lexical_root / dataset_path))
    )
    try:
        relative = lexical_candidate.relative_to(lexical_root)
    except ValueError as error:
        raise DatasetInspectionError("Dataset path escapes the workspace") from error

    if not relative.parts or relative == Path("."):
        raise DatasetInspectionError("Dataset path must name a file within the workspace")
    # Keep the original authorization root. open_workspace_file opens it with
    # O_NOFOLLOW and holds the descriptor, so a rename-to-symlink race cannot
    # silently authorize the replacement target.
    return relative, lexical_root, (
        root_metadata.st_dev,
        root_metadata.st_ino,
        root_metadata.st_mode,
    )


def open_workspace_file(
    workspace_root: Path,
    relative_path: Path,
    *,
    expected_root_identity: tuple[int, int, int],
) -> int:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
        file_flags |= os.O_NOFOLLOW

    try:
        current_descriptor = os.open(workspace_root, directory_flags)
    except OSError as error:
        raise DatasetInspectionError("Workspace root cannot be opened safely") from error

    try:
        opened_root = os.fstat(current_descriptor)
        opened_root_identity = (
            opened_root.st_dev,
            opened_root.st_ino,
            opened_root.st_mode,
        )
        if opened_root_identity != expected_root_identity:
            raise DatasetInspectionError("Workspace root changed before it was opened safely")
        for component in relative_path.parts[:-1]:
            _reject_symlink_at(current_descriptor, component)
            next_descriptor = os.open(component, directory_flags, dir_fd=current_descriptor)
            os.close(current_descriptor)
            current_descriptor = next_descriptor
        filename = relative_path.parts[-1]
        mode = _reject_symlink_at(current_descriptor, filename)
        if not stat.S_ISREG(mode):
            raise DatasetInspectionError("Dataset path is not a regular file")
        return os.open(filename, file_flags, dir_fd=current_descriptor)
    except OSError as error:
        raise DatasetInspectionError("Dataset path cannot be opened safely") from error
    finally:
        os.close(current_descriptor)


def _reject_symlink_at(directory_descriptor: int, component: str) -> int:
    try:
        mode = os.stat(component, dir_fd=directory_descriptor, follow_symlinks=False).st_mode
    except OSError as error:
        raise DatasetInspectionError("Dataset path component is unavailable") from error
    if stat.S_ISLNK(mode):
        raise DatasetInspectionError("Dataset path may not contain symbolic links")
    return mode


def stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_mode,
        value.st_nlink,
    )


def _assert_unchanged(before: os.stat_result, after: os.stat_result) -> None:
    if stat_identity(before) != stat_identity(after):
        raise DatasetInspectionError("Dataset changed while it was being inspected")


def _canonical_profile_bytes(profile: DatasetProfile) -> bytes:
    payload = profile.model_dump(mode="json", by_alias=True, exclude_none=False)
    return canonical_json_bytes(payload)


def _hash_and_prefix(descriptor: int) -> tuple[str, bytes, bool]:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    prefix = bytearray()
    contains_nul = False
    while chunk := os.read(descriptor, 1024 * 1024):
        digest.update(chunk)
        contains_nul = contains_nul or b"\x00" in chunk
        if len(prefix) < _PREFIX_BYTES:
            prefix.extend(chunk[: _PREFIX_BYTES - len(prefix)])
    return digest.hexdigest(), bytes(prefix), contains_nul


def _encoding_candidates(prefix: bytes) -> tuple[str, ...]:
    if prefix.startswith(codecs.BOM_UTF8):
        return "utf-8-sig", "cp1252", "latin-1"
    try:
        codecs.getincrementaldecoder("utf-8")(errors="strict").decode(prefix, final=False)
    except UnicodeDecodeError:
        return "cp1252", "latin-1"
    return "utf-8", "cp1252", "latin-1"


def parse_with_encoding_fallback(
    descriptor: int,
    prefix: bytes,
    *,
    max_sample_rows: int,
    seed: int,
) -> tuple[str, _ParsedCsv]:
    last_decode_error: UnicodeDecodeError | None = None
    for encoding in _encoding_candidates(prefix):
        try:
            delimiter = _detect_delimiter(prefix, encoding)
            parsed = _parse_csv(
                descriptor,
                encoding=encoding,
                delimiter=delimiter,
                max_sample_rows=max_sample_rows,
                seed=seed,
            )
            return encoding, parsed
        except UnicodeDecodeError as error:
            last_decode_error = error
            continue
    raise DatasetInspectionError("Dataset encoding cannot be decoded safely") from last_decode_error


def _detect_delimiter(prefix: bytes, encoding: str) -> str:
    try:
        text = prefix.decode(encoding, errors="strict")
    except UnicodeDecodeError:
        decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
        text = decoder.decode(prefix, final=False)
    try:
        return csv.Sniffer().sniff(text, delimiters=_DELIMITERS).delimiter
    except csv.Error:
        first_nonempty = next((line for line in text.splitlines() if line.strip()), "")
        counts = {delimiter: first_nonempty.count(delimiter) for delimiter in _DELIMITERS}
        delimiter, count = max(
            counts.items(), key=lambda item: (item[1], -_DELIMITERS.index(item[0]))
        )
        return delimiter if count else ","


def _parse_csv(
    descriptor: int,
    *,
    encoding: str,
    delimiter: str,
    max_sample_rows: int,
    seed: int,
) -> _ParsedCsv:
    os.lseek(descriptor, 0, os.SEEK_SET)
    binary_stream = os.fdopen(os.dup(descriptor), "rb", closefd=True)
    text_stream = io.TextIOWrapper(binary_stream, encoding=encoding, errors="strict", newline="")
    bounded_lines = _BoundedLineIterator(text_stream, _MAX_RECORD_CHARS)
    reader = csv.reader(bounded_lines, delimiter=delimiter, strict=True)
    try:
        header_row = _read_header(reader, bounded_lines)
        if not header_row:
            raise DatasetInspectionError("CSV has no usable header row")
        if len(header_row) > 10_000:
            raise DatasetInspectionError("CSV exceeds the 10000-column inspection limit")

        headers: list[str] = []
        header_changed = False
        for index, raw_header in enumerate(header_row):
            normalized = raw_header.strip()
            if not normalized:
                normalized = f"column_{index + 1}"
                header_changed = True
            safe_header, changed = _safe_display(normalized, max_length=1_000)
            if not safe_header:
                safe_header = f"column_{index + 1}"
                changed = True
            headers.append(safe_header)
            header_changed = header_changed or changed

        duplicate_headers = len(set(headers)) != len(headers)
        sample_capacity = min(
            max_sample_rows,
            max(1, _MAX_SAMPLE_CELLS // len(headers)),
        )
        head_capacity = (sample_capacity + 1) // 2
        reservoir_capacity = sample_capacity - head_capacity
        head_rows: list[_SampleRow] = []
        reservoir_rows: list[_SampleRow] = []
        random_generator = random.Random(seed)
        row_count = 0
        malformed_rows = 0
        sample_display_changed = False
        sample_value_truncated = False

        while True:
            bounded_lines.start_record()
            prior_line = reader.line_num
            parse_failed = False
            try:
                row = next(reader)
            except StopIteration:
                break
            except _CsvRecordTooLarge as error:
                raise DatasetInspectionError(
                    "CSV record exceeds the safe 4 MiB inspection limit"
                ) from error
            except csv.Error as error:
                if "field larger than field limit" in str(error):
                    raise DatasetInspectionError(
                        "CSV field exceeds the safe inspection limit"
                    ) from error
                if reader.line_num <= prior_line:
                    raise DatasetInspectionError(
                        "Malformed CSV cannot be safely resumed"
                    ) from error
                parse_failed = True
                row = []

            if not row and not parse_failed:
                continue
            row_count += 1
            if parse_failed or len(row) != len(headers):
                malformed_rows += 1
            row_index = row_count - 1
            target = _sample_target(
                row_count=row_count,
                head_capacity=head_capacity,
                reservoir_capacity=reservoir_capacity,
                reservoir_size=len(reservoir_rows),
                random_generator=random_generator,
            )
            if target is None:
                continue
            sampled, display_changed, value_truncated = _sample_row(
                row_index,
                row,
                column_count=len(headers),
            )
            sample_display_changed = sample_display_changed or display_changed
            sample_value_truncated = sample_value_truncated or value_truncated
            if target == "head":
                head_rows.append(sampled)
            elif target == "append":
                reservoir_rows.append(sampled)
            elif isinstance(target, int):
                reservoir_rows[target] = sampled
            else:
                raise AssertionError(f"unexpected sample target: {target}")

        sampled_rows = tuple(head_rows + sorted(reservoir_rows, key=lambda item: item.row_index))
        return _ParsedCsv(
            headers=tuple(headers),
            rows_read=row_count,
            sampled_rows=sampled_rows,
            malformed_rows=malformed_rows,
            duplicate_headers=duplicate_headers,
            header_display_changed=header_changed,
            sample_display_changed=sample_display_changed,
            sample_value_truncated=sample_value_truncated,
        )
    except _CsvRecordTooLarge as error:
        raise DatasetInspectionError(
            "CSV record exceeds the safe 4 MiB inspection limit"
        ) from error
    finally:
        text_stream.close()


def _read_header(reader: _CsvReader, bounded_lines: _BoundedLineIterator) -> list[str]:
    while True:
        bounded_lines.start_record()
        try:
            row = next(reader)
        except StopIteration:
            return []
        except csv.Error as error:
            raise DatasetInspectionError("CSV header is malformed") from error
        if row and any(cell.strip() for cell in row):
            return row


def _sample_target(
    *,
    row_count: int,
    head_capacity: int,
    reservoir_capacity: int,
    reservoir_size: int,
    random_generator: random.Random,
) -> str | int | None:
    if row_count <= head_capacity:
        return "head"
    if reservoir_capacity == 0:
        return None
    tail_index = row_count - head_capacity - 1
    if reservoir_size < reservoir_capacity:
        return "append"
    replacement = random_generator.randrange(tail_index + 1)
    return replacement if replacement < reservoir_capacity else None


def _sample_row(
    row_index: int,
    row: list[str],
    *,
    column_count: int,
) -> tuple[_SampleRow, bool, bool]:
    sampled_cells: list[_SampleCell] = []
    display_changed = False
    value_truncated = False
    for index in range(column_count):
        raw_value = row[index].strip() if index < len(row) else ""
        fingerprint = hashlib.sha256(raw_value.encode("utf-8")).hexdigest()
        profile_value = raw_value[:_MAX_PROFILE_CELL_CHARS]
        display, changed = _safe_display(raw_value, max_length=_MAX_DISPLAY_CHARS)
        truncated = len(raw_value) > _MAX_PROFILE_CELL_CHARS
        sampled_cells.append(
            _SampleCell(
                value=profile_value,
                fingerprint=fingerprint,
                display=display,
            )
        )
        display_changed = display_changed or changed
        value_truncated = value_truncated or truncated
    return (
        _SampleRow(row_index=row_index, cells=tuple(sampled_cells)),
        display_changed,
        value_truncated,
    )


def _safe_display(value: str, *, max_length: int) -> tuple[str, bool]:
    sanitized = "".join("�" if not character.isprintable() else character for character in value)
    sanitized = " ".join(sanitized.split())
    if sanitized.startswith(("=", "+", "-", "@")):
        sanitized = "'" + sanitized
    changed = sanitized != value
    if len(sanitized) > max_length:
        sanitized = sanitized[: max(0, max_length - 1)] + "…"
        changed = True
    return sanitized, changed


def _profile_columns(
    headers: tuple[str, ...],
    sampled_rows: tuple[_SampleRow, ...],
) -> tuple[list[DatasetColumnProfile], list[str]]:
    columns: list[DatasetColumnProfile] = []
    mixed_columns: list[str] = []
    for index, name in enumerate(headers):
        cells = [row.cells[index] for row in sampled_rows]
        profile = _profile_column(index=index, name=name, cells=cells)
        columns.append(profile)
        if profile.mixed_type:
            mixed_columns.append(name)
    return columns, mixed_columns


def _profile_column(
    *,
    index: int,
    name: str,
    cells: list[_SampleCell],
) -> DatasetColumnProfile:
    non_missing = [cell for cell in cells if cell.value.casefold() not in _MISSING_VALUES]
    missing_count = len(cells) - len(non_missing)
    unique_by_hash: dict[str, _SampleCell] = {}
    kinds: Counter[str] = Counter()
    numeric_values: list[float] = []
    date_count = 0
    for cell in non_missing:
        unique_by_hash.setdefault(cell.fingerprint, cell)
        kind, numeric_value = _classify_value(cell.value)
        kinds[kind] += 1
        if numeric_value is not None:
            numeric_values.append(numeric_value)
        if kind == "datetime":
            date_count += 1

    inferred_type, mixed_type = _inferred_type(kinds, len(unique_by_hash))
    numeric_range = (
        DatasetNumericRange(minimum=min(numeric_values), maximum=max(numeric_values))
        if numeric_values and not mixed_type
        else None
    )
    low_cardinality = _low_cardinality(inferred_type, unique_by_hash)
    non_missing_count = len(non_missing)
    unique_ratio = len(unique_by_hash) / non_missing_count if non_missing_count else 0.0
    date_ratio = date_count / non_missing_count if non_missing_count else 0.0
    potential_date = bool(
        non_missing_count
        and (date_ratio >= 0.8 or (_DATE_NAME_PATTERN.search(name) and date_ratio >= 0.5))
    )
    potential_id = bool(
        non_missing_count >= 2
        and unique_ratio >= 0.98
        and (_ID_NAME_PATTERN.search(name) is not None or inferred_type == "string")
    )
    return DatasetColumnProfile(
        index=index,
        name=name,
        inferred_type=inferred_type,
        missing_count=missing_count,
        unique_count=len(unique_by_hash),
        numeric_range=numeric_range,
        low_cardinality=low_cardinality,
        potential_date=potential_date,
        potential_id=potential_id,
        mixed_type=mixed_type,
    )


def _classify_value(value: str) -> tuple[str, float | None]:
    folded = value.casefold()
    if folded in _BOOLEAN_VALUES:
        return "boolean", None
    if _INTEGER_PATTERN.fullmatch(value):
        try:
            return "integer", float(int(value))
        except OverflowError:
            return "string", None
    if _NUMBER_PATTERN.fullmatch(value):
        parsed = float(value)
        if math.isfinite(parsed):
            return "number", parsed
    if _DATE_PREFIX_PATTERN.match(value) and _is_iso_datetime(value):
        return "datetime", None
    return "string", None


def _is_iso_datetime(value: str) -> bool:
    normalized = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return True


def _inferred_type(
    kinds: Counter[str], unique_count: int
) -> tuple[DatasetColumnInferredType, bool]:
    if not kinds:
        return "empty", False
    present = set(kinds)
    if present <= {"integer"}:
        return "integer", False
    if present <= {"integer", "number"}:
        return "number", False
    if len(present) == 1:
        only = next(iter(present))
        if only == "string" and unique_count <= _LOW_CARDINALITY_LIMIT:
            return "categorical", False
        if only == "boolean":
            return "boolean", False
        if only == "datetime":
            return "datetime", False
        if only == "string":
            return "string", False
        return "mixed", True
    return "mixed", True


def _low_cardinality(
    inferred_type: DatasetColumnInferredType,
    unique_by_hash: dict[str, _SampleCell],
) -> DatasetLowCardinalitySummary | None:
    if inferred_type != "categorical":
        return None
    ordered = sorted(
        ((cell.display, fingerprint) for fingerprint, cell in unique_by_hash.items()),
        key=lambda item: (item[0].casefold(), item[1]),
    )
    values: list[str] = []
    seen: set[str] = set()
    for display, _fingerprint in ordered:
        if display in seen:
            continue
        seen.add(display)
        values.append(display)
        if len(values) == _LOW_CARDINALITY_LIMIT:
            break
    return DatasetLowCardinalitySummary(
        values=values,
        truncated=len(values) < len(unique_by_hash),
    )


def _build_warnings(
    *,
    encoding: str,
    parsed: _ParsedCsv,
    mixed_columns: list[str],
) -> list[DatasetInspectionWarning]:
    warnings: list[DatasetInspectionWarning] = []
    if encoding not in {"utf-8", "utf-8-sig"}:
        warnings.append(
            DatasetInspectionWarning(
                code="encoding-fallback",
                message=f"CSV required the deterministic {encoding} fallback decoder.",
                column_name=None,
            )
        )
    if parsed.duplicate_headers:
        warnings.append(
            DatasetInspectionWarning(
                code="duplicate-column-name",
                message=(
                    "Duplicate column names are preserved and profiled by zero-based column index."
                ),
                column_name=None,
            )
        )
    if parsed.malformed_rows:
        warnings.append(
            DatasetInspectionWarning(
                code="malformed-row",
                message=(
                    f"{parsed.malformed_rows} row(s) were malformed or did not match the "
                    "header width; missing fields were padded and extra fields were ignored."
                ),
                column_name=None,
            )
        )
    if parsed.rows_read > len(parsed.sampled_rows):
        warnings.append(
            DatasetInspectionWarning(
                code="sample-limited",
                message=(
                    "Column missing, unique, type, range, cardinality, date, and ID metrics "
                    f"use a deterministic {len(parsed.sampled_rows)}-row sample; row_count "
                    "is the full streamed count."
                ),
                column_name=None,
            )
        )
    for name in mixed_columns[:_MAX_MIXED_TYPE_WARNINGS]:
        warnings.append(
            DatasetInspectionWarning(
                code="mixed-column-type",
                message="The sampled non-missing values contain incompatible data types.",
                column_name=name,
            )
        )
    if len(mixed_columns) > _MAX_MIXED_TYPE_WARNINGS:
        warnings.append(
            DatasetInspectionWarning(
                code="mixed-column-type",
                message=(
                    f"{len(mixed_columns) - _MAX_MIXED_TYPE_WARNINGS} additional columns "
                    "contain mixed sampled types."
                ),
                column_name=None,
            )
        )
    if parsed.header_display_changed or parsed.sample_display_changed:
        warnings.append(
            DatasetInspectionWarning(
                code="other",
                message=(
                    "Control characters, formula-like prefixes, or overlong display values "
                    "were escaped only in the bounded profile; the source file was unchanged."
                ),
                column_name=None,
            )
        )
    if parsed.sample_value_truncated:
        warnings.append(
            DatasetInspectionWarning(
                code="other",
                message=(
                    "At least one sampled cell exceeded the bounded type-inspection length; "
                    "its full value was not retained in the profile."
                ),
                column_name=None,
            )
        )
    return warnings
