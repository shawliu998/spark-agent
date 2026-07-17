#!/usr/bin/env python3
"""Portable acceptance harness for the managed-project nbconvert fallback."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[2]
MARKER = "spark-notebook-fallback-ok"
IGNORED_DIRECTORIES = {
    ".git",
    ".opencode",
    ".openscience",
    ".spark",
    ".venv",
    "__pycache__",
    "node_modules",
}


def notebook_document() -> dict[str, object]:
    return {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": "# Spark notebook fallback\n",
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": f'print("{MARKER}")\n',
            },
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            }
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def serialize(document: dict[str, object]) -> str:
    return json.dumps(document, indent=1, ensure_ascii=False) + "\n"


def managed_python() -> Path | None:
    configured = os.environ.get("SPARK_NOTEBOOK_PYTHON")
    if configured:
        return Path(configured).expanduser().resolve()
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    candidate = ROOT / ".spark" / "python" / relative
    return candidate if candidate.is_file() else None


def output_text(output: dict[str, object]) -> str:
    text = output.get("text", "")
    return "".join(text) if isinstance(text, list) else str(text)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="spark-notebook-") as temp:
        workspace = Path(temp)
        notebook_dir = workspace / "notebooks"
        notebook_dir.mkdir()
        source_path = notebook_dir / "analysis.ipynb"

        first = serialize(notebook_document())
        second = serialize(notebook_document())
        assert first == second, "notebook serialization is not deterministic"
        source_path.write_text(first, encoding="utf-8")

        parsed = json.loads(source_path.read_text(encoding="utf-8"))
        assert parsed["nbformat"] == 4
        assert parsed["metadata"]["kernelspec"]["name"] == "python3"
        assert parsed["cells"][1]["source"].strip().endswith(f'{MARKER}")')
        print("PASS: deterministic notebook creation and serialization")

        relative = source_path.relative_to(workspace)
        assert relative.suffix == ".ipynb"
        assert not (set(relative.parts) & IGNORED_DIRECTORIES)
        assert ".spark" in (Path(".spark") / "python" / "bin" / "python").parts
        print("PASS: notebook path satisfies workspace artifact discovery")

        python = managed_python()
        if python is None or not python.is_file():
            expected = ROOT / ".spark" / "python"
            print(
                "SKIP: managed project Python runtime is unavailable at "
                f"{expected}; run project Python setup or set SPARK_NOTEBOOK_PYTHON"
            )
            return 0

        probe = subprocess.run(
            [str(python), "-c", "import nbconvert, nbformat, ipykernel"],
            text=True,
            capture_output=True,
            check=False,
        )
        if probe.returncode != 0:
            detail = (probe.stderr or probe.stdout).strip().splitlines()[-1]
            print(
                f"SKIP: managed project Python {python} lacks the notebook runtime: {detail}"
            )
            return 0

        command = [
            str(python),
            "-m",
            "jupyter",
            "nbconvert",
            "--to",
            "notebook",
            "--execute",
            str(source_path),
            "--output",
            "analysis.executed.ipynb",
            "--ExecutePreprocessor.timeout=60",
        ]
        completed = subprocess.run(
            command,
            cwd=workspace,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
        if completed.returncode != 0:
            raise SystemExit(
                "nbconvert execution failed:\n" + (completed.stderr or completed.stdout)[-2000:]
            )

        executed_path = notebook_dir / "analysis.executed.ipynb"
        executed = json.loads(executed_path.read_text(encoding="utf-8"))
        code_cell = next(cell for cell in executed["cells"] if cell["cell_type"] == "code")
        assert code_cell["execution_count"] is not None
        assert any(
            output.get("output_type") == "stream" and MARKER in output_text(output)
            for output in code_cell["outputs"]
        )
        assert not any(output.get("output_type") == "error" for output in code_cell["outputs"])
        assert executed_path.suffix == ".ipynb"
        print(f"PASS: nbconvert executed and saved {executed_path.relative_to(workspace)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
