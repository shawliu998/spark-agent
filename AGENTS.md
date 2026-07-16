# Spark Agent

Brand name: **Spark Agent** — "The open-source AI workbench for scientific
research." Bundle identifier is `io.github.shawliu998.sparkagent`. Internal
`@ai4s/*` package names remain unchanged for upstream compatibility until they
can be separated behind stable extension interfaces.

Project rules and working context for AI agents (Claude Code, Cursor, Codex, etc.).
`CLAUDE.md` is a symlink to this file — edit only `AGENTS.md`.

## Design principles

Keep it **simple, explicit, clear, complete**.

- **Simple** — no over-engineering; if not necessary, do not add entities.
- **Explicit** — no ambiguity; no bugs.
- **Clear** — understandable at a glance.
- **Complete** — cover the key points; prioritize safety.

## What this project is

An independent, open-source, local-first, model-agnostic AI workbench for
scientific research. Its default General Research workspace can read literature,
form hypotheses, write and run code, inspect results, and produce research
artifacts. Optional Verified Workflows add strict approvals, reproducibility,
evidence binding, and deterministic review when a task needs them.

The project reuses MIT-licensed portions of Open Science Desktop and adapts
selected Apache-2.0 OpenScience agents and scientific skills with attribution,
while keeping Spark-specific services and workflows product-owned. See
`README.md`, `docs/PRD.md`, `docs/TECHNICAL_DESIGN.md`, and
`docs/OPENSCIENCE_PARITY.md`.

Recommended stack: **Tauri 2 + React + TypeScript + Vite**, Tailwind + Radix UI,
**OpenCode** as the agent runtime (bundled single-binary sidecar; HTTP + SSE API),
local workspace + SQLite + JSONL provenance.

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
- OpenCode is the only general agent runtime. It owns General Research sessions,
  turns, tools, agents, skills, MCP, providers, permissions, events, and sub-agents.
- `science-core` is optional and is canonical only for Structured / Verified
  Workflows and reusable scientific services. General Research must work without
  Docker or `science-core` and must not be forced through a fixed workflow router.
- Do not recreate capabilities already supplied by OpenCode. Thin, typed adapters
  in `packages/sdk` and the desktop runtime manager are the intended boundary.
- Keep the frontend, desktop shell, and agent runtime decoupled.
- Skills, MCP servers, and model providers must stay pluggable.
- Keep the artifact schema and workflow templates stable and versioned.

## Safety defaults (non-negotiable for the desktop)

- The agent may only access the current workspace. A command-pattern approval
  layer is not sufficient proof of confinement; do not claim this invariant
  until an isolation boundary enforces it for arbitrary code.
- Command execution, file deletion, dependency install, and remote connections
  require approval (manual approval mode by default — never ship `off`).
- Verified Workflows retain their stricter plan, execution, and remote-data
  approvals independently of the General Research runtime.
- API keys and equivalent long-lived secrets must use the OS keychain /
  credential manager, including model-provider and scientific-connector keys.
  They must never enter provenance, logs, crash reports, git, or exported
  projects. Private file modes are defense in depth, not a substitute for
  credential-manager storage; any migration gap blocks a Foundation-complete
  claim.

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
