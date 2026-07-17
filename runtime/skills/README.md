# runtime/skills

Scientific skills, layered:

```text
skills/
  core/      # small, loadable Spark research/data skills
  external/  # third-party skill packs, fetched by script — git-ignored
  user/      # user-installed / custom skills (live in the runtime workspace)
```

Core skills are bundled as the `skills-core/` app resource and deployed next to
the external pack on every sidecar start; directories without a `SKILL.md` are
skipped. `manifest.json` is the catalog and provenance contract for bundled core
skills; every listed path must stay inside this directory and contain a matching
`SKILL.md` name.

## Foundation research skills

The bundled core pack contains 29 small, loadable research, data, review,
visualization, and computation skills. The evidence-to-writing loop includes:

- `research-lookup`, `literature-review`, `systematic-review`,
  `evidence-synthesis`, `citation-management`;
- `hypothesis-generation`, `scientific-brainstorming`,
  `scientific-critical-thinking`, `peer-review`, `scientific-writing`;
- `exploratory-data-analysis`, `data-cleaning`, `pandas`, `numpy-scipy`,
  `statistical-analysis`, `statsmodels`, `scikit-learn`, `model-evaluation`;
- `matplotlib`, `scientific-visualization`, `notebook-analysis`, and
  `reproducible-python`, alongside the reusable local gates and run helpers.

Their instructions are Spark-authored, behavior-only implementations informed
by corresponding OpenScience capabilities at commit
`e9844a49f1f4d93cbf5f88b8f4880c003adc6e61` (Apache-2.0); no upstream skill file
is copied or substantially adapted. New Batch 2 skills are original Spark
content under MIT. Any future Apache adaptation must retain its source
repository, upstream path, pinned commit, Apache notice, and a statement that
Spark modified it. Exact source references and reuse classifications are
recorded in `manifest.json`.

## Default pack: ai4s-skills (bundled into the installer)

The default scientific skills come from
[ai4s-research/ai4s-skills](https://github.com/ai4s-research/ai4s-skills)
(research-explorer, literature-survey, experiment-suite, paper-writer,
integrity-auditor, mindmap-render, ai4s-agent).

How they ship, end to end:

1. `scripts/dev/fetch-skills.sh` (run locally and in CI) downloads the pack at a
   pinned commit into `external/ai4s-skills/`.
2. `tauri.conf.json` bundles that directory as an app resource (`resources/skills/`).
3. On every sidecar start, `runtime.rs::deploy_bundled_skills` syncs the pack into
   the app-private profile's global skills dir (`<xdg-config>/opencode/skills/`),
   which OpenCode scans regardless of project detection. Bundled skill directories
   are replaced on app upgrade; the workspace's own `.opencode/skills/` stays
   reserved for user-installed skills. Skill listing must be workspace-scoped
   (`GET /skill?directory=…`) — the SDK does this via its `directory` option.

To bump the pack version, update `AI4S_SKILLS_COMMIT` in `fetch-skills.sh`.

## Office pack: Anthropic document skills (not currently bundled)

The docx / pdf / pptx / xlsx skills come from Anthropic's open-source
[anthropics/skills](https://github.com/anthropics/skills) repo (Apache-2.0;
each skill directory keeps its own `LICENSE.txt`). Spark does not currently
fetch or declare an Anthropic `skills-office/` app resource, so these skills are
not part of the installer or deployed by `deploy_bundled_skills`. Adding an
optional office pack later must pin its source, preserve its licenses, declare
the Tauri resource explicitly, and add matching deployment and validation.

## Third-party skills

Do **not** enable large third-party collections (e.g. ~148 K-Dense skills) by
default. Use curated install, enable by domain, and always surface each skill's
license, dependencies, and risk.

Each skill directory must contain a `SKILL.md`.
