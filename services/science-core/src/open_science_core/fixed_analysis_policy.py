from __future__ import annotations

import ast
from typing import Literal, TypeAlias

AnalysisPolicyId = Literal[
    "approved-python-container-v1",
    "dataset-analysis-fixed-v1",
    "dataset-analysis-spec-v1",
]
GENERAL_ANALYSIS_POLICY_ID: AnalysisPolicyId = "approved-python-container-v1"
FIXED_ANALYSIS_POLICY_ID: AnalysisPolicyId = "dataset-analysis-fixed-v1"
COMPILED_ANALYSIS_POLICY_ID: AnalysisPolicyId = "dataset-analysis-spec-v1"
FixedAnalysisTemplate = Literal["baseline", "repair-1", "repair-2"]
CompiledAnalysisTemplate = Literal["analysis-spec-compiler-v1"]
AnalysisPolicyTemplate: TypeAlias = FixedAnalysisTemplate | CompiledAnalysisTemplate
COMPILED_ANALYSIS_TEMPLATE: CompiledAnalysisTemplate = "analysis-spec-compiler-v1"


class FixedAnalysisPolicyError(ValueError):
    """Raised when code is not one of the versioned deterministic templates."""


def fixed_analysis_template_for_repair_attempt(
    repair_attempt: int,
) -> FixedAnalysisTemplate:
    if type(repair_attempt) is not int:
        raise FixedAnalysisPolicyError("repair attempt is outside the fixed policy contract")
    if repair_attempt == 0:
        return "baseline"
    if repair_attempt == 1:
        return "repair-1"
    if repair_attempt == 2:
        return "repair-2"
    raise FixedAnalysisPolicyError("repair attempt is outside the fixed policy contract")


def fixed_analysis_source(
    template: FixedAnalysisTemplate,
    *,
    selected_column_index: int = 0,
) -> str:
    """Return the canonical source for one versioned deterministic template."""

    if type(selected_column_index) is not int or not 0 <= selected_column_index <= 1_000_000:
        raise FixedAnalysisPolicyError("selected column index is outside the policy contract")
    if template == "baseline":
        return _baseline_source(selected_column_index)
    if template == "repair-1":
        if selected_column_index != 0:
            raise FixedAnalysisPolicyError("repair templates do not accept a column index")
        return _repair_one_source()
    if template == "repair-2":
        if selected_column_index != 0:
            raise FixedAnalysisPolicyError("repair templates do not accept a column index")
        return _repair_two_source()
    raise FixedAnalysisPolicyError("unknown fixed analysis template")


def validate_fixed_analysis_code(
    code: str,
    *,
    template: FixedAnalysisTemplate,
) -> None:
    """Require AST equivalence with one exact, versioned deterministic template."""

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as error:
        location = f"line {error.lineno}: " if error.lineno is not None else ""
        raise FixedAnalysisPolicyError(f"{location}code must be valid Python syntax") from error

    selected_column_index = 0
    if template == "baseline":
        selected_column_index = _selected_column_index(tree)
    expected = ast.parse(
        fixed_analysis_source(
            template,
            selected_column_index=selected_column_index,
        ),
        mode="exec",
    )
    if ast.dump(tree, include_attributes=False) != ast.dump(
        expected,
        include_attributes=False,
    ):
        raise FixedAnalysisPolicyError(
            f"code does not match the {FIXED_ANALYSIS_POLICY_ID}/{template} contract"
        )


def _selected_column_index(tree: ast.Module) -> int:
    matches = [
        statement.value
        for statement in tree.body
        if isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
        and statement.targets[0].id == "selected_column_index"
    ]
    if len(matches) != 1:
        raise FixedAnalysisPolicyError("baseline selected column binding is missing")
    value = matches[0]
    if (
        not isinstance(value, ast.Constant)
        or type(value.value) is not int
        or not 0 <= value.value <= 1_000_000
    ):
        raise FixedAnalysisPolicyError("baseline selected column index is invalid")
    return value.value


def _safe_csv_export_source() -> tuple[str, ...]:
    return (
        "def _escape_csv_cell(value):",
        "    if not isinstance(value, str):",
        "        return value",
        "    cleaned = ''.join(",
        "        character if character.isprintable() else ' ' for character in value",
        "    )",
        "    if cleaned.lstrip().startswith(('=', '+', '-', '@')):",
        '        return "\'" + cleaned',
        "    return cleaned",
        "",
        "export_summary = summary.copy()",
        "export_summary.index = [",
        "    _escape_csv_cell(str(value)) for value in export_summary.index",
        "]",
        "export_summary.columns = [",
        "    _escape_csv_cell(str(value)) for value in export_summary.columns",
        "]",
        "export_summary = export_summary.map(_escape_csv_cell)",
    )


def _baseline_source(selected_column_index: int) -> str:
    return "\n".join(
        (
            "import matplotlib.pyplot as plt",
            "import pandas as pd",
            "",
            "data = pd.read_csv(DATASET_PATH)",
            "summary = data.describe(include='all').transpose()",
            *_safe_csv_export_source(),
            "export_summary.to_csv(RUN_DIR / 'summary.csv')",
            f"selected_column_index = {selected_column_index}",
            "selected_series = data.iloc[:, selected_column_index]",
            "figure, axis = plt.subplots(figsize=(8, 5))",
            "if pd.api.types.is_numeric_dtype(selected_series):",
            "    selected_series.dropna().plot(kind='hist', bins=20, ax=axis)",
            "else:",
            "    selected_series.astype('string').value_counts(dropna=False).head(20).plot(",
            "        kind='bar', ax=axis",
            "    )",
            "axis.set_title('Distribution of selected column')",
            "axis.set_xlabel('Selected column')",
            "figure.tight_layout()",
            "figure.savefig(RUN_DIR / 'figure.png', dpi=150)",
            "plt.close(figure)",
            "print(export_summary.to_string())",
        )
    )


def _repair_one_source() -> str:
    return "\n".join(
        (
            "import matplotlib.pyplot as plt",
            "import pandas as pd",
            "",
            "data = pd.read_csv(DATASET_PATH)",
            "summary = pd.DataFrame(",
            "    {'metric': ['row_count', 'column_count'],",
            "     'value': [len(data.index), len(data.columns)]}",
            ")",
            "summary.to_csv(RUN_DIR / 'summary.csv', index=False)",
            "selected_series = data.iloc[:, 0]",
            "counts = selected_series.astype('string').fillna('<missing>').value_counts().head(20)",
            "figure, axis = plt.subplots(figsize=(8, 5))",
            "if counts.empty:",
            "    axis.text(0.5, 0.5, 'No values', ha='center', va='center')",
            "else:",
            "    counts.plot(kind='bar', ax=axis)",
            "axis.set_title('Values of selected column')",
            "figure.tight_layout()",
            "figure.savefig(RUN_DIR / 'figure.png', dpi=150)",
            "plt.close(figure)",
            "print(summary.to_string(index=False))",
        )
    )


def _repair_two_source() -> str:
    return "\n".join(
        (
            "import matplotlib.pyplot as plt",
            "import pandas as pd",
            "",
            "data = pd.read_csv(DATASET_PATH)",
            "summary = data.isna().sum().rename('missing_count').reset_index()",
            "summary.columns = ['column', 'missing_count']",
            *_safe_csv_export_source(),
            "export_summary.to_csv(RUN_DIR / 'summary.csv', index=False)",
            "figure, axis = plt.subplots(figsize=(8, 5))",
            "axis.axis('off')",
            "axis.text(",
            "    0.5, 0.5, f'Rows: {len(data.index)}\\nColumns: {len(data.columns)}',",
            "    ha='center', va='center'",
            ")",
            "figure.tight_layout()",
            "figure.savefig(RUN_DIR / 'figure.png', dpi=150)",
            "plt.close(figure)",
            "print(export_summary.to_string(index=False))",
        )
    )
