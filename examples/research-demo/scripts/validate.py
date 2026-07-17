#!/usr/bin/env python3
"""Focused validation for the synthetic Spark research demo."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "scripts" / "analysis.py"
RAW_DATA = ROOT / "data" / "raw" / "synthetic_observations.csv"
PROJECT = ROOT / ".spark" / "project.json"
FIGURE = ROOT / "figures" / "synthetic_condition_trends.png"
SUMMARY = ROOT / "reports" / "summary.csv"
REPORT = ROOT / "reports" / "report.md"
BIB = ROOT / "references" / "references.bib"
NOTEBOOK = ROOT / "notebooks" / "analysis.ipynb"
DEMO = ROOT / "DEMO.md"


EXPECTED_OUTPUTS = [FIGURE, SUMMARY, REPORT, BIB, NOTEBOOK]


def fail(message: str) -> None:
    raise SystemExit(message)


def run_analysis() -> None:
    subprocess.run([sys.executable, str(ANALYSIS)], cwd=ROOT, check=True)


def read_summary(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def computed_summary(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    grouped: dict[str, list[tuple[int, float]]] = {}
    for row in rows:
        grouped.setdefault(row["condition"], []).append((int(row["day"]), float(row["value"])))
    expected: list[dict[str, str]] = []
    for condition, points in grouped.items():
        days = [day for day, _ in points]
        values = [value for _, value in points]
        n = len(points)
        mean_day = sum(days) / n
        mean_value = sum(values) / n
        numerator = sum((day - mean_day) * (value - mean_value) for day, value in points)
        denominator = sum((day - mean_day) ** 2 for day in days)
        slope = 0.0 if denominator == 0 else numerator / denominator
        expected.append(
            {
                "condition": condition,
                "n": str(n),
                "mean_value": f"{mean_value:.3f}",
                "min_value": f"{min(values):.3f}",
                "max_value": f"{max(values):.3f}",
                "slope_per_day": f"{slope:.3f}",
            }
        )
    return expected


def expect(condition: bool, message: str) -> None:
    if not condition:
        fail(message)


def validate_structure() -> None:
    required = [PROJECT, RAW_DATA, ANALYSIS, DEMO]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    expect(not missing, f"Missing required project files: {missing}")


def validate_outputs() -> None:
    for output in EXPECTED_OUTPUTS:
        expect(output.exists(), f"Missing expected output: {output.relative_to(ROOT)}")
        expect(output.stat().st_size > 0, f"Empty output: {output.relative_to(ROOT)}")

    expect(
        FIGURE.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"),
        "Figure is not a valid PNG file",
    )

    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    expect(notebook["nbformat"] == 4, "Notebook nbformat must be 4")
    expect(len(notebook["cells"]) == 2, "Notebook should contain two cells")
    expect(
        any("Synthetic Research Demo" in "".join(cell.get("source", [])) for cell in notebook["cells"]),
        "Notebook must explicitly describe the synthetic demo",
    )

    bibliography = BIB.read_text(encoding="utf-8")
    expect("@comment" in bibliography, "BibTeX placeholder must explain that it is intentionally empty")
    expect("@article" not in bibliography, "BibTeX placeholder must not invent references")

    report = REPORT.read_text(encoding="utf-8")
    expect("synthetic" in report.lower(), "Report must explicitly say the project is synthetic")
    expect("no real literature identifiers" in report.lower(), "Report must explain the empty bibliography")


def validate_summary() -> None:
    observed = read_summary(SUMMARY)
    expected = computed_summary(RAW_DATA)
    expect(observed == expected, f"Summary CSV does not match raw data: {observed!r} != {expected!r}")


def validate_discoverability() -> None:
    discoverable = {
        "png",
        "csv",
        "md",
        "ipynb",
    }
    for path in [FIGURE, SUMMARY, REPORT, NOTEBOOK]:
        expect(
            path.suffix.lstrip(".").lower() in discoverable,
            f"Output is not discoverable by extension registry: {path.relative_to(ROOT)}",
        )
    with PROJECT.open(encoding="utf-8") as handle:
        project = json.load(handle)
    artifacts = set(project.get("artifacts", []))
    expected = {
        "figures/synthetic_condition_trends.png",
        "reports/summary.csv",
        "reports/report.md",
        "references/references.bib",
        "notebooks/analysis.ipynb",
    }
    expect(expected.issubset(artifacts), "project.json must list every demo artifact")


def main() -> int:
    run_analysis()
    validate_structure()
    validate_outputs()
    validate_summary()
    validate_discoverability()
    print("research demo validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
