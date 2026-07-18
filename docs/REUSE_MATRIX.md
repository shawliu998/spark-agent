# Spark Agent reuse matrix

Audit snapshot: 2026-07-17. Decisions describe the smallest adapter that keeps
OpenCode as the only General Research runtime. Unknown provenance remains
unknown until verified; a repository name alone is not license evidence.

| Capability | Existing implementation | License | Decision | Spark adapter | Status |
| --- | --- | --- | --- | --- | --- |
| General Agent runtime | Bundled OpenCode 1.17.13 through `packages/sdk` and the native sidecar manager | MIT, recorded in `THIRD_PARTY_NOTICES.md` | `use-directly` | Keep sessions, tools, Agents, Skills, task children, MCP, permissions, providers, and streaming behind `OpenCodeClient`. | Active and covered by profile/live smoke tests |
| Spark desktop and Verified Workflows | Tauri/React workspace, artifact, provenance, and `science-core` surfaces | Spark-owned MIT | `keep-existing-spark` | Keep product UI and deterministic Verified execution separate from General Research. | Active |
| Autonomous research behavior | Research profile, Autonomous preset, project Python, artifact diff, native child observation | Spark-owned MIT | `keep-existing-spark` | Continue one selected-model Research turn; do not introduce a workflow state machine. | Developer Preview |
| K-Dense BYOK UX | Pi/Fastify application with workspace, sub-agent, notebook, and Skill-management patterns | Upstream repository reports MIT; no Spark pin | `behavior-only` | Reuse product patterns only where they fit existing React/Tauri surfaces; do not import Pi runtime, sessions, providers, server, or event broker. | Reference only |
| K-Dense scientific Skills | Existing Spark core pack has 29 loadable Skills; a curated upstream subset adds 30 more | Upstream root MIT at commit `3f825caafe149b7853ec8c4d1dd7f4553ea6b2a5`, archive SHA-256 recorded in the curated manifest | `use-directly` | Verify and extract only the 30 selected directories into the existing OpenCode Skill directory; never execute upstream scripts or install every dependency. | Adapter implemented; bundled pack remains fallback |
| Literature search | `paper-search-mcp==0.1.4` is already in the native connector allowlist/profile | Upstream reports MIT | `use-directly` | Keep the package external and pinned; Spark owns installation, health, OpenCode configuration, permissions, and saved project outputs. | Configured; real multi-source run still unverified |
| Notebook fallback | Project `.spark/python`, pinned Jupyter packages, nbconvert guidance, notebook discovery/viewer | Spark-owned adapter over upstream Python packages | `keep-existing-spark` | Use `nbconvert --execute` as the portable minimum and test it through `pnpm test:notebook`. | Portable gate passes; managed runtime execution is environment-dependent |
| Datalayer Jupyter MCP | No enabled Agent-side Jupyter MCP; Spark-managed JupyterLab already exists | Upstream reports BSD-3-Clause; no Spark pin adopted | `exclude` | Time-box compatibility and credential-transport validation later; do not block the working nbconvert path or invent a custom cell protocol. | Deferred |
| OpenScience prompts and scientific behavior | Selected prompts, Agent roster behavior, and independently written Skills are tracked in `OPENSCIENCE_PARITY.md` | Apache-2.0 at recorded revision `e9844a49f1f4d93cbf5f88b8f4880c003adc6e61` | `copy-and-adapt` | Retain notices and exact source references; exclude its server, SolidJS UI, SDK, billing, wallet, cloud catalog, and managed models. | Active, partial parity tracked separately |
| `shawliu998/Vera` product/release patterns | User-owned reference for onboarding, provider tests, restart, packaging, and release output | Copy permission not established for this batch; task explicitly requires behavior-only use | `behavior-only` | Reproduce test/product behavior independently; do not copy Vera/Mike-derived code. | Reference only |
| `shawliu998/contextdelta` evaluation patterns | User-owned reference for tool-event, real-provider, end-state, restart, and stale-session tests | Not yet verified in Spark | `behavior-only` | Adapt test cases and assertions without taking a runtime dependency. | Reference only |
| `shawliu998/Dart` demo patterns | User-owned reference for one-click demo, fixtures, acceptance report, and reproducibility | Not yet verified in Spark | `behavior-only` | Adapt deterministic demo structure into existing Spark examples and test commands. | Reference only |
| Living Research Notebook | `.spark/lab-notebook.jsonl` parser, viewer, filters, exports, and parent-Agent guidance | Spark-owned MIT | `implement-missing-gap` | Treat the append-only file as a workspace artifact; do not add a database, scheduler, MCP, or hidden-reasoning store. | Implemented in this batch |

## Default-path coupling audit

- `LiveSessionPage.tsx` imports `generateTaskPlan` only for the explicitly opened
  Manual Parallel Tasks surface. The ordinary Composer path calls `sendPrompt`
  directly and does not create a deterministic plan or synthesis session.
- `taskPlanning.ts` remains a compatibility helper for editable manual batches.
- `modelRouting.ts` no longer matches prompt text, provider brands, Luna, Terra,
  Sol, Kimi, Claude, or GPT. The parent turn keeps the selected/default model.

Removing the compatibility files would add migration risk without changing the
default Agent path, so they remain present but are no longer extended.
