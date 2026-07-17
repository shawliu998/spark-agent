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
| Sub-agent delegation | native agents in `agent.ts` | OpenCode agent/task events | behavior-only / Spark | Use OpenCode delegation; do not add a second scheduler. Runtime | Foundation | parity | SDK normalization plus live task-child creation, one-time child approval, result return, and restart-lineage smoke |
| Research workflow | `prompt/research.txt` | Research agent prompt and workspace | adapted / Apache-2.0 | Preserve iterative scope/reason/compute/analyze/write behavior. Runtime | Foundation | partial | Prompt contract + deterministic vertical smoke |
| Sessions | `backend/cli/src/session/**` | OpenCode sessions through `OpenCodeClient` | behavior-only / Spark | OpenCode is canonical; persist its app-private data directory. SDK | Foundation | partial | Session restore desktop test |
| Context compaction | `prompt/compaction.txt`, session runtime | OpenCode native context handling | excluded / OpenCode | Do not fork or duplicate OpenCode compaction. Runtime | Foundation | excluded | OpenCode boundary assertion |
| Planning | plan agent in `agent.ts` | OpenCode `plan` agent | adapted / Apache-2.0 | Read-only planning is selectable; no science-core gate in General mode. Runtime | Foundation | parity | Agent profile and fail-closed permission tests |
| Todo | OpenScience todo tools | OpenCode native tools/events | behavior-only / OpenCode | Render native events when exposed; no duplicate store. Desktop | PR 7 | partial | Event rendering tests |
| Model providers | `backend/cli/src/provider/**` | OpenCode provider APIs + native credential custody | behavior-only / Spark | Keep providers model-agnostic. Simple API keys use the OS credential manager at rest; structured API and OAuth records remain explicitly partial. SDK/desktop | Foundation | partial | Provider/config preservation and credential migration tests |
| MCP | `backend/cli/src/mcp/**` | OpenCode MCP APIs + native curated-key custody | behavior-only / Spark | OpenCode owns MCP lifecycle. Materials/FRED migration and private-broker infrastructure ship, and the legacy DYLD-sensitive launcher is removed. Their managed entry is disabled and uses only Apple platform-signed `/usr/bin/nc -U` to a private socket; credential-bearing execution fails closed and remains security-gated. App-managed Jupyter is available for local UI/kernel use, but agent Jupyter MCP is also security-gated: native startup reconciliation scrubs legacy plaintext Spark-owned config and refuses managed registration. The broker identity/config/target checks are staged defenses, not a delivered key-delivery guarantee. Custom/BYO MCP custody remains outside this boundary. SDK/desktop | Foundation | partial | Migration, reconciliation, default-denial, and broker tests now; release requires the P0 target-integrity/native-approval/config-dependency gate plus P1 hashed-atomic-install and packaged-macOS-E2E gates |
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
| Notebook | `src/science/kernel/**`, notebook UI | Persistent local Python/R kernels, app-managed JupyterLab, plus verified science-runtime | behavior-only / Spark | Keep current local and verified paths. An exposed plaintext v1 Jupyter token is rotated and its fresh replacement is stored in the OS credential manager; v2 metadata and renderer status are secretless, startup argv omits the token, token-bearing runtime files are suppressed/scrubbed, readiness verifies child listener ownership without a credential, and macOS URL opening is native. Agent Jupyter MCP remains fail-closed pending its secure broker and release gates; add a fully isolated general sandbox later. Runtime | PR 5 | partial | Credential rotation/conflict, metadata/IPC/argv, native pre-spawn/config scrub, runtime-artifact, readiness, path-validation, real local Lab, and kernel/science-runtime tests |
| R | skill/runtime references | Persistent local R kernel and notebook UI | behavior-only / Spark | Preserve current R execution; add environment management and sandbox parity later. Runtime | PR 5 | partial | Rust kernel and R notebook UI tests |
| Scientific databases | `src/science/connectors/**` | Curated paper-search, BioMCP, Materials Project, FRED, and related MCP setup | behavior-only / Spark | Keep credential-free shipped connectors optional; Materials/FRED stay visible but security-gated until credential-execution release gates pass. Add the unified typed result contract and missing OpenScience sources incrementally. Connectors | PR 4/6 | partial | Connector config/setup and fail-closed keyed-connector tests |

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
same live run must also create a real task-child session, route its one-time
approval, return its artifact to the parent, and preserve that lineage over a
sidecar restart. The existing Verified Dataset Analysis smoke remains a
separate regression gate.

Known Foundation limits are explicit: the credential-free live smoke uses a
deterministic loopback model double while exercising the real pinned OpenCode
Research Agent and `skill`/`apply_patch`/`bash`/`task` loops; it does not validate
an external paid provider or a packaged desktop-process restart. Skill source
labels cannot yet distinguish app-bundled from user-global entries returned from
the same OpenCode directory. The smoke observes and grants the real main-agent
bash permission once and two child edit requests once each, but manual approval
is not workspace confinement; hard arbitrary-code isolation remains open. Simple
provider API keys are migrated from OpenCode files to the OS credential manager,
referenced through runtime-only environment placeholders, and supplied to the
sidecar at launch. An approved local tool can still inherit those provider keys.
Spark-managed Materials Project/FRED keys use separate credential-manager items.
Migration and private-broker infrastructure are implemented, and the legacy
DYLD-sensitive Spark launcher is removed. Their disabled native MCP config contains
only Apple platform-signed `/usr/bin/nc -U` to a private Unix-domain socket. The
staged Tauri broker authenticates relay UID/PID/executable/parent against the owned
OpenCode PID/start-time/generation and validates the strict app config and canonical
target. Credential-bearing execution is disabled by default and fails closed, so
MP/FRED remain security-gated and unavailable to the runtime. These checks are
staged defense in depth, not a delivered assertion that the key path is uncrossable
or the downloaded target is confined. One P0 release gate requires immutable
signed/verified targets or same-UID-resistant isolated execution, native approval
for each broker call, and closure of the OpenCode config-dependency approval bypass.
Two P1 gates require a fully hashed transitive lock with staged atomic install and
packaged macOS E2E. Structured provider API records are rejected until they can be
migrated without losing metadata. OAuth records still use an owner-only app-private
file. Spark now rotates an exposed plaintext v1 Jupyter token and stores the fresh
replacement in the OS credential manager; v2 metadata contains only version and port,
renderer IPC omits token/URL/command material, startup argv omits the token, known
token-bearing runtime files are suppressed and scrubbed, readiness sends no token,
and native macOS URL
opening avoids a helper argument. Agent Jupyter MCP remains security-gated and
unavailable until a secretless native broker and the same target-integrity,
per-call-approval, config-dependency, hashed-atomic-install, and packaged-E2E gates
pass. The child startup environment, browser token URL/history, same-UID listener
races/introspection, and execution-time isolation remain explicit limits. Custom/BYO MCP
custody is outside the verified boundary. Foundation must not be declared complete
while those non-negotiable custody and isolation gaps remain.
