#!/usr/bin/env python3
"""Deterministic synthetic research analysis for the Spark Agent demo."""

from __future__ import annotations

import argparse
import csv
import json
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
RAW_DATA = ROOT / "data" / "raw" / "synthetic_observations.csv"
FIGURE_PATH = ROOT / "figures" / "synthetic_condition_trends.png"
SUMMARY_PATH = ROOT / "reports" / "summary.csv"
REPORT_PATH = ROOT / "reports" / "report.md"
BIB_PATH = ROOT / "references" / "references.bib"
NOTEBOOK_PATH = ROOT / "notebooks" / "analysis.ipynb"


@dataclass(frozen=True)
class Observation:
    condition: str
    day: int
    value: float


@dataclass(frozen=True)
class SummaryRow:
    condition: str
    n: int
    mean_value: float
    min_value: float
    max_value: float
    slope_per_day: float


WIDTH = 960
HEIGHT = 560
WHITE = (255, 255, 255, 255)
BLACK = (34, 40, 49, 255)
GRID = (226, 230, 235, 255)
AXIS = (95, 106, 119, 255)
BADGE_TEXT = (255, 255, 255, 255)
PALETTE = {
    "baseline": (15, 118, 110, 255),
    "increasing": (37, 99, 235, 255),
    "decreasing": (217, 119, 6, 255),
}


def make_canvas(width: int, height: int, color: tuple[int, int, int, int] = WHITE) -> bytearray:
    pixels = bytearray(width * height * 4)
    for index in range(0, len(pixels), 4):
        pixels[index : index + 4] = bytes(color)
    return pixels


def set_pixel(canvas: bytearray, width: int, height: int, x: int, y: int, color: tuple[int, int, int, int]) -> None:
    if x < 0 or y < 0 or x >= width or y >= height:
        return
    index = (y * width + x) * 4
    canvas[index : index + 4] = bytes(color)


def fill_rect(
    canvas: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    rect_width: int,
    rect_height: int,
    color: tuple[int, int, int, int],
) -> None:
    for yy in range(y, y + rect_height):
        for xx in range(x, x + rect_width):
            set_pixel(canvas, width, height, xx, yy, color)


def draw_line(
    canvas: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[int, int, int, int],
    thickness: int = 1,
) -> None:
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        half = thickness // 2
        fill_rect(canvas, width, height, x0 - half, y0 - half, thickness, thickness, color)
        if x0 == x1 and y0 == y1:
            break
        twice_err = 2 * err
        if twice_err >= dy:
            err += dy
            x0 += sx
        if twice_err <= dx:
            err += dx
            y0 += sy


def draw_ellipse(
    canvas: bytearray,
    width: int,
    height: int,
    center_x: int,
    center_y: int,
    radius: int,
    color: tuple[int, int, int, int],
) -> None:
    for yy in range(-radius, radius + 1):
        for xx in range(-radius, radius + 1):
            if xx * xx + yy * yy <= radius * radius:
                set_pixel(canvas, width, height, center_x + xx, center_y + yy, color)


SEGMENTS = {
    0: "abcedf",
    1: "bc",
    2: "abged",
    3: "abgcd",
    4: "fgbc",
    5: "afgcd",
    6: "afgecd",
    7: "abc",
    8: "abcdefg",
    9: "abcfgd",
}


def draw_digit(
    canvas: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    digit: int,
    color: tuple[int, int, int, int],
    scale: int = 2,
) -> None:
    segments = SEGMENTS[digit]
    thick = max(2, scale)
    w = 4 * scale
    h = 6 * scale
    segment_specs = {
        "a": (x + thick, y, w - 2 * thick, thick),
        "b": (x + w - thick, y + thick, thick, h // 2 - thick),
        "c": (x + w - thick, y + h // 2, thick, h // 2 - thick),
        "d": (x + thick, y + h - thick, w - 2 * thick, thick),
        "e": (x, y + h // 2, thick, h // 2 - thick),
        "f": (x, y + thick, thick, h // 2 - thick),
        "g": (x + thick, y + h // 2 - thick // 2, w - 2 * thick, thick),
    }
    for segment, (sx, sy, sw, sh) in segment_specs.items():
        if segment in segments:
            fill_rect(canvas, width, height, sx, sy, sw, sh, color)


def draw_number(
    canvas: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    number: int,
    color: tuple[int, int, int, int],
    scale: int = 2,
    spacing: int = 2,
) -> int:
    text = str(number)
    cursor = x
    digit_width = 4 * scale
    for char in text:
        draw_digit(canvas, width, height, cursor, y, int(char), color, scale=scale)
        cursor += digit_width + spacing
    return cursor - x - spacing if text else 0


def draw_badge(
    canvas: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    number: int,
    fill_color: tuple[int, int, int, int],
) -> None:
    badge_width = 24
    badge_height = 18
    fill_rect(canvas, width, height, x, y, badge_width, badge_height, fill_color)
    draw_number(canvas, width, height, x + 4, y + 2, number, BADGE_TEXT, scale=2, spacing=1)


def save_png(path: Path, canvas: bytearray, width: int, height: int) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = bytearray()
    row_width = width * 4
    for row in range(height):
        raw.append(0)
        start = row * row_width
        raw.extend(canvas[start : start + row_width])
    payload = zlib.compress(bytes(raw), level=9)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", payload)
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def read_observations(path: Path) -> list[Observation]:
    rows: list[Observation] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"condition", "day", "value"}
        if set(reader.fieldnames or ()) != required:
            raise ValueError(f"{path} must have columns {sorted(required)}")
        for raw in reader:
            rows.append(
                Observation(
                    condition=raw["condition"].strip(),
                    day=int(raw["day"]),
                    value=float(raw["value"]),
                )
            )
    if not rows:
        raise ValueError(f"{path} did not contain any observations")
    return rows


def group_rows(rows: Iterable[Observation]) -> dict[str, list[Observation]]:
    grouped: dict[str, list[Observation]] = {}
    for row in rows:
        grouped.setdefault(row.condition, []).append(row)
    return grouped


def slope_per_day(points: list[Observation]) -> float:
    days = [point.day for point in points]
    values = [point.value for point in points]
    n = len(points)
    mean_day = fmean(days)
    mean_value = fmean(values)
    numerator = sum((day - mean_day) * (value - mean_value) for day, value in zip(days, values))
    denominator = sum((day - mean_day) ** 2 for day in days)
    if denominator == 0:
        return 0.0
    return numerator / denominator


def summarize(rows: Iterable[Observation]) -> list[SummaryRow]:
    grouped = group_rows(rows)
    order = {condition: index for index, condition in enumerate(grouped)}
    rows = [
        SummaryRow(
            condition=condition,
            n=len(points),
            mean_value=fmean(point.value for point in points),
            min_value=min(point.value for point in points),
            max_value=max(point.value for point in points),
            slope_per_day=slope_per_day(points),
        )
        for condition, points in grouped.items()
    ]
    return sorted(rows, key=lambda row: order[row.condition])


def format_float(value: float) -> str:
    return f"{value:.3f}"


def write_summary(rows: list[SummaryRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="\n", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["condition", "n", "mean_value", "min_value", "max_value", "slope_per_day"])
        for row in rows:
            writer.writerow(
                [
                    row.condition,
                    row.n,
                    format_float(row.mean_value),
                    format_float(row.min_value),
                    format_float(row.max_value),
                    format_float(row.slope_per_day),
                ]
            )


def write_figure(rows: list[Observation], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    grouped = group_rows(rows)
    canvas = make_canvas(WIDTH, HEIGHT)
    left = 90
    right = 40
    top = 40
    bottom = 70
    plot_width = WIDTH - left - right
    plot_height = HEIGHT - top - bottom
    x_min, x_max = 0, 7
    y_min, y_max = 8.0, 12.0

    def x_for(day: int) -> int:
        return left + round((day - x_min) / (x_max - x_min) * plot_width)

    def y_for(value: float) -> int:
        return top + round((y_max - value) / (y_max - y_min) * plot_height)

    for day in range(0, 8):
        x = x_for(day)
        draw_line(canvas, WIDTH, HEIGHT, x, top, x, top + plot_height, GRID, thickness=1)
    for tick in range(8, 13):
        y = y_for(float(tick))
        draw_line(canvas, WIDTH, HEIGHT, left, y, left + plot_width, y, GRID, thickness=1)

    axis_x = left
    axis_y = top + plot_height
    draw_line(canvas, WIDTH, HEIGHT, axis_x, top, axis_x, axis_y, AXIS, thickness=2)
    draw_line(canvas, WIDTH, HEIGHT, axis_x, axis_y, axis_x + plot_width, axis_y, AXIS, thickness=2)

    for day in range(0, 8):
        x = x_for(day)
        draw_line(canvas, WIDTH, HEIGHT, x, axis_y, x, axis_y + 8, AXIS, thickness=1)
        draw_number(canvas, WIDTH, HEIGHT, x - 4, axis_y + 12, day, BLACK, scale=2, spacing=1)
    for tick in range(8, 13):
        y = y_for(float(tick))
        draw_line(canvas, WIDTH, HEIGHT, axis_x - 8, y, axis_x, y, AXIS, thickness=1)
        draw_number(canvas, WIDTH, HEIGHT, 22, y - 8, tick, BLACK, scale=2, spacing=1)

    for number, condition in enumerate(["baseline", "increasing", "decreasing"], start=1):
        points = sorted(grouped[condition], key=lambda point: point.day)
        color = PALETTE[condition]
        for start, end in zip(points, points[1:]):
            draw_line(
                canvas,
                WIDTH,
                HEIGHT,
                x_for(start.day),
                y_for(start.value),
                x_for(end.day),
                y_for(end.value),
                color,
                thickness=4,
            )
        for point in points:
            draw_ellipse(canvas, WIDTH, HEIGHT, x_for(point.day), y_for(point.value), 3, color)
        end_point = points[-1]
        badge_x = min(x_for(end_point.day) + 10, WIDTH - 60)
        badge_y = max(y_for(end_point.value) - 9, 12)
        draw_badge(canvas, WIDTH, HEIGHT, badge_x, badge_y, number, color)

    save_png(path, canvas, WIDTH, HEIGHT)


def write_report(rows: list[SummaryRow], figure_path: Path, summary_path: Path, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table_lines = [
        "| condition | n | mean | min | max | slope/day |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        table_lines.append(
            "| {condition} | {n} | {mean} | {min} | {max} | {slope} |".format(
                condition=row.condition,
                n=row.n,
                mean=format_float(row.mean_value),
                min=format_float(row.min_value),
                max=format_float(row.max_value),
                slope=format_float(row.slope_per_day),
            )
        )
    report = "\n".join(
        [
            "# Synthetic Research Demo",
            "",
            "This report summarizes a fully synthetic dataset generated to exercise the Spark Agent research workflow.",
            "All inputs, outputs, and conclusions in this project are synthetic and should not be interpreted as scientific evidence.",
            "",
            "## Method",
            "",
            "The analysis script reads `data/raw/synthetic_observations.csv`",
            "and computes deterministic per-condition summary statistics plus a simple least-squares slope.",
            "",
            "## Results",
            "",
            "\n".join(table_lines),
            "",
            "## Interpretation",
            "",
            "The numbered lines in the figure map to the conditions below: 1 = baseline, 2 = increasing, 3 = decreasing.",
            "The baseline series is flat by construction, the increasing series rises by 0.5 units per day, and the decreasing series falls by 0.4 units per day.",
            "Those patterns demonstrate file generation, plotting, and report writing, not a real scientific effect.",
            "",
            "## Artifacts",
            "",
            f"- Figure: `{figure_path.relative_to(ROOT)}`",
            f"- Summary CSV: `{summary_path.relative_to(ROOT)}`",
            "- Notebook: `notebooks/analysis.ipynb`",
            "- Bibliography: `references/references.bib` (intentionally empty because no real literature identifiers were used)",
            "",
        ]
    )
    path.write_text(report, encoding="utf-8")


def write_bibliography(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "@comment{Synthetic demo only; no real literature identifiers were used.}\n"
        "@comment{This file is intentionally empty.}\n",
        encoding="utf-8",
    )


def write_notebook(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    notebook = {
        "cells": [
            {
                "cell_type": "markdown",
                "metadata": {},
                "source": [
                    "# Synthetic Research Demo\n",
                    "This notebook mirrors the deterministic analysis in `scripts/analysis.py`.",
                ],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "from pathlib import Path\n",
                    "print(Path('../reports/summary.csv').read_text())\n",
                ],
            },
        ],
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3.12"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(notebook, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def run() -> list[SummaryRow]:
    rows = read_observations(RAW_DATA)
    summary = summarize(rows)
    write_summary(summary, SUMMARY_PATH)
    write_figure(rows, FIGURE_PATH)
    write_report(summary, FIGURE_PATH, SUMMARY_PATH, REPORT_PATH)
    write_bibliography(BIB_PATH)
    write_notebook(NOTEBOOK_PATH)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate the raw input and print the computed summary without writing files.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = summarize(read_observations(RAW_DATA))
    if args.check_only:
        for row in summary:
            print(
                f"{row.condition}\t{row.n}\t{format_float(row.mean_value)}\t"
                f"{format_float(row.min_value)}\t{format_float(row.max_value)}\t"
                f"{format_float(row.slope_per_day)}"
            )
        return 0
    run()
    for row in summary:
        print(
            f"{row.condition}: n={row.n}, mean={format_float(row.mean_value)}, "
            f"min={format_float(row.min_value)}, max={format_float(row.max_value)}, "
            f"slope/day={format_float(row.slope_per_day)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
