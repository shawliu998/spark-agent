# OpenScience parity

This document is the single long-lived audit and implementation matrix for
adapting useful OpenScience behavior into Spark Agent. It is not a claim that
the products are identical.

## Audit basis and rules

- Upstream: `synthetic-sciences/openscience` at
  `e9844a49f1f4d93cbf5f88b8f4880c003adc6e61` (2026-07-11).
- OpenScience code is Apache-2.0. Spark also retains MIT-licensed code inherited
  from Open Science Desktop. Copied or adapted files must retain the applicable
  notices; behavior-only reimplementations remain Spark-owned.
- Spark keeps its Tauri + React desktop and bundled OpenCode runtime. It does
  not import the OpenScience Bun/Hono runtime, SolidJS UI, SDK, cloud catalog,
  Atlas implementation, trademarks, or brand assets.
- A capability may be marked `parity` only when the listed automated test or a
  recorded repeatable smoke test demonstrates the relevant behavior.
- Allowed status values are `not-started`, `in-progress`, `partial`, `parity`,
  and `excluded`.

Reuse labels below mean: `adapted` for source-derived content changed for
Spark/OpenCode; `behavior-only` for an independent implementation of observed
behavior; `copied` for materially unchanged licensed content; and `excluded`
for deliberately unported code.

## Product, runtime, and workspace matrix

| Capability | OpenScience source | Spark target / existing support | Reuse / license | Decision and owner | Phase | Status | Tests |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Product positioning | `README.md`, workspace copy | `README.md`, `AGENTS.md`, product copy | behavior-only / Spark | General research is the default; verified workflows are optional. Product | Foundation | parity | Copy assertions and desktop tests |
| General research agent | `backend/cli/src/agent/agent.ts`, `prompt/research.txt` | App-private OpenCode profile + desktop session | adapted / Apache-2.0 | OpenCode owns the session and loop. Runtime | Foundation | partial | Profile, SDK, desktop tests, deterministic live agent/tool smoke |
| Agent roster | `backend/cli/src/agent/**` | `runtime/opencode-profile/agents/` | adapted / Apache-2.0 | Ship a runtime-loaded roster in pinned OpenCode format. Runtime | Foundation | parity | Profile fixture, live discovery, `listAgents`, grouped picker tests |
| Sub-agent delegation | native agents in `agent.ts` | OpenCode agent/task events | behavior-only / Spark | Use OpenCode delegation; do not add a second scheduler. Runtime | PR 2 | partial | SDK event normalization tests |
| Research workflow | `prompt/research.txt` | Research agent prompt and workspace | adapted / Apache-2.0 | Preserve iterative scope/reason/compute/analyze/write behavior. Runtime | Foundation | partial | Prompt contract + deterministic vertical smoke |
| Sessions | `backend/cli/src/session/**` | OpenCode sessions through `OpenCodeClient` | behavior-only / Spark | OpenCode is canonical; persist its app-private data directory. SDK | Foundation | partial | Session restore desktop test |
| Context compaction | `prompt/compaction.txt`, session runtime | OpenCode native context handling | excluded / OpenCode | Do not fork or duplicate OpenCode compaction. Runtime | Foundation | excluded | OpenCode boundary assertion |
| Planning | plan agent in `agent.ts` | OpenCode `plan` agent | adapted / Apache-2.0 | Read-only planning is selectable; no science-core gate in General mode. Runtime | Foundation | parity | Agent profile and fail-closed permission tests |
| Todo | OpenScience todo tools | OpenCode native tools/events | behavior-only / OpenCode | Render native events when exposed; no duplicate store. Desktop | PR 7 | partial | Event rendering tests |
| Model providers | `backend/cli/src/provider/**` | OpenCode provider APIs + native credential custody | behavior-only / Spark | Keep providers model-agnostic. Simple API keys use the OS credential manager at rest; structured API and OAuth records remain explicitly partial. SDK/desktop | Foundation | partial | Provider/config preservation and credential migration tests |
| MCP | `backend/cli/src/mcp/**` | OpenCode MCP APIs | behavior-only / Spark | OpenCode owns MCP lifecycle and credentials. SDK | PR 8 | partial | Existing SDK MCP tests |
| Custom agents | config markdown loading | Project/global OpenCode agents | behavior-only / OpenCode | Surface only agents returned by runtime. Runtime | PR 8 | partial | Runtime-list desktop test |
| Custom commands | `backend/cli/src/command/**` | OpenCode command APIs and slash composer | behavior-only / Spark | Forward runtime commands with selected agent/model; do not hardcode a catalog. SDK | Foundation | partial | `listCommands`, selection payload, and invocation tests |
| Plugins | OpenScience plugin packages | OpenCode extension boundaries | excluded / OpenCode | No OpenScience plugin runtime import. Runtime | PR 8 | not-started | None |
| Local research memory | learned skills / Atlas graph | Local files, sessions, provenance | behavior-only / Spark | Keep local; Atlas cloud/private implementation is excluded. Product | PR 8 | partial | Existing persistence tests |
| Cloud compute | `skills/cloud-compute/**` | Optional skills/backends | behavior-only / third party | Add per-provider integrations only with explicit credentials/terms. Skills | PR 8 | partial | Existing remote-run guards |

## Skills and tool matrix

| Capability | OpenScience source | Spark target / existing support | Reuse / license | Decision and owner | Phase | Status | Tests |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Skill discovery | `src/skill/skill.ts`, `docs/notes/skills.md` | Pinned OpenCode native discovery | behavior-only / OpenCode | Deploy bundled skills app-privately and avoid duplicate names so project/user entries win deterministically. Runtime | Foundation | partial | Deployment, ownership, and live project-precedence tests |
| Skill loading | `src/tool/skill.ts` | OpenCode native skill tool | behavior-only / OpenCode | Do not create a parallel executor. Runtime | Foundation | partial | `listSkills` + live `skill` tool invocation |
| Skill authoring | `skill new/validate` | Compatible `SKILL.md` files | behavior-only / Spark | Document and expose later without a new format. Skills | PR 3 | partial | Frontmatter fixture tests |
| Skill installation | `src/skill/install/**` | Future Git pack installer into app-private profile | behavior-only / Spark | Validate source, license, names, and precedence. Skills | PR 3 | not-started | None |
| Foundation scientific skills | `backend/cli/skills/{research,coding,writing,visualization}/**` | `runtime/skills/` | behavior-only / MIT | Start with eight independently written, auditable research/data/writing skills; upstream paths document behavioral references only. Skills | Foundation | parity | Manifest/content tests + pinned OpenCode discovery/invocation |
| Web search | agent tool permissions | OpenCode web/MCP tools when configured | behavior-only / OpenCode | Ask for manual approval on every OpenCode web fetch/search request; configured MCP services retain their own approval boundary. Runtime | PR 4 | partial | Foundation profile contract |
| File tools | agent tool permissions | OpenCode workspace tools | behavior-only / OpenCode | General file tools deny outside-workspace access; patch application asks, while ordinary workspace edits remain available. Runtime | Foundation | partial | Profile contract + Python/artifact vertical smoke |
| Shell | agent tool permissions | OpenCode shell tool | behavior-only / OpenCode | Every General shell command requires manual approval; Verified retains its separate strict approvals. This is an approval boundary, not process isolation. Runtime | Foundation | partial | Profile contract + live one-time bash approval smoke |
| Python | research prompt + coding skills | Workspace Python through OpenCode | behavior-only / Spark | General scripts run without Docker only after Shell approval; hard arbitrary-code isolation and broader environment/dependency parity remain open. Runtime | Foundation | partial | Live Research Agent approved bash + CSV/PNG smoke |
| Notebook | `src/science/kernel/**`, notebook UI | Persistent local Python/R kernels plus verified science-runtime | behavior-only / Spark | Keep current local and verified paths; add a fully isolated general sandbox later. Runtime | PR 5 | partial | Kernel, notebook UI, and science-runtime tests |
| R | skill/runtime references | Persistent local R kernel and notebook UI | behavior-only / Spark | Preserve current R execution; add environment management and sandbox parity later. Runtime | PR 5 | partial | Rust kernel and R notebook UI tests |
| Scientific databases | `src/science/connectors/**` | Curated paper-search, BioMCP, Materials Project, FRED, and related MCP setup | behavior-only / Spark | Keep shipped connectors optional; add the unified typed result contract and missing OpenScience sources incrementally. Connectors | PR 4/6 | partial | Connector config/setup tests |

## Rendering, writing, and verification matrix

| Capability | OpenScience source | Spark target / existing support | Reuse / license | Decision and owner | Phase | Status | Tests |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Artifact rendering | workspace artifact/file components | React artifact dock + workspace scan | behavior-only / Spark | Discover generated files without requiring science-core registration. Desktop | Foundation | in-progress | CSV/PNG discovery + restart tests |
| Plot rendering | workspace plot/file components | Existing image artifact preview | behavior-only / Spark | Render workspace PNG/SVG with safe local URLs. Desktop | Foundation | in-progress | PNG artifact test |
| Citation rendering | citation UI and literature tools | Existing literature/evidence views | behavior-only / Spark | General citations are artifacts; Verified keeps bound evidence. Desktop | PR 4 | partial | Existing literature tests |
| Molecule rendering | workspace viewers, chemistry skills | `MoleculeView` routes MOL/SDF/SMILES and 3Dmol-compatible formats | behavior-only / third party | Preserve the existing viewer and expand metadata/format QA later. Desktop | PR 6 | partial | Molecule routing, rendering, and i18n tests |
| Protein structure rendering | workspace viewers, protein connectors | `MoleculeView` renders PDB/mmCIF/macromolecule files with 3Dmol | behavior-only / third party | Existing viewing is useful but does not yet match a dedicated protein workflow. Desktop | PR 6 | partial | PDB/macromolecule viewer tests |
| Genome rendering | workspace viewers, genomics connectors | `GenomeView` routes BED/GFF/VCF-style files | behavior-only / third party | Preserve the current viewer; richer IGV-style navigation remains later work. Desktop | PR 6 | partial | Genome routing, rendering, and i18n tests |
| Scientific writing | `prompt/write.txt`, `skills/writing/**` | Write agent + scientific-writing skill | adapted / Apache-2.0 | Keep claims, citations, limitations, and uncertainty explicit. Skills | Foundation | in-progress | Agent/skill profile tests |
| Structured workflows | OpenScience provenance/review behavior | Existing science-core workflows | Spark-owned | Keep and relabel as optional Verified surfaces. Science core | Foundation | partial | Existing core/desktop/integration tests |
| Verified execution | verification notes and provenance behavior | AnalysisSpec compiler + science-runtime + Reviewer | Spark-owned | science-core remains canonical only for this mode. Science core | Foundation | partial | Existing runtime and Docker integration tests |

## Foundation acceptance trail

The Foundation slice is complete only when one repeatable run demonstrates:

```text
Create project -> create/restore OpenCode session -> select research agent
-> load a bundled skill -> write and run Python -> discover CSV and PNG artifacts
-> restart the desktop state -> recover the session and artifacts
```

General Research must complete this path without Docker or science-core. The
existing Verified Dataset Analysis smoke remains a separate regression gate.

Known Foundation limits are explicit: the credential-free live smoke uses a
deterministic loopback model double while exercising the real pinned OpenCode
Research Agent and `skill`/`write`/`bash` loop; it does not validate an external
paid provider or a packaged desktop-process restart. Skill source labels cannot
yet distinguish app-bundled from user-global entries returned from the same
OpenCode directory. The smoke observes and grants the real bash permission once,
but manual approval is not workspace confinement; hard arbitrary-code isolation
remains open. Simple provider API keys are migrated from OpenCode files to the OS
credential manager and referenced through runtime-only environment placeholders.
That is at-rest protection, not execution-time isolation: an approved local tool
can inherit the sidecar environment. Structured provider API records are rejected
until they can be migrated without losing metadata, while OAuth records,
scientific-connector keys, and the persistent Jupyter token still use owner-only
app-private files. Foundation must not be declared complete while those
non-negotiable custody and isolation gaps remain.
