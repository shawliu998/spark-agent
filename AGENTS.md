# Spark Agent

Brand name: **Spark Agent** — "Local-first, evidence-first research agent for
macOS." Bundle identifier is `io.github.shawliu998.sparkagent`. Internal
`@ai4s/*` package names remain unchanged for upstream compatibility until they
can be separated behind stable extension interfaces.

## Brand asset source of truth

Keep the application icon and the wordmark separate:

- `apps/desktop/src/assets/spark-app-icon.png` is the canonical 1024px
  application-icon master: the user-selected blue-violet 3D Spark mark.
- `apps/desktop/src/assets/spark-icon.png` is the 512px derivative of that
  application-icon master.
- `apps/desktop/src-tauri/icons/` contains generated platform derivatives only.
  Regenerate them from `spark-app-icon.png`; never edit one platform icon as a
  new master.
- `apps/desktop/src/assets/spark-wordmark.png` is the canonical horizontal
  `spark` wordmark used by the desktop UI.
- `apps/desktop/src/assets/spark-wordmark.webp` is an alternate encoding of the
  same wordmark, not an application icon.

Never substitute the wordmark for the application icon, recreate the wordmark
from the icon, or introduce a generic `logo.*` asset whose role is ambiguous.

Project rules and working context for AI agents (Claude Code, Cursor, Codex, etc.).
`CLAUDE.md` is a symlink to this file — edit only `AGENTS.md`.

## Design principles

Keep it **simple, explicit, clear, complete**.

- **Simple** — no over-engineering; if not necessary, do not add entities.
- **Explicit** — no ambiguity; no bugs.
- **Clear** — understandable at a glance.
- **Complete** — cover the key points; prioritize safety.

## What this project is

An independent, local-first, model-agnostic, reproducible AI research desktop
for macOS. It reuses MIT-licensed portions of Open Science Desktop while keeping
Spark-specific research services and workflows product-owned. See `README.md`, `docs/PRD.md`, and
`docs/TECHNICAL_DESIGN.md`.

Recommended stack: **Tauri 2 + React + TypeScript + Vite**, Tailwind + Radix UI,
**OpenCode** as the agent runtime (bundled single-binary sidecar; HTTP + SSE API),
local workspace + SQLite + JSONL provenance.

## Product design source of truth

For product, UX, Figma, visual, interaction, or frontend design work, read
`PRODUCT.md` before planning or editing. `PRODUCT.md` defines the current product
positioning, information architecture, competitor hierarchy, anti-references, and
active design phase. When a general roadmap document conflicts with its current
design phase, follow `PRODUCT.md` for design sequencing while preserving the
architecture and safety contracts in this file.

### Competitor-first design guardrails

- The active design goal is to reproduce proven research-product interface
  capabilities before inventing new product structure. Elicit is the primary
  reference; Consensus and SciSpace are secondary references for the specific
  surfaces named in `PRODUCT.md`.
- Every new Figma screen must name one primary captured competitor screen or stable
  node as its visual and interaction target. No visual target means no new screen.
- Reproduce layout, hierarchy, density, controls, interaction states, and content
  behavior first. Replace competitor branding and proprietary content with Spark
  branding and neutral research examples; do not redesign during the reproduction
  pass.
- Use one primary competitor per screen. Combining patterns from multiple products
  requires an explicit rationale and user approval.
- Do not invent product requirements to make a screen look complete. A visible
  feature must be grounded in a captured competitor capability, an implemented
  Spark capability, or an explicit user request.
- Do not continue the Project → Sources → Plan → Execution → Evidence → Results →
  Review sequence as permanent navigation or automatically create its next screen.
  It is an internal workflow lifecycle and may only appear as compact contextual
  progress when useful.
- Local-first storage, approvals, hashes, provenance, sandboxing, and audit records
  remain non-negotiable system boundaries. Keep them progressively disclosed in
  settings, inspectors, execution approval, source details, and export metadata;
  do not make them the default page hierarchy or repeat them on every object.
- Do not implement React from an unapproved exploratory Figma screen. Complete the
  named competitor capture, reproduce one screen, show the visual result, and stop
  for user confirmation before moving to the next capability group.
- A generic request such as “continue” means continue the currently approved
  screen or audit only. It does not authorize selecting the next workflow, creating
  a new screen, changing information architecture, or starting code implementation.
- New competitor research and captures are read-only. Do not upload private project
  data; use public papers, synthetic datasets, or disposable test content.

## Repository map

- `apps/desktop/` — Tauri + React desktop shell (`src/` frontend, `src-tauri/` Rust).
- `packages/` — `ui`, `shared`, `sdk` (the `OpenCodeClient` wrapper).
- `runtime/` — `manager`, `opencode-profile`, `mcp`, `skills`.
- `docs/` — product and technical specs.
- `examples/bci-trends/` — the built-in demo project.
- `scripts/` — release and dev scripts.

## Architecture guardrails

- The UI never calls OpenCode directly — it goes through `packages/sdk` (`OpenCodeClient`).
  Pin the OpenCode version (see `OPENCODE_VERSION`) and bundle it as a sidecar.
- Keep the frontend, desktop shell, and agent runtime decoupled.
- Skills, MCP servers, and model providers must stay pluggable.
- Keep the artifact schema and workflow templates stable and versioned.

## Safety defaults (non-negotiable for the desktop)

- The agent may only access the current workspace.
- Command execution, file deletion, dependency install, and remote connections
  require approval (manual approval mode by default — never ship `off`).
- API keys go to the OS keychain / credential manager; never into provenance,
  logs, crash reports, git, or exported projects.

## Working conventions

- Default working language for discussion is Chinese; **all project files and
  code are in English** (this is a pure-English project).
- One progress file: `PROGRESS.md`. Append one line per real milestone,
  `YYYY-MM-DD HH:MM` + a one-sentence conclusion, newest on top. Results and
  blockers only.
- Avoid adding new Markdown docs unless requested — too many docs become debt.
- Prefer minimal, verifiable changes; every step should produce a checkable result.
- Do not write inferences as verified facts; tie conclusions to code or data.
- New session workspaces are local git repos: the app initializes them and makes
  best-effort local commits after workspace file changes. Never set a remote or push.

## Model collaboration guardrails

Use lower-reasoning models only for bounded, reversible execution with an
explicit acceptance checklist (for example: applying a reviewed Figma change,
running a named audit, or updating a ledger). Do not delegate product choices,
safety-boundary interpretation, architecture, or final quality judgments to
them.

Use a higher-reasoning model to define the scope and invariants before execution
and to independently review the resulting artifact afterward. The reviewer must
be read-only, receive the concrete node/file target and checklist, and report
actionable findings by priority. The executor fixes only confirmed findings and
re-runs the smallest relevant evidence-backed audit.

For token efficiency, pass artifacts by stable identifiers and concise
acceptance criteria rather than full history; avoid duplicate inspection; use
incremental changes and screenshots only at section boundaries; and stop the
review loop when all explicit criteria pass. Keep every model within the same
workspace, approval, and data-access boundary.
