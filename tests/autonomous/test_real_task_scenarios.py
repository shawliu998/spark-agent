from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from real_task_scenarios import SCENARIOS, scenario


class RealTaskScenarioTests(unittest.TestCase):
    def test_catalog_covers_three_distinct_real_tasks(self) -> None:
        self.assertEqual(set(SCENARIOS), {"dataset", "papers-data", "notebook"})
        self.assertEqual(len({item.prompt for item in SCENARIOS.values()}), 3)

    def test_each_contract_accepts_minimal_structurally_valid_artifacts(self) -> None:
        for task in SCENARIOS.values():
            with self.subTest(task=task.id), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                task.seed(root)
                for relative in task.required_artifacts:
                    path = root / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if relative == task.png_path:
                        path.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
                    elif relative == task.csv_path:
                        path.write_text("name,value\nfixture,1\n", encoding="utf-8")
                    elif relative == task.notebook_path:
                        path.write_text(
                            json.dumps(
                                {
                                    "nbformat": 4,
                                    "nbformat_minor": 5,
                                    "metadata": {},
                                    "cells": [
                                        {
                                            "cell_type": "code",
                                            "execution_count": 1,
                                            "metadata": {},
                                            "source": ["print('ok')\n"],
                                            "outputs": [
                                                {
                                                    "output_type": "stream",
                                                    "name": "stdout",
                                                    "text": ["ok\n"],
                                                }
                                            ],
                                        }
                                    ],
                                }
                            ),
                            encoding="utf-8",
                        )
                    elif relative == task.bibliography_path:
                        path.write_text("@article{x, doi={10.1234/example}}\n", encoding="utf-8")
                    elif relative == task.corpus_path:
                        path.write_text("title,DOI\nExample,10.1234/example\n", encoding="utf-8")
                    elif relative.endswith(".md"):
                        path.write_text("## Limitations\nFixture only.\n", encoding="utf-8")
                    else:
                        path.write_text("# fixture\n", encoding="utf-8")
                self.assertEqual(task.validate(root), list(task.required_artifacts))

    def test_missing_artifact_fails_with_the_scenario_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(AssertionError, "papers-data"):
                scenario("papers-data").validate(Path(temporary))

    def test_unknown_scenario_lists_choices(self) -> None:
        with self.assertRaisesRegex(ValueError, "dataset, notebook, papers-data"):
            scenario("missing")


if __name__ == "__main__":
    unittest.main()
