#!/usr/bin/env python3
"""Derive the Core-ready annual GISTEMP input from the bundled NASA source."""

from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data" / "gistemp_global_means.csv"
DEFAULT_OUTPUT = ROOT / "data" / "gistemp_annual_global_means.csv"


def annual_rows(source: Path) -> list[tuple[int, str]]:
    """Return complete annual means, omitting NASA's incomplete current year."""
    with source.open(newline="", encoding="utf-8") as handle:
        title = handle.readline().strip()
        if title != "Land-Ocean: Global Means":
            raise ValueError(f"unexpected GISTEMP title: {title!r}")
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not {"Year", "J-D"}.issubset(reader.fieldnames):
            raise ValueError("GISTEMP source must provide Year and J-D columns")
        rows: list[tuple[int, str]] = []
        for row in reader:
            year = row["Year"].strip()
            annual_mean = row["J-D"].strip()
            if annual_mean == "***":
                continue
            float(annual_mean)
            rows.append((int(year), annual_mean))
    if not rows or any(later <= earlier for (earlier, _), (later, _) in zip(rows, rows[1:])):
        raise ValueError("GISTEMP annual rows must contain strictly increasing years")
    return rows


def render(rows: list[tuple[int, str]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("Year", "J-D"))
    writer.writerows(rows)
    return output.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="fail if the output is stale")
    args = parser.parse_args()

    expected = render(annual_rows(args.input))
    actual = args.output.read_text(encoding="utf-8") if args.output.exists() else None
    if args.check:
        if actual != expected:
            raise SystemExit(f"{args.output} is missing or stale; rerun this script without --check")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()
