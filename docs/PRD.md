# Spark Agent Desktop — Product Requirements

> **Status (Foundation, 2026-07-16).** The only general agent runtime is
> **OpenCode**, bundled as an isolated sidecar that does not touch a user's own
> OpenCode. General Research is the default product surface. The existing strict
> literature and dataset pipelines remain available as optional Structured /
> Verified Workflows. Connector and viewer parity described below is not all shipped.

## 1. Positioning

**Spark Agent Desktop** is an open-source, local-first AI workbench for scientific
research with macOS / Windows installers.

Give it a research goal. It can read the literature, form hypotheses, write and
run code, execute experiments, analyze results, and write up what it finds. It is
not an ordinary paper-summarization tool or a fixed workflow router. It supports:

- Literature search
- Paper parsing
- Data analysis
- Code execution
- Figure generation
- Report writing
- Citation checking
- Research artifacts and iterative methodology
- Optional reproducible and verified workflows

Slogan:

> The open-source AI workbench for scientific research.

## 2. Goals

### 2.1 Phase 1 goal

Phase 1 must be a genuinely installable desktop app, not a CLI tool.

Required support:

| Platform | Installer | Priority |
| --- | --- | --- |
| macOS Apple Silicon | `.dmg` / `.app` | P0 |
| macOS Intel | `.dmg` / `.app` | P1 |
| Windows x64 | `.exe` NSIS installer | P0 |
| Windows x64 | `.msi` installer | P1 |

Tauri officially supports macOS and Windows and can package `dmg`, `app`, `nsis`,
and `msi` targets; Windows can ship as `.msi` or an NSIS `setup.exe`.

### 2.2 Differentiation

Versus ordinary AI paper tools, Spark Agent is different because it is:

1. A research workbench, not a chat box.
2. A generator of traceable artifacts, not just text.
3. Model-agnostic (BYOK / OpenRouter / OpenAI-compatible / local), not tied to one model.
4. Transparent — it keeps code, data, figures, reports, logs, and provenance — not a black box.
5. Multi-domain — expanding from biology to AI4S, materials, chemistry, biology,
   medicine, engineering, and industry.

## 3. Target users

### 3.1 Core users

1. **Researchers** — fast literature reviews; organizing papers, data, figures,
   reports; reproducibility and citation accuracy.
2. **AI4S / AI-for-Science developers** — integrating scientific skills, MCP, and
   database connectors into one workbench; an open-source Claude Science alternative.
3. **Grad / PhD / postdoc students** — topic surveys, paper reading, experiment data
   analysis, submission material prep.
4. **Open-source AI agent users** — already using OpenCode, Codex, Claude Code, Cursor,
   MCP, Agent Skills; want a research-focused desktop product.

### 3.2 Non-target users (Phase 1)

- Complete beginners who cannot configure an API key.
- Users needing clinical diagnosis or medical decisions.
- Institutions needing multi-user collaborative SaaS.
- Teams needing enterprise permissions, audit, or SSO.

## 4. Core product principles

### 4.1 Local-first

Runs on the user's machine by default. Project files, corpora, figures, reports, and
execution logs are stored in a local workspace.

### 4.2 Model-agnostic

No lock-in to Claude, OpenAI, or any single local model. Users can choose OpenRouter,
OpenAI-compatible APIs, the Anthropic API, or local models; Ollama / vLLM / LM Studio
support follows.

### 4.3 Open-ended by default, reproducible when chosen

General Research lets the agent revise its plan, methods, code, and dependencies
as evidence arrives. It must not be forced through `science-core` intent routing,
a deterministic compiler, or an approval object for each step.

When users select a Verified Workflow, every important artifact must be traceable:

| Artifact | Must trace to |
| --- | --- |
| Figure | generating code, input data, parameters |
| Report | citation sources, data sources, analysis steps |
| Table | raw data, cleaning script |
| Conclusion | citations, data, model output |
| Agent action | time, tool, input, output, status |

### 4.4 Human-in-the-loop

General Research uses a Spark-managed OpenCode manual-approval profile. Ordinary
workspace reads may proceed, but every write/edit, Shell command, web request,
and MCP action asks first; unknown concrete tool IDs default to ask and file-tool
access outside the workspace is denied. The Composer does not offer Full Access
or persistent grants. Verified Workflows retain their existing plan, execution,
and remote-data approvals. Manual approval is not a process-isolation boundary,
so arbitrary-code confinement remains a separate Sandbox requirement.

### 4.5 Execution modes

| Mode | Product behavior | Runtime owner |
| --- | --- | --- |
| General Research (default) | Open-ended files, Python, Shell, skills, MCP, and iterative research; no Docker dependency | OpenCode |
| Sandbox Research | General capabilities executed in a constrained runtime with configurable network and resource limits | OpenCode plus an optional sandbox backend |
| Verified Workflow | Typed plans, explicit approvals, hash-bound inputs, deterministic compiler, isolated execution, and deterministic review | `science-core` and `science-runtime` |

OpenCode is canonical for General Research sessions, turns, tools, agents,
skills, permissions, providers, MCP, and sub-agents. `science-core` is canonical
only for Structured / Verified Workflows and reusable scientific services.

## 5. MVP scope

### 5.1 P0 features

#### 5.1.1 Install & first launch

After downloading and first opening, the user enters onboarding:

1. Choose a model provider.
2. Enter an API key.
3. Choose a workspace directory.
4. Detect the local runtime environment.
5. Use the bundled OpenCode runtime (auto-started; no separate install).
6. Create the first research project.

First launch must clearly tell the user: data is stored locally by default; the
selected OpenCode permission profile controls tool authorization; the user must
supply their own model API key; research results need human verification and are
not final conclusions.

#### 5.1.2 Home

Shows: recent projects, new project, example workflows, current runtime status, model
connection status, local workspace status.

Recommended default examples: Literature Review, Bibliometric Analysis,
Paper-to-Report, Dataset Analysis, Citation Review, Reproducibility Audit.

#### 5.1.3 Research agent workspace

The main work area, in a three-column layout:

```text
Left:   projects / workflows / files
Middle: agent chat + plan + execution progress
Right:  artifacts / citations / review / run logs
```

Default interaction: user submits a goal → Research Agent scopes the task → loads
relevant skills → reads literature and files → writes and runs code → inspects the
results → revises the method when needed → produces artifacts and a synthesis.
OpenCode owns this loop; it does not create a parallel `science-core` workflow.

#### 5.1.4 Plan confirmation

General Research may plan inline or use the read-only `plan` agent, but does not
require product-level plan approval. The following strict plan confirmation
belongs to Structured / Verified Workflows:

```text
Goal:
Data sources:
Steps:
Expected artifacts:
Risks & limitations:
Actions requiring authorization:
```

User options: Approve · Edit Plan · Run Step by Step · Cancel.

#### 5.1.5 Literature search

v1 sources: arXiv, PubMed, Crossref, OpenAlex, Semantic Scholar (optional API key),
local PDF import.

Features: keyword search; filter by year and source; dedup; export `corpus.csv`; save
search logs; record data-source limits.

#### 5.1.6 Skills library

The Skills page lists the **real** skills and agents the OpenCode runtime has loaded
(built-in + project `.opencode/skill/` + user config) — no hardcoded catalog. Skill
sources, layered:

1. **OpenCode built-in** skills/agents (shipped with the runtime).
2. **Bundled Spark skills** — the Foundation pack includes `literature-review`,
   `citation-management`, `hypothesis-generation`, `scientific-critical-thinking`,
   `exploratory-data-analysis`, `statistical-analysis`, `scientific-writing`, and
   `matplotlib` as OpenCode-compatible `SKILL.md` bundles.
3. **Third-party scientific skills** — e.g. K-Dense `scientific-agent-skills` (curated
   install, a later feature).

K-Dense `scientific-agent-skills` is a collection for science/research; its README
describes ~148 skills and compatibility with Claude Code, Codex, Cursor, OpenCode, and
other Agent Skills hosts.

#### 5.1.7 Code execution

v1 languages: Python, Shell (R later).

| Mode | Notes |
| --- | --- |
| Local | Run directly in the local workspace |
| Docker | Run in an isolated container |
| SSH | Remote server execution (later) |
| Modal | Cloud execution (later) |
| Jupyter Kernel | Notebook-style persistent kernel (later) |

OpenCode runs tools locally inside the bundled runtime by default; Docker sandbox and
SSH / Modal remote execution are optional advanced backends, so the desktop starts local
and expands later.

#### 5.1.8 Artifact panel

All outputs land here. Types: Markdown reports, CSV tables, PNG / SVG figures, PDFs,
Python scripts, notebooks, JSONL provenance, review reports.

Each artifact shows: filename, type, created time, generating step, input data,
generating code, review status, and export / copy / open actions.

#### 5.1.9 Provenance

Verified projects generate `provenance.jsonl`, `manifest.json`, and `review.md`.
General Research may create these artifacts when requested, but discovery in the
artifact dock does not depend on prior database registration.

`provenance.jsonl` records each step, append-only:

```json
{
  "step_id": "step_001",
  "type": "literature_search",
  "tool": "openalex",
  "input": {},
  "output_files": ["data/corpus.csv"],
  "timestamp": "",
  "status": "success"
}
```

#### 5.1.10 Reviewer panel

v1 reviewer does basic checks: citations exist; DOI / PMID / arXiv IDs are
well-formed; figures have generating code; tables have source data; reports include
limitations; no untraced artifacts; no steps the agent claims but never ran.

## 6. UI design requirements

### 6.1 Keywords

Modern, restrained, refined, research feel, tool feel — not flashy, not a traditional
admin panel, not a low-quality AI wrapper. Reference vibes: Linear's simplicity,
Cursor's technical feel, Notion's information structure, Raycast's command palette,
Vercel's cleanliness, Claude's warmth.

### 6.2 Visual style

Light theme (default):

| Use | Suggestion |
| --- | --- |
| Background | warm white / soft gray |
| Primary | deep indigo / blue violet |
| Accent | teal / cyan |
| Success | soft green |
| Warning | amber |
| Error | soft red |
| Text | near black / slate |

Dark theme:

| Use | Suggestion |
| --- | --- |
| Background | near black / deep navy |
| Card | dark slate |
| Primary | blue violet |
| Accent | cyan |
| Text | soft white |

### 6.3 Main layout

```text
┌─────────────────────────────────────────────────────────┐
│ Top Bar: Project / Model / Runtime / Sync / Settings    │
├──────────────┬──────────────────────────┬───────────────┤
│ Sidebar      │ Main Agent Workspace      │ Artifact Dock │
│ Projects     │ Chat / Plan / Execution   │ Files         │
│ Workflows    │ Progress Timeline         │ Figures       │
│ Skills       │ Code Blocks               │ Tables        │
│ Connectors   │ Reports                   │ Citations     │
│ Settings     │                          │ Review        │
└──────────────┴──────────────────────────┴───────────────┘
```

### 6.4 Core pages

- **Home** — welcome card, new project, recent projects, example workflows, runtime status, model status.
- **Project Workspace** — agent chat, execution timeline, plan approval card, tool-call cards, artifact dock, review warnings.
- **Literature** — search, filter, list, abstract preview, PDF status, citation info, add to corpus, export BibTeX / CSV.
- **Data & Code** — file tree, Python scripts, notebook preview, CSV preview, run history, environment dependencies.
- **Artifacts** — figure gallery, report preview, table preview, provenance chain, download / export.
- **Review** — citation check, figure provenance check, data source check, reproducibility check, risk warnings, limitations.
- **Skills** — installed skills, recommended scientific skills, install from GitHub, enable / disable, view `SKILL.md`, check license, check dependencies.
- **Settings** — model provider, API keys, workspace path, runtime backend, security approvals, update settings, appearance theme, data cleanup.

## 7. Key interactions

### 7.1 Plan card

Must be clean and clear. Contains: goal, step list, tools to call, expected artifacts,
risk notes, run buttons. Buttons: Approve & Run · Edit Plan · Run Step-by-step ·
Save as Workflow.

### 7.2 Tool-call card

Shows: tool name, status, input summary, output summary, duration, token / cost
(optional), view details, copy log. Status: Pending · Running · Waiting Approval ·
Success · Warning · Failed.

### 7.3 Approval dialog

OpenCode's Spark-managed profile asks before every workspace write/edit, Shell
command, web fetch/search, MCP action, or patch operation; unknown tool IDs ask
and file-tool access outside the workspace is denied. Ordinary bounded workspace
reads do not require a second Spark approval. General Research is not an OS
sandbox; Sandbox and Verified execution provide stronger isolation. Prompt
options are Allow Once · Deny · View Details; persistent grants and Full Access
are not offered.

### 7.4 Command palette

Shortcut: `Cmd + K` (macOS) / `Ctrl + K` (Windows). Quick actions: new project, search
literature, run reviewer, open settings, switch model, install skill, export report.

## 8. MVP example workflow

v1 must ship one complete demo:

```text
2023–2026 brain-computer interface literature trends
```

Outputs: `plan.md`, `data/corpus.csv`, `scripts/analyze.py`, `figures/year_trend.png`,
`figures/topic_clusters.png`, `figures/top_keywords.png`, `report.md`, `review.md`,
`provenance.jsonl`. Used for README, website, screenshots, video, and launch.

## 9. Roadmap

- **Foundation** — General Research as the default, OpenCode ownership, Research
  Agent and foundation skills, runtime-backed selectors, workspace artifact
  discovery, and relabeled Verified Workflows.
- **Agent and skill parity** — complete domain/sub-agent roster, skill inspection,
  authoring, precedence, and audited Git installation.
- **Scientific search and sandbox** — unified literature connectors, persistent
  notebook kernel, general container execution, Python environment management,
  R, and configurable network access.
- **Scientific workspace and extensibility** — scientific viewers, terminal/editor,
  custom agents and commands, MCP marketplace, cloud/HPC backends, and plugin hooks.

## 10. Non-functional requirements

### 10.1 Performance

Cold start < 3s (excluding first-time runtime init); no noticeable UI jank; streaming
agent output; live tool-call refresh; paginated large-file preview; lazy-loaded
figures; virtualized log lists.

### 10.2 Security

No silent permission escalation; every General write/edit/Shell/web/MCP operation
prompts, unknown concrete tools ask, and outside-workspace file-tool access is
denied. Stricter isolation and immutable approvals apply in Verified mode; remote
connections are auditable. Verified-workflow model keys and simple General
provider API keys use the OS credential manager; unsupported structured provider
records fail closed rather than being silently weakened or corrupted. OAuth,
scientific-connector, and Jupyter secret migration plus execution-time secret
isolation remain release-blocking work.

### 10.3 Maintainability

Frontend, desktop shell, and agent runtime decoupled; pluggable skills; configurable
MCP servers; extensible model providers; stable artifact schema; versioned workflow
templates.

### 10.4 Open-source friendliness

Clear first-screen README; one-click install; one-click demo; nice screenshots;
complete example results; clear license; separate note for third-party skill licenses.

## 11. One-liner

**Spark Agent Desktop is the open-source, local-first AI workbench for scientific
research: OpenCode powers open-ended research sessions, and optional Verified
Workflows add strict reproducibility and review when required.**
