"""Real autonomous-research task contracts shared by live provider gates."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RealTaskScenario:
    id: str
    prompt: str
    seed_files: dict[str, str]
    required_artifacts: tuple[str, ...]
    png_path: str
    csv_path: str
    notebook_path: str | None = None
    bibliography_path: str | None = None
    corpus_path: str | None = None

    def seed(self, workspace: Path) -> None:
        for relative, content in self.seed_files.items():
            path = workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

    def validate(self, workspace: Path) -> list[str]:
        missing = [
            path for path in self.required_artifacts if not (workspace / path).is_file()
        ]
        if missing:
            raise AssertionError(f"{self.id} did not create required artifacts: {missing}")

        png = (workspace / self.png_path).read_bytes()
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AssertionError(f"{self.id} figure is not a PNG: {self.png_path}")
        csv_lines = (workspace / self.csv_path).read_text(encoding="utf-8").splitlines()
        if len(csv_lines) < 2:
            raise AssertionError(f"{self.id} summary CSV has no data rows: {self.csv_path}")

        reports = [path for path in self.required_artifacts if path.endswith(".md")]
        if reports and not any(
            "limitation" in (workspace / path).read_text(encoding="utf-8").lower()
            for path in reports
        ):
            raise AssertionError(f"{self.id} report does not state limitations")

        if self.notebook_path:
            notebook = json.loads((workspace / self.notebook_path).read_text(encoding="utf-8"))
            if notebook.get("nbformat") != 4:
                raise AssertionError(f"{self.id} notebook is not nbformat 4")
            code_cells = [
                cell
                for cell in notebook.get("cells", [])
                if cell.get("cell_type") == "code"
            ]
            if not code_cells or not any(cell.get("outputs") for cell in code_cells):
                raise AssertionError(
                    f"{self.id} notebook has no executed code-cell output"
                )

        if self.bibliography_path:
            bibliography = (workspace / self.bibliography_path).read_text(encoding="utf-8")
            if "@" not in bibliography or not re.search(r"10\.\d{4,9}/", bibliography, re.I):
                raise AssertionError(
                    f"{self.id} bibliography has no DOI-bearing BibTeX entry"
                )
        if self.corpus_path:
            corpus = (workspace / self.corpus_path).read_text(encoding="utf-8")
            if not re.search(r"(?:10\.\d{4,9}/|arXiv|PMID)", corpus, re.I):
                raise AssertionError(f"{self.id} corpus has no stable scholarly identifier")

        return list(self.required_artifacts)


SCENARIOS = {
    "dataset": RealTaskScenario(
        id="dataset",
        seed_files={
            "data.csv": (
                "group,value,quality\ncontrol,1.0,ok\ncontrol,1.2,ok\n"
                "treatment,1.8,ok\ntreatment,2.1,ok\ntreatment,,missing\n"
            )
        },
        prompt=(
            "Work autonomously on data.csv. Load the exploratory-data-analysis skill and "
            "delegate exactly one bounded schema and quality review to the task agent. Then "
            "inspect the returned result, write and run scripts/analysis.py, and create "
            "tables/summary.csv, figures/analysis.png, and reports/data-analysis.md with "
            "limitations. Verify every artifact. Use only installed Python packages and do "
            "not ask questions."
        ),
        required_artifacts=(
            "scripts/analysis.py",
            "tables/summary.csv",
            "figures/analysis.png",
            "reports/data-analysis.md",
        ),
        png_path="figures/analysis.png",
        csv_path="tables/summary.csv",
    ),
    "papers-data": RealTaskScenario(
        id="papers-data",
        seed_files={
            "data.csv": (
                "workflow,reproduced,minutes\nscript,1,4\nnotebook,1,7\n"
                "notebook,0,18\nscript,1,5\nnotebook,1,9\n"
            ),
            "papers/source-notes.md": (
                "Candidate primary sources to verify, not facts to assume:\n"
                "- FAIR Guiding Principles: https://doi.org/10.1038/sdata.2016.18\n"
                "- Ten Simple Rules for Jupyter: https://doi.org/10.1371/journal.pcbi.1007007\n"
            ),
        },
        prompt=(
            "Run a real papers-plus-data synthesis. Inventory data.csv and "
            "papers/source-notes.md; "
            "load the literature-review skill; delegate exactly one bounded source-verification "
            "check to the task agent; fetch the two DOI landing pages and never invent metadata. "
            "Inspect data quality, write and execute scripts/papers_data_analysis.py, and produce "
            "references/corpus.csv, references/references.bib, tables/papers_data_summary.csv, "
            "figures/papers_data_analysis.png, and reports/papers-data-synthesis.md. Trace numeric "
            "claims to executed outputs, literature claims to stable identifiers, state "
            "limitations, verify every artifact, use only installed Python packages, and do not "
            "ask questions."
        ),
        required_artifacts=(
            "references/corpus.csv",
            "references/references.bib",
            "scripts/papers_data_analysis.py",
            "tables/papers_data_summary.csv",
            "figures/papers_data_analysis.png",
            "reports/papers-data-synthesis.md",
        ),
        png_path="figures/papers_data_analysis.png",
        csv_path="tables/papers_data_summary.csv",
        bibliography_path="references/references.bib",
        corpus_path="references/corpus.csv",
    ),
    "notebook": RealTaskScenario(
        id="notebook",
        seed_files={
            "data.csv": (
                "day,condition,value\n0,control,10\n1,control,10\n2,control,10\n"
                "0,treated,9\n1,treated,11\n2,treated,13\n"
            )
        },
        prompt=(
            "Build a reproducible notebook analysis for data.csv. Load the notebook-analysis "
            "skill and delegate exactly one bounded notebook QA check to the task agent. Write "
            "and run scripts/notebook_analysis.py, create tables/notebook_summary.csv and "
            "figures/notebook_analysis.png, and create notebooks/analysis.ipynb with real executed "
            "code-cell outputs plus reports/notebook-analysis.md. The notebook and script must "
            "agree; trace all numbers, state limitations, verify every artifact, use only "
            "installed Python packages, and do not ask questions."
        ),
        required_artifacts=(
            "scripts/notebook_analysis.py",
            "tables/notebook_summary.csv",
            "figures/notebook_analysis.png",
            "notebooks/analysis.ipynb",
            "reports/notebook-analysis.md",
        ),
        png_path="figures/notebook_analysis.png",
        csv_path="tables/notebook_summary.csv",
        notebook_path="notebooks/analysis.ipynb",
    ),
}


def scenario(name: str) -> RealTaskScenario:
    try:
        return SCENARIOS[name]
    except KeyError as error:
        choices = ", ".join(sorted(SCENARIOS))
        raise ValueError(
            f"unknown real-task scenario {name!r}; choose one of: {choices}"
        ) from error
