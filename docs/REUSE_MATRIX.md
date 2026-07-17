# Spark Agent reuse matrix

Audit snapshot: 2026-07-17. This is a reuse decision record, not a feature
roadmap. `adapted`, `behavior-only`, `copied`, and `excluded` are the reuse
classifications already used by `docs/OPENSCIENCE_PARITY.md`; unknown upstream
identity, license, or version is recorded as unknown rather than inferred.

| Candidate | Repository evidence | License / version evidence | Spark boundary and decision | Current evidence |
| --- | --- | --- | --- | --- |
| Spark Agent | This repository: `README.md`, `AGENTS.md`, `packages/`, `runtime/`, `apps/desktop/` | Spark-owned code is MIT under [`LICENSE`](../LICENSE); product version is not used as a reuse pin | Product-owned implementation. Keep the desktop, workspace, provenance, and Verified Workflow boundaries Spark-specific. | Current code and tests in this repository |
| OpenCode | [`anomalyco/opencode`](https://github.com/anomalyco/opencode); Spark SDK boundary in [`packages/sdk`](../packages/sdk/) and sidecar manager in [`apps/desktop/src-tauri/src/runtime.rs`](../apps/desktop/src-tauri/src/runtime.rs) | MIT is recorded in [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md); Spark pins `1.17.13` in [`docs/TECHNICAL_DESIGN.md`](TECHNICAL_DESIGN.md) and runtime code | `behavior-only` at the app layer; reuse the pinned runtime as the sole General Research runtime. Do not fork sessions, tools, agents, skills, MCP, or permissions. | OpenCodeClient, sidecar version checks, profile tests, and live foundation smoke |
| K-Dense BYOK | [`K-Dense-AI/k-dense-byok`](https://github.com/K-Dense-AI/k-dense-byok) describes a local BYOK research workspace with files, code, sources, figures, reports, and run history | Upstream repository page identifies MIT; no release/version pin was found in Spark or adopted | `behavior-only`; do not copy its app/runtime or make it a second product shell. Consider only separately reviewed workflow ideas. | Not bundled, imported, or enabled by default |
| `scientific-agent-skills` | [`K-Dense-AI/scientific-agent-skills`](https://github.com/K-Dense-AI/scientific-agent-skills) is a large Agent Skills collection | Upstream root is MIT, but its README says individual `SKILL.md` files can carry different licenses; no Spark pin | `excluded` from the default pack. Future curated installation must inspect each skill's license, dependencies, network/data flow, and risk before deployment. | [`runtime/skills/README.md`](../runtime/skills/README.md) explicitly prohibits enabling the large collection by default |
| `paper-search-mcp` | [`openags/paper-search-mcp`](https://github.com/openags/paper-search-mcp); Spark connector mapping in [`science_mcp.rs`](../apps/desktop/src-tauri/src/science_mcp.rs) and [`scienceConnectors.ts`](../apps/desktop/src/lib/scienceConnectors.ts) | Upstream repository identifies MIT; Spark pins package `paper-search-mcp==0.1.4` in native connector code | `behavior-only` for the external package integration; no upstream source is copied into Spark. Keep it optional, pluggable, and credential-free by default. | Native package allowlist and connector tests; no claim of full-text/provider reliability beyond upstream behavior |
| Datalayer Jupyter MCP | [`datalayer/jupyter-mcp-server`](https://github.com/datalayer/jupyter-mcp-server) provides an MCP server for real-time Jupyter notebook control | Upstream repository identifies BSD-3-Clause and currently shows release `1.0.2`; no Spark package pin is adopted | `excluded` from the enabled Agent MCP path. Spark may keep local JupyterLab for the UI, but agent registration remains security-gated until the documented secretless broker, target-integrity, per-call approval, dependency-install, lock, and packaged-E2E gates pass. | [`docs/TECHNICAL_DESIGN.md`](TECHNICAL_DESIGN.md), native Jupyter reconciliation, and fail-closed tests |
| OpenScience | [`synthetic-sciences/openscience`](https://github.com/synthetic-sciences/openscience); exact source paths and revision are recorded in [`docs/OPENSCIENCE_PARITY.md`](OPENSCIENCE_PARITY.md) | Apache-2.0 and revision `e9844a49f1f4d93cbf5f88b8f4880c003adc6e61` are recorded in repository notices and parity docs | `adapted` for selected prompt/agent behavior and `behavior-only` for independently written skills. Do not import its Bun/Hono runtime, SolidJS UI, cloud catalog, Atlas, plugins, or brand assets. | Parity matrix, retained notice, profile/skill tests, and deterministic OpenCode smoke |
| Vera | Candidate identified as [`lemon07r/Vera`](https://github.com/lemon07r/Vera), an offline Rust semantic code-search tool with CLI/skill/MCP surfaces | Upstream repository identifies MIT; no version pin was found in Spark | `excluded` from the current default path: no code, model, index, or MCP server is bundled. Re-evaluate only as an optional workspace search integration with path confinement and license/model provenance review. | No repository import or runtime registration |
| ContextDelta | No authoritative upstream repository or exact project identity was found in this checkout or the reviewed upstream search results | License and version: unknown | `excluded` pending an exact upstream identity and license/version evidence; do not copy or depend on an ambiguously named project. | No code, package, or config reference found |
| Dart | Candidate [`nsivaku/dart`](https://github.com/nsivaku/dart) is a research repository for multi-agent disagreement/tool recruitment, but “Dart” is ambiguous | Candidate repository identifies MIT; version/release pin: unknown | `behavior-only` at most; no code or model reuse. The candidate is not a desktop runtime, MCP server, or default research dependency for Spark. | No code, package, or config reference found |

## Default-path coupling audit

- [`LiveSessionPage.tsx`](../apps/desktop/src/app/routes/LiveSessionPage.tsx) imports
  `generateTaskPlan` only inside the explicitly opened Tasks panel. The normal
  Composer path calls `sendPrompt` directly; it does not create a deterministic
  task plan.
- [`taskPlanning.ts`](../apps/desktop/src/lib/taskPlanning.ts) is a local,
  editable-plan helper for the optional manual task batch. It is not a General
  Research workflow router.
- [`modelRouting.ts`](../apps/desktop/src/lib/modelRouting.ts) no longer
  classifies prompt text or provider names. `runtime.ts` retains the persisted
  `modelRoutingMode`/`lastModelRoute` compatibility state and uses the selected
  `defaultModel` for task batches; the default `sendPrompt` path does not call
  `routeModelForTask`.

No code change was warranted: removing the compatibility state or imports would
expand this audit into a migration and would not remove a live default-path
coupling.

## Evidence limits

This matrix records repository evidence and the linked upstream repository
metadata checked on the audit date. It does not certify every transitive
dependency, every individual skill license, provider availability, or runtime
security property. Those remain release gates where the matrix says so.
