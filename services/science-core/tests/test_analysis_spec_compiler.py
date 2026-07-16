from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from open_science_core.analysis_spec.compiler import (
    COMPILER_VERSION,
    EXPECTED_OUTPUTS,
    RUNTIME_POLICY_ID,
    compile_analysis_spec,
)
from open_science_core.analysis_spec.results import StructuredAnalysisResult
from open_science_core.analysis_spec.schemas import (
    AnalysisSpec,
    CorrelationOperation,
    DescriptiveOperation,
    TwoGroupComparisonOperation,
)
from open_science_core.analysis_spec.validator import ResolvedMethod, ValidatedAnalysisSpec

SOURCE_ID = "dataset-1"
CONTENT_HASH = "a" * 64
PROFILE_HASH = "b" * 64


def _spec(operation: object, *, objective: str = "Analyze the selected data.") -> AnalysisSpec:
    return AnalysisSpec.model_validate(
        {
            "schemaVersion": "1",
            "objective": objective,
            "datasetSourceId": SOURCE_ID,
            "datasetContentHash": CONTENT_HASH,
            "datasetProfileHash": PROFILE_HASH,
            "operation": operation,
            "missingValuePolicy": "drop-per-operation",
            "confidenceLevel": 0.95,
            "randomSeed": 1234,
            "assumptions": ["Rows are independent."],
            "limitations": ["The v1 method rule is intentionally bounded."],
        }
    )


def _validated(spec: AnalysisSpec, resolved_method: str) -> ValidatedAnalysisSpec:
    operation = spec.operation
    if isinstance(operation, DescriptiveOperation):
        columns = tuple(operation.columns)
    elif isinstance(operation, TwoGroupComparisonOperation):
        columns = (operation.outcome_column, operation.group_column)
    else:
        columns = (operation.x_column, operation.y_column)
    return ValidatedAnalysisSpec(
        spec=spec,
        resolved_method=cast(ResolvedMethod, resolved_method),
        method_selection_reason="A deterministic v1 rule selected this method.",
        referenced_columns=columns,
        dataset_delimiter=",",
        dataset_encoding="utf-8",
    )


def _execute_compiled(
    tmp_path: Path,
    validated: ValidatedAnalysisSpec,
) -> StructuredAnalysisResult:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    compiled = compile_analysis_spec(validated)
    exec(  # noqa: S102 - executes only the deterministic compiler output under test.
        compile(compiled.code, "<compiled-analysis-test>", "exec"),
        {"DATASET_PATH": tmp_path / "dataset.csv", "RUN_DIR": run_dir},
    )
    assert (run_dir / "analysis-spec.json").is_file()
    assert (run_dir / "summary.csv").is_file()
    return StructuredAnalysisResult.model_validate_json(
        (run_dir / "results.json").read_text(encoding="utf-8")
    )


def test_compiler_is_deterministic_and_binds_every_identity() -> None:
    spec = _spec(
        CorrelationOperation(
            type="correlation",
            x_column="sleep_hours",
            y_column="cognitive_score",
            method="auto",
            confidence_interval=True,
            plot="scatter",
        )
    )
    validated = _validated(spec, "pearson")

    first = compile_analysis_spec(validated)
    second = compile_analysis_spec(validated)

    assert first == second
    assert first.compiler_version == COMPILER_VERSION
    assert first.runtime_policy_id == RUNTIME_POLICY_ID
    assert first.expected_outputs == [*EXPECTED_OUTPUTS, "figure.png"]
    assert ast.parse(first.code)
    assert "analysis-spec.json" in first.code
    assert "results.json" in first.code
    assert "summary.csv" in first.code


def test_spec_or_method_change_changes_code_hash() -> None:
    base_spec = _spec(
        CorrelationOperation(
            type="correlation",
            x_column="x",
            y_column="y",
            method="auto",
            confidence_interval=True,
            plot="scatter",
        )
    )
    pearson = compile_analysis_spec(_validated(base_spec, "pearson"))
    spearman = compile_analysis_spec(_validated(base_spec, "spearman"))
    changed_spec = base_spec.model_copy(update={"random_seed": 42})
    changed = compile_analysis_spec(_validated(changed_spec, "pearson"))

    assert pearson.code_sha256 != spearman.code_sha256
    assert pearson.spec_sha256 == spearman.spec_sha256
    assert pearson.code_sha256 != changed.code_sha256
    assert pearson.spec_sha256 != changed.spec_sha256


def test_special_column_and_goal_are_only_embedded_as_json_data() -> None:
    hostile_column = "score']; __import__('os').system('touch /tmp/pwned'); #"
    hostile_goal = "Analyze it\n__import__('socket').socket()"
    spec = _spec(
        DescriptiveOperation(
            type="descriptive",
            columns=[hostile_column],
            statistics=["count", "missing"],
            plot="none",
        ),
        objective=hostile_goal,
    )

    compiled = compile_analysis_spec(_validated(spec, "descriptive"))
    tree = ast.parse(compiled.code)
    imports = {
        alias.name.split(".", maxsplit=1)[0]
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".", maxsplit=1)[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
    }
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]

    assert imports == {"json", "math", "matplotlib", "numpy", "pandas", "scipy"}
    assert not any(
        isinstance(call.func, ast.Name)
        and call.func.id in {"__import__", "compile", "eval", "exec"}
        for call in calls
    )
    spec_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "SPEC_JSON"
    )
    assert isinstance(spec_assignment.value, ast.Constant)
    assert isinstance(spec_assignment.value.value, str)
    embedded_spec = json.loads(spec_assignment.value.value)
    assert embedded_spec["operation"]["columns"] == [hostile_column]
    assert embedded_spec["objective"] == hostile_goal


def test_compiler_rejects_unknown_version() -> None:
    spec = _spec(
        DescriptiveOperation(
            type="descriptive",
            columns=["score"],
            statistics=["count"],
            plot="none",
        )
    )

    with pytest.raises(ValueError, match="unsupported compiler version"):
        compile_analysis_spec(
            _validated(spec, "descriptive"),
            compiler_version="analysis-spec-compiler-v2",
        )


def test_compiler_rejects_tampered_code_hash_via_domain_contract() -> None:
    spec = _spec(
        TwoGroupComparisonOperation(
            type="two-group-comparison",
            outcome_column="score",
            group_column="group",
            groups=("treatment", "control"),
            method="welch-t-test",
            effect_size="hedges-g",
            check_assumptions=True,
            plot="boxplot",
        )
    )
    compiled = compile_analysis_spec(_validated(spec, "welch-t-test"))

    with pytest.raises(ValueError, match="code_sha256"):
        compiled.__class__.model_validate(
            {**compiled.model_dump(), "code": compiled.code + "\nprint('tampered')"}
        )


def test_compiled_descriptive_executes_semicolon_cp1252_and_validates_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MPLBACKEND", "Agg")
    (tmp_path / "dataset.csv").write_bytes(
        "label;score\ncafé;1\nthé;na\nrose;3\n".encode("cp1252")
    )
    spec = _spec(
        DescriptiveOperation(
            type="descriptive",
            columns=["score"],
            statistics=["count", "missing", "mean", "median"],
            plot="none",
        )
    )
    validated = replace(
        _validated(spec, "descriptive"),
        dataset_delimiter=";",
        dataset_encoding="cp1252",
    )

    result = _execute_compiled(tmp_path, validated)

    assert result.operation_type == "descriptive"
    assert result.sample_summary.total_rows == 3
    assert result.sample_summary.analyzed_rows == 2
    assert result.sample_summary.missing_rows == 1
    assert result.result.type == "descriptive"
    assert result.result.columns[0].statistics == {
        "count": 2,
        "mean": 2.0,
        "median": 2.0,
        "missing": 1,
    }


def test_compiled_perfect_pearson_uses_pearson_degenerate_interval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MPLBACKEND", "Agg")
    (tmp_path / "dataset.csv").write_text(
        "x,y\n1,2\n2,4\n3,6\n4,8\n5,10\n",
        encoding="utf-8",
    )
    spec = _spec(
        CorrelationOperation(
            type="correlation",
            x_column="x",
            y_column="y",
            method="pearson",
            confidence_interval=True,
            plot="none",
        )
    )

    result = _execute_compiled(tmp_path, _validated(spec, "pearson"))

    assert result.result.type == "correlation"
    assert result.result.correlation == pytest.approx(1.0)
    assert result.result.confidence_interval == pytest.approx((1.0, 1.0))
    assert any("degenerate" in warning for warning in result.warnings)
