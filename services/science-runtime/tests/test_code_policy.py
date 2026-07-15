from __future__ import annotations

import hashlib
import re
from typing import Any, cast

import pytest

from open_science_runtime.code_policy import CodePolicyError, validate_python_code
from open_science_runtime.fixed_analysis_policy import (
    FIXED_ANALYSIS_POLICY_ID,
    FixedAnalysisPolicyError,
    FixedAnalysisTemplate,
    fixed_analysis_source,
    fixed_analysis_template_for_repair_attempt,
)

_V1_TEMPLATE_SOURCE_SHA256: dict[FixedAnalysisTemplate, str] = {
    # These digests are the immutable dataset-analysis-fixed-v1 contract.
    # Source changes require a new policy ID instead of updating these values.
    "baseline": "8d3e24189110e8286f287b1873ace80e3ffce7c9b3958acfe1a9eb9d4573ba7e",
    "repair-1": "97ac3bc4dc8038857065a11e919eecdb058c457aa34d5aab272892a7b2d1e736",
    "repair-2": "e2a39274eb7fed95bfb4df7ad6676916306fc7707f2c57fc31ea0adb871d226e",
}


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("import subprocess", "importing subprocess is not allowed"),
        ("from socket import socket", "importing socket is not allowed"),
        ("import pty as terminal", "importing pty is not allowed"),
        (
            "import os as operating_system\noperating_system.system('id')",
            "calling os.system is not allowed",
        ),
        (
            "import os\nrunner = os.popen\nrunner('id')",
            "referencing os.popen is not allowed",
        ),
        (
            "import importlib as loader\nloader.import_module('subprocess')",
            "dynamic import of subprocess is not allowed",
        ),
        (
            "dynamic_import = __import__\ndynamic_import('socket')",
            "dynamic import of socket is not allowed",
        ),
        (
            "module_name = 'sub' + 'process'\n__import__(module_name)",
            "computed dynamic imports are not allowed",
        ),
        (
            "import importlib as loader\nmodule_name = 'socket'\nloader.import_module(module_name)",
            "computed dynamic imports are not allowed",
        ),
        (
            "from pathlib import Path\nPath('/etc/passwd').read_text()",
            "absolute and parent-relative path literals are not allowed",
        ),
        (
            "from pathlib import Path\nPath('../other-run/input.csv').read_text()",
            "absolute and parent-relative path literals are not allowed",
        ),
        (
            "exec(\"print('policy bypass')\")",
            "calling exec is not allowed",
        ),
        (
            "get_ipython().run_line_magic('system', 'id')",
            "IPython %system magic is not allowed",
        ),
        ("get_ipython().system('id')", "get_ipython().system is not allowed"),
        ("run_process(['id'], shell=True)", "shell=True is not allowed"),
    ],
    ids=[
        "subprocess-import",
        "socket-from-import",
        "pty-alias",
        "os-system-alias",
        "os-popen-callable-alias",
        "importlib-dynamic-import",
        "builtin-dynamic-import-alias",
        "computed-builtin-dynamic-import",
        "computed-importlib-dynamic-import",
        "absolute-file-read",
        "parent-relative-file-read",
        "dynamic-code-execution",
        "ipython-shell-magic",
        "ipython-system",
        "shell-true",
    ],
)
def test_rejects_process_and_shell_escape_forms(code: str, message: str) -> None:
    with pytest.raises(CodePolicyError, match=re.escape(message)) as caught:
        validate_python_code(code)

    assert str(caught.value).startswith("line ")


def test_allows_normal_analysis_imports_and_filesystem_helpers() -> None:
    validate_python_code(
        "\n".join(
            (
                "import os",
                "from pathlib import Path",
                "import pandas as pd",
                "dataset = pd.read_csv(Path(DATASET_PATH))",
                "dataset.to_csv(RUN_DIR / 'summary.csv')",
                "print(os.path.basename(str(dataset_path)))",
            )
        )
    )


def test_syntax_error_reports_only_a_stable_location_and_reason() -> None:
    with pytest.raises(CodePolicyError) as caught:
        validate_python_code("sensitive_value = (\n")

    assert str(caught.value) == "line 1: code must be valid Python syntax"
    assert "sensitive_value" not in str(caught.value)


@pytest.mark.parametrize(
    ("template", "selected_column_index"),
    [("baseline", 0), ("baseline", 19), ("repair-1", 0), ("repair-2", 0)],
)
def test_fixed_analysis_policy_accepts_only_canonical_template_asts(
    template: FixedAnalysisTemplate,
    selected_column_index: int,
) -> None:
    code = fixed_analysis_source(
        template,
        selected_column_index=selected_column_index,
    )

    validate_python_code(
        f"# approved fixed template\n{code}\n",
        policy_profile_id=FIXED_ANALYSIS_POLICY_ID,
        policy_template=template,
    )


@pytest.mark.parametrize("template", ["baseline", "repair-1", "repair-2"])
def test_fixed_analysis_v1_template_source_is_frozen(
    template: FixedAnalysisTemplate,
) -> None:
    source = fixed_analysis_source(template)

    assert (
        hashlib.sha256(source.encode("utf-8")).hexdigest() == (_V1_TEMPLATE_SOURCE_SHA256[template])
    )


@pytest.mark.parametrize("invalid_value", [False, True, 1.0, "1"])
def test_fixed_analysis_policy_rejects_non_integer_contract_values(
    invalid_value: object,
) -> None:
    with pytest.raises(FixedAnalysisPolicyError):
        fixed_analysis_source(
            "baseline",
            selected_column_index=cast(Any, invalid_value),
        )
    with pytest.raises(FixedAnalysisPolicyError):
        fixed_analysis_template_for_repair_attempt(cast(Any, invalid_value))


@pytest.mark.parametrize(
    "mutation",
    [
        "\nrunner = eval\nrunner('1 + 1')",
        "\n__builtins__['__import__']('os').system('id')",
        "\npd.io.common.os.system('id')",
        "\nobject.__subclasses__()",
        "\nopen(chr(47) + 'etc/passwd').read()",
        "\nRUN_DIR.parent.joinpath('other').read_text()",
        "\nget_ipython().run_line_magic('sys' + 'tem', 'id')",
    ],
)
def test_fixed_analysis_policy_rejects_every_non_template_statement(
    mutation: str,
) -> None:
    code = fixed_analysis_source("baseline") + mutation

    with pytest.raises(CodePolicyError, match="dataset-analysis-fixed-v1/baseline"):
        validate_python_code(
            code,
            policy_profile_id=FIXED_ANALYSIS_POLICY_ID,
            policy_template="baseline",
        )
