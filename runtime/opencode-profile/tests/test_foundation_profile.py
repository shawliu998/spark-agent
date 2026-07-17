from __future__ import annotations

import json
import re
import unittest
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[3]
PROFILE = ROOT / "runtime" / "opencode-profile"
SKILLS = ROOT / "runtime" / "skills"
AGENTS = PROFILE / "agents"

OPENSCIENCE_COMMIT = "e9844a49f1f4d93cbf5f88b8f4880c003adc6e61"
OPENCODE_COMMIT = "10c894bdeef3618f5666fb506ef7f9491bb964d8"

EXPECTED_AGENTS = {
    "research": "primary",
    "biology": "primary",
    "physics": "primary",
    "ml": "primary",
    "plan": "primary",
    "literature-review": "subagent",
    "critique": "subagent",
    "reviewer": "subagent",
    "write": "subagent",
    "explore": "subagent",
    "task": "subagent",
}

ENTRY_SKILLS = {
    "literature-review",
    "citation-management",
    "hypothesis-generation",
    "scientific-critical-thinking",
    "scientific-writing",
    "exploratory-data-analysis",
    "statistical-analysis",
    "matplotlib",
    "research-lookup",
    "systematic-review",
    "evidence-synthesis",
    "scientific-brainstorming",
    "peer-review",
    "data-cleaning",
    "pandas",
    "numpy-scipy",
    "statsmodels",
    "scikit-learn",
    "notebook-analysis",
    "reproducible-python",
    "model-evaluation",
    "scientific-visualization",
}

FOUNDATION_SKILLS = {
    "literature-review",
    "citation-management",
    "hypothesis-generation",
    "scientific-critical-thinking",
    "scientific-writing",
    "exploratory-data-analysis",
    "statistical-analysis",
    "matplotlib",
}


def frontmatter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise AssertionError("missing opening frontmatter delimiter")
    try:
        end = lines.index("---", 1)
    except ValueError as error:
        raise AssertionError("missing closing frontmatter delimiter") from error
    return "\n".join(lines[1:end])


def scalar(metadata: str, key: str) -> str | None:
    match = re.search(rf"^{re.escape(key)}:\s*(.+?)\s*$", metadata, re.MULTILINE)
    return match.group(1).strip('"\'') if match else None


class FoundationProfileTest(unittest.TestCase):
    def test_base_config_uses_pinned_schema_and_safe_defaults(self) -> None:
        config = json.loads((PROFILE / "opencode.json").read_text())
        self.assertEqual(config["$schema"], "https://opencode.ai/config.json")
        self.assertEqual(config["default_agent"], "research")
        self.assertEqual(config["share"], "disabled")
        self.assertFalse(config["autoupdate"])
        self.assertEqual(config["permission"]["*"], "ask")
        self.assertEqual(config["permission"]["external_directory"], "deny")
        self.assertEqual(config["permission"]["read"]["*.env"], "ask")
        self.assertEqual(config["permission"]["read"]["*.env.*"], "ask")
        self.assertEqual(config["permission"]["read"]["*.env.example"], "allow")
        self.assertEqual(config["permission"]["read"]["mcp:*"], "ask")
        self.assertEqual(config["permission"]["edit"], "ask")
        self.assertEqual(config["permission"]["apply_patch"], "ask")
        self.assertEqual(config["permission"]["bash"], "ask")
        self.assertEqual(config["permission"]["webfetch"], "ask")
        self.assertEqual(config["permission"]["websearch"], "ask")
        self.assertNotIn("provider", config)
        self.assertNotIn("model", config)
        self.assertNotIn("mcp", config)

    def test_agent_roster_and_changed_file_notices(self) -> None:
        files = {path.stem: path for path in AGENTS.glob("*.md")}
        self.assertEqual(set(files), set(EXPECTED_AGENTS))
        for name, expected_mode in EXPECTED_AGENTS.items():
            text = files[name].read_text()
            metadata = frontmatter(text)
            self.assertEqual(scalar(metadata, "mode"), expected_mode, name)
            self.assertTrue(scalar(metadata, "description"), name)
            self.assertIn("Modified for Spark Agent", metadata, name)
            self.assertIn(OPENSCIENCE_COMMIT, metadata, name)
            self.assertIn("Source license: Apache-2.0", metadata, name)

    def test_agent_overrides_do_not_bypass_global_safety_boundaries(self) -> None:
        for name in ("critique", "explore", "literature-review", "reviewer"):
            metadata = frontmatter((AGENTS / f"{name}.md").read_text())
            self.assertRegex(metadata, r'(?m)^    "\*\.env": ask$', name)
            self.assertRegex(metadata, r'(?m)^    "mcp:\*": ask$', name)
            self.assertRegex(metadata, r"(?m)^  external_directory: deny$", name)

        for name in ("literature-review", "plan"):
            metadata = frontmatter((AGENTS / f"{name}.md").read_text())
            self.assertRegex(metadata, r"(?m)^  webfetch: ask$", name)
            self.assertRegex(metadata, r"(?m)^  websearch: ask$", name)

        plan = frontmatter((AGENTS / "plan.md").read_text())
        self.assertRegex(plan, r'(?m)^    "\.opencode/plans/\*\.md": ask$')

    def test_research_prompt_contains_flexible_complete_loop(self) -> None:
        text = (AGENTS / "research.md").read_text()
        for stage in (
            "SCOPE",
            "LITERATURE",
            "REASON",
            "METHODOLOGY",
            "COMPUTE",
            "ANALYZE",
            "SYNTHESIZE",
            "WRITE",
        ):
            self.assertIn(f"### {stage}", text)
        self.assertIn("flexible method, not a mandatory state machine", text)
        self.assertIn("Never invent", text)
        self.assertIn("Continue working until the requested deliverable is completed", text)
        self.assertIn("desktop sends one Research Agent turn", text)
        self.assertIn("`task` for independent parallel units", text)
        self.assertIn("parent Research Agent must inspect and", text)
        self.assertIn("For mixed PDF-and-data work", text)
        self.assertIn("python -m jupyter nbconvert", text)

    def test_read_only_specialists_are_fail_closed(self) -> None:
        for name in ("literature-review", "critique", "reviewer", "explore"):
            metadata = frontmatter((AGENTS / f"{name}.md").read_text())
            self.assertRegex(metadata, r'(?m)^  "\*": deny$')
            self.assertRegex(metadata, r"(?m)^  read:$")
            self.assertRegex(metadata, r'(?m)^    "\*": allow$')
            for tool in ("glob", "grep", "list", "skill"):
                self.assertRegex(metadata, rf"(?m)^  {tool}: allow$")
            self.assertNotRegex(metadata, r"(?m)^  bash: allow$")

    def test_manifest_covers_every_deployable_core_skill(self) -> None:
        manifest = json.loads((SKILLS / "manifest.json").read_text())
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["namespace"], "spark-core")
        upstream = manifest["upstreamReference"]
        self.assertEqual(upstream["commit"], OPENSCIENCE_COMMIT)
        self.assertEqual(upstream["license"], "Apache-2.0")

        entries = manifest["skills"]
        names = [entry["name"] for entry in entries]
        self.assertEqual(len(names), len(set(names)), "duplicate manifest skill")
        deployable = {
            path.parent.name for path in (SKILLS / "core").glob("*/SKILL.md")
        }
        self.assertEqual(set(names), deployable)
        self.assertEqual(
            {entry["name"] for entry in entries if entry["entry"]},
            ENTRY_SKILLS,
        )

        skills_root = SKILLS.resolve()
        for entry in entries:
            relative = PurePosixPath(entry["path"])
            self.assertFalse(relative.is_absolute(), entry["name"])
            self.assertNotIn("..", relative.parts, entry["name"])
            directory = (SKILLS / Path(*relative.parts)).resolve()
            self.assertTrue(directory.is_relative_to(skills_root), entry["name"])
            self.assertTrue(directory.is_dir(), entry["name"])
            skill_file = directory / "SKILL.md"
            self.assertTrue(skill_file.is_file(), entry["name"])
            metadata = frontmatter(skill_file.read_text())
            self.assertEqual(scalar(metadata, "name"), entry["name"])
            self.assertTrue(scalar(metadata, "description"), entry["name"])
            for child in directory.rglob("*"):
                self.assertFalse(child.is_symlink(), str(child))

    def test_behavior_only_skills_and_attribution_are_explicit(self) -> None:
        manifest = json.loads((SKILLS / "manifest.json").read_text())
        foundation = {
            entry["name"]: entry
            for entry in manifest["skills"]
            if entry["name"] in FOUNDATION_SKILLS
        }
        self.assertEqual(set(foundation), FOUNDATION_SKILLS)
        for name, entry in foundation.items():
            self.assertEqual(entry["reuseType"], "behavior-only", name)
            self.assertEqual(entry["license"], "MIT", name)
            self.assertTrue(entry["upstreamPath"].startswith("backend/cli/skills/"))

        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text()
        self.assertIn(OPENSCIENCE_COMMIT, notices)
        self.assertIn("Apache-2.0", notices)
        self.assertTrue((PROFILE / "OPENSCIENCE_LICENSE.txt").is_file())
        self.assertTrue((PROFILE / "OPENSCIENCE_NOTICE.txt").is_file())
        tauri = json.loads(
            (ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_text()
        )
        resources = tauri["bundle"]["resources"]
        self.assertIn(
            "../../../runtime/opencode-profile/OPENSCIENCE_LICENSE.txt", resources
        )
        self.assertIn(
            "../../../runtime/opencode-profile/OPENSCIENCE_NOTICE.txt", resources
        )

    def test_profile_readme_records_verified_opencode_contract(self) -> None:
        readme = (PROFILE / "README.md").read_text()
        self.assertIn("OpenCode **1.17.13**", readme)
        self.assertIn(OPENCODE_COMMIT, readme)
        self.assertIn("{agent,agents}/**/*.md", readme)
        self.assertIn("{skill,skills}/**/SKILL.md", readme)


if __name__ == "__main__":
    unittest.main()
