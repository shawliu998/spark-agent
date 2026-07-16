# Spark Agent Desktop — Technical Design

> **Implementation status (Foundation, 2026-07-16).** The Tauri/React desktop
> provides two coordinated but independent product surfaces. OpenCode is the
> canonical runtime for default General Research sessions, turns, agents, skills,
> tools, providers, permissions, MCP, and sub-agents. `science-core` is canonical
> only for optional Structured / Verified Workflows and their strict persisted
> state. General Research does not depend on Docker or the Python services.

Current control paths:

```text
General Research (default)
  Desktop → @ai4s/sdk OpenCodeClient → app-private bundled OpenCode
          → session → selected agent/model → skills/tools/sub-agents
          → workspace files and executable research artifacts

Structured / Verified Workflows (optional)
  Desktop → @spark/research-sdk → science-core Workflow API
          → SQLite state machine + leased jobs + approvals
          → hash-bound evidence / deterministic compiler
          → isolated science-runtime + Reviewer + frozen result
```

The internal launcher binds science-core to a dynamically allocated loopback port,
generates an ephemeral credential, migrates/backs up the database with Alembic, and
passes connection details to the Verified UI. Optional model credentials are stored in macOS
Keychain and mounted into science-core as a runtime-only Compose secret, never as a
container environment variable. This state is never duplicated for an ordinary
OpenCode session; the two control planes are presented together without requiring
cross-control-plane consistency.

## 1. Technical goals

A high-performance, open-source, local-first AI workbench for scientific research
with macOS / Windows installers. Design priorities: open-ended research through
one agent runtime; local and sandboxed execution; pluggable models, MCP, skills,
agents, and workflows; artifact discovery; optional verification; and extensibility.

## 2. Overall architecture

```text
Spark Agent Desktop
├── Desktop Shell: Tauri 2
├── Frontend: React + TypeScript + Vite
├── UI System: Tailwind CSS + Radix UI / shadcn-style components
├── General Research (default)
│   ├── packages/sdk OpenCodeClient → bundled OpenCode HTTP + SSE
│   ├── OpenCode-owned sessions, agents, skills, tools, MCP, and providers
│   └── Local workspace files and runtime-discovered artifacts
├── Sandbox Research (optional constrained backend)
├── Structured / Verified Workflows (optional)
│   └── @spark/research-sdk → science-core → science-runtime / Reviewer
├── Storage: OpenCode app data + workspace + Verified SQLite/JSONL
└── Packaging: Tauri DMG / APP / NSIS / MSI
```

## 3. Tauri over Electron

### 3.1 Recommendation

v1 uses **Tauri 2 + React + TypeScript + Vite**. Not Electron.

Reasons: Tauri is lighter with smaller installers; it uses the OS-native WebView,
suited to tool-type desktop apps; it is cross-platform (macOS / Windows / Linux); it
allows any frontend framework; and a Rust backend is well-suited to local files,
security, process management, and sidecar orchestration. Tauri positions itself around
small, fast, secure cross-platform apps built from a single codebase.

### 3.2 When Electron might fit

If later needs arise — complex browser capabilities, a more mature desktop ecosystem,
identical embedded Chromium behavior, or many native Node.js modules — Electron could be
reconsidered. But Spark Agent's core is the workbench, files, agent, runtime, and
artifacts, which do not need Chromium-level capabilities, so Tauri fits better.

## 4. Frontend

### 4.1 Stack

React · TypeScript · Vite · Tailwind CSS · Radix UI · TanStack Query · Zustand ·
React Router · Monaco Editor · Markdown renderer · ECharts / Plotly / Observable Plot.

### 4.2 Module layout

```text
src/
  app/{routes,layout,providers}
  components/{sidebar,topbar,command-palette,cards,artifact-viewer,
             approval-dialog,tool-call-card,code-viewer,markdown-viewer}
  features/{onboarding,projects,chat,agent-runtime,literature,artifacts,
            provenance,review,skills,settings}
  lib/{api,events,store,theme}
```

### 4.3 UI performance strategy

Streaming chat render; virtualized log lists; lazy file tree; paginated CSV; chunked
large-Markdown render; on-demand figures; cached artifact previews; a unified agent
event bus; all heavy work off to sidecar / worker; the Tauri main process does system
capabilities only, not heavy computation.

## 5. Agent runtime

### 5.1 Choice: OpenCode (bundled)

The agent runtime is **OpenCode** (`anomalyco/opencode`, MIT), pinned to a stable
release (`OPENCODE_VERSION`, currently 1.17.13). It is distributed as a **single
binary**, which makes it ideal to bundle as a desktop sidecar — no Python/Node runtime
to package. It supports MCP, skills, and agents, is model-agnostic (BYOK), and serves as
an open-source coding/agent runtime in the spirit of Claude Code.

OpenCode exposes an HTTP + SSE server (`opencode serve`) for sessions, prompts,
streaming output, skills, agents, commands, providers, permissions, MCP, and
sub-agent activity. Spark extends this boundary instead of adding another runtime.

### 5.2 Desktop ↔ OpenCode communication

The app talks to OpenCode over its HTTP + SSE API, wrapped by `packages/sdk`
(`OpenCodeClient`). Key endpoints:

| Endpoint | Use |
| --- | --- |
| `POST /session` · `GET /session` | Create / list sessions (conversation history) |
| `GET /session/:id/message` | Load a session's history |
| `POST /session/:id/prompt_async` | Send a prompt with selected agent/model |
| `GET /event` (SSE) | Stream `message.part.updated` (text/tool), `session.idle`, `session.error` |
| `GET /skill` · `GET /agent` | Real loaded skills / agents |
| `GET /command` | Runtime slash commands |
| provider/config APIs | Real providers, models, and defaults |

Flow:

```text
App launch → Rust starts the bundled `opencode serve` (dedicated free port)
↓
OpenCodeClient opens GET /event (SSE) and creates/loads sessions
↓
Prompt → POST /session/:id/prompt_async
↓
SSE streams message.part.updated / session.idle → folded into thread blocks by part/call id
↓
Frontend renders streaming messages, tool cards, and per-session history
```

### 5.3 Bundling & isolation (no interference)

OpenCode is bundled as a Tauri **sidecar** (`externalBin`, one binary per target triple,
git-ignored and fetched by `scripts/dev/fetch-opencode.sh`). The Rust side
(`src-tauri/src/runtime.rs`) starts it so it never collides with a user's own OpenCode:

- runs the **bundled** binary (not the user's `PATH`);
- on a **dedicated free port** (not the default 4096);
- with an **app-private** config/data dir via `XDG_CONFIG_HOME`/`XDG_DATA_HOME` under
  `~/Library/Application Support/io.github.shawliu998.sparkagent/runtime/` (macOS) — so the user's
  sessions/config are never touched;
- it can **explicitly import the user's login** from Settings: Spark parses the
  selected `auth.json` in memory, stores simple provider API keys in the OS
  credential manager, writes only environment references for those keys, and
  copies only sanitized non-API records into its owner-only auth file. Unsupported
  API records with metadata fail closed. The source file and user sessions remain
  untouched, and no import occurs silently at startup.
- killed on app exit.

The runtime manager idempotently deploys Spark agents and skills into the
app-private profile and merges Spark defaults without replacing existing provider
or user fields. Settings sends simple provider API keys through native Rust
commands that store them in the OS credential manager and persist only environment
references. Those keys are supplied to the OpenCode sidecar at launch. Settings
sends allowlisted Materials Project/FRED keys to separate credential-manager
items. Migration and private-broker infrastructure are implemented, but
credential-bearing execution is disabled by default and fails closed. Migration
canonicalizes Spark-owned entries to a disabled, secretless command consisting
only of Apple platform-signed `/usr/bin/nc -U <private-socket>`. This removes the
legacy DYLD-sensitive Spark launcher from the OpenCode process tree. The staged
broker in the already-running Tauri process authenticates relay UID, PID,
executable, and parent against the currently owned OpenCode PID, start time, and
generation, then validates the strict app config and canonical target. The
credential-release/spawn branch is not enabled in production, and Settings marks
MP/FRED security-gated; these connectors are not available to the runtime while
the gate is closed. The nc/private-UDS/PID-generation design is defense in depth,
not a delivered assertion that a key cannot cross a boundary or that a downloaded
target is confined. OpenCode owns OAuth login, after which Spark finalizes any
simple API record. The legacy
`configure_opencode` bridge uses the same native custody path. Neither path touches
the user's global OpenCode config. OAuth records remain in an owner-only
app-private auth file, and the persistent Jupyter token remains in an owner-only
app-private metadata file. Provider keys can still be inherited by an explicitly
approved local tool in the sidecar process tree; broader execution isolation and
hard confinement remain open.

Credential-bearing connector release has one P0 and two P1 gates:

- **P0 — execution authority and target integrity:** make downloaded targets
  immutable and signed/verified, or run them in an isolation boundary that a
  same-UID OpenCode extension cannot mutate or replace; require native approval
  for every broker call and gate or disable OpenCode config-directory dependency
  installation, which currently occurs before tool approval.
- **P1 — supply-chain installation:** replace the current exact top-level pins
  with a fully hashed transitive lock and staged atomic installation. Clearing the
  caller environment, disabling uv configuration, and fixing official PyPI remain
  useful subordinate controls.
- **P1 — packaged validation:** pass packaged macOS E2E covering migration,
  fail-closed denial, relay lineage and revocation, target verification, atomic
  install, and restart.

## 6. Skills & MCP

### 6.1 Skill layering

```text
runtime/skills/
  core/       # Spark-authored helpers and Foundation scientific skills

app-private OpenCode profile
  agents/     # Spark roster in pinned OpenCode markdown format
  skills/     # idempotently deployed bundles

project workspace
  .opencode/{agent,agents,skill,skills}/ # project extensions
```

### 6.2 v1 built-in skills

| Skill | Purpose |
| --- | --- |
| `literature-review` | Search, screen, synthesize, and report literature |
| `citation-management` | Verify and manage traceable citations |
| `hypothesis-generation` | Form testable hypotheses from gaps and observations |
| `scientific-critical-thinking` | Challenge assumptions, bias, leakage, and claims |
| `exploratory-data-analysis` | Profile and visualize data before inference |
| `statistical-analysis` | Select, check, execute, and report statistical methods |
| `scientific-writing` | Write evidence-calibrated scientific outputs |
| `matplotlib` | Produce reproducible publication figures |

### 6.3 Third-party skills

`K-Dense-AI/scientific-agent-skills` (large set; compatible with Cursor, Claude Code,
Codex, OpenCode) can be added later. Do **not** enable all ~148 skills by default: use
curated install, enable by domain, and show license, dependencies, and risk. (Curated
third-party install is a later feature; today the Skills page lists the real skills
OpenCode has loaded — built-in + project `.opencode/skill/` + user config.)

### 6.4 MCP servers

First batch: `filesystem` (project files), `paper-search-mcp` (literature), `BioMCP`
(biomedical databases), `Zotero MCP` (library), `GitHub MCP` (repos/issues/releases),
`local runtime MCP` (execution status). v1 ships filesystem + paper search first;
BioMCP and Zotero follow.

## 7. Execution layer

```text
Execution Layer
├── OpenCode tools (local, in the bundled runtime)
├── Docker sandbox            (optional, advanced)
├── SSH / Modal remote        (optional, advanced — later)
└── Jupyter Kernel Gateway    (later)
```

OpenCode executes its tools locally within the bundled runtime, gated by its permission
system. Heavier/remote execution (Docker sandbox, SSH, Modal) is optional and belongs in
an advanced "Remote Compute" area, never the default path.

**Default:** local execution + Spark's OpenCode manual-approval profile. Do not
hard-depend on Docker Desktop or WSL in v1 — that raises the install barrier and is not
consumer-grade.

**v0.3 Jupyter Kernel Gateway** for a more notebook-like experience:

```text
Desktop App → Local Runtime Manager → Jupyter Kernel Gateway → Python / R kernel
→ stream output / figures / tables
```

Jupyter Kernel Gateway is a headless Jupyter kernel server addressable over REST /
WebSocket.

## 8. Local Runtime Manager

### 8.1 Why

The installer should not bundle every scientific dependency (huge installer, slow
updates, cross-platform pain, dependency conflicts, hard debugging). Instead: a
lightweight installer + a first-launch Runtime Manager + on-demand scientific env.

### 8.2 Responsibilities

Detect OpenCode; detect Python / uv / Node / Git; create the workspace; create isolated
environments; install base Python packages; manage scientific tool dependencies; start
the OpenCode server; start an optional Jupyter Gateway; monitor runtime health.

### 8.3 Runtime directory

```text
macOS app data:
  ~/Library/Application Support/io.github.shawliu998.sparkagent/runtime/
    xdg-config/  xdg-data/  xdg-cache/  xdg-state/

default research workspaces:
  ~/Documents/SparkAgent/<dated-session>/
```

Tauri resolves the corresponding per-user app-data directory on Windows. The
workspace base is user-selectable; the active workspace path is persisted under
the app-private runtime root.

## 9. Storage

### 9.1 Project structure

```text
workspace/
  project.json  plan.md
  data/{raw,processed}/  papers/  parsed/  scripts/  notebooks/
  figures/  reports/  artifacts/  reviews/
  provenance.jsonl  manifest.json
```

### 9.2 SQLite

Stores: project list, session index, artifact index, literature metadata index,
tool-call state, user settings, runtime state.

### 9.3 JSONL

`provenance.jsonl` is an append-only execution record — easy to read, diff, recover,
export, and open-source friendly.

## 10. Artifact provenance

### 10.1 Manifest

```json
{
  "project_id": "bci-trends",
  "created_at": "",
  "artifacts": [
    {
      "id": "fig_year_trend",
      "type": "figure",
      "path": "figures/year_trend.png",
      "created_by_step": "step_004",
      "input_files": ["data/processed/corpus.csv"],
      "code_files": ["scripts/analyze.py"],
      "status": "reviewed"
    }
  ]
}
```

### 10.2 Provenance event

```json
{
  "event_id": "evt_001",
  "step_id": "step_004",
  "type": "code_execution",
  "tool": "python",
  "command": "python scripts/analyze.py",
  "input_files": ["data/processed/corpus.csv"],
  "output_files": ["figures/year_trend.png"],
  "started_at": "",
  "finished_at": "",
  "status": "success"
}
```

### 10.3 Verified Reviewer rules (v2, deterministic evidence integrity)

For literature workflows, every claim must belong to the workflow answer, retain an
exact sentence from a verified project-owned passage, and have a valid supporting
relationship, page location, and quote hash. The answer summary is reconstructed
deterministically from the ordered claim/evidence map; remote model output cannot
free-write it. Generation provider, model, prompt version, and approved endpoint
identity are provenance, not evidence strength.

This Reviewer does not establish scientific correctness, methodological quality, or
generalizability. Semantic and scientific review remain separate future layers.
Analysis artifact verification separately checks recorded files, provenance, source
data/code relationships, reproducibility metadata, containment, and content hashes.

## 11. Security

### 11.1 Default permissions

General Research uses a Spark-managed OpenCode profile. Ordinary reads inside the
workspace are allowed, while every workspace write/edit, Shell command, web
fetch/search, MCP action, and patch operation asks. Unknown concrete tool IDs
default to ask, and file-tool access outside the workspace is denied. The Composer
does not offer Full Access or persistent permission grants. Manual approval is not
an OS sandbox; Verified Workflows retain their stricter approvals.

### 11.2 Approval levels

| Action | Default |
| --- | --- |
| Read current project files | Allow |
| Workspace write/overwrite/patch (including possible deletion) | Ask |
| Any Shell command | Ask |
| Web fetch/search or MCP action | Ask |
| File-tool access outside workspace | Deny |

OpenCode has a per-tool permission system (allow / ask / deny per agent). The
desktop exposes only the safe manual profile and can remediate an old Full/custom
configuration without fabricating a second permission protocol. Verified
approvals remain separate domain objects.

### 11.3 API keys

Verified-workflow gateway credentials are stored in macOS Keychain and handed to
science-core through a bounded Compose secret. For General Research, simple
provider API keys are migrated to the OS credential manager; OpenCode config holds
only environment references, and the sidecar receives those keys at launch.
Spark-managed Materials Project/FRED keys use separate credential-manager items
and disabled, secretless native MCP config. Migration and the private broker are
implemented, and the legacy DYLD-sensitive Spark launcher is removed. The staged
command is Apple platform-signed `/usr/bin/nc -U <private-socket>`; the Tauri
broker binds relay identity to the currently owned OpenCode PID/start time/
generation and validates the strict config and canonical target.
Credential-bearing execution remains fail-closed and security-gated, so no
production claim is made that the key-delivery path is available, uncrossable, or
hard-confined.
Approved local tools can still inherit provider or other sidecar runtime secrets.
Structured provider API records fail closed instead of losing metadata. OAuth
records and the persistent Jupyter token still use owner-only app-private files
and remain open custody work; custom/BYO MCP credential custody is outside this
guarantee. Spark does not intentionally write secrets to workspace provenance,
git, crash reports, or exports, but execution-time redaction is not yet a hard
boundary. Before credential-bearing connectors can be enabled, the P0
target-integrity/native-approval/config-dependency gate and both P1 gates—a fully
hashed transitive lock with staged atomic install, and packaged macOS E2E—must
pass.
Public and LAN model endpoints require HTTPS, while plain HTTP is limited to
literal loopback destinations.

## 12. Packaging & release

### 12.1 macOS

Outputs use the Tauri product name, for example `Spark Agent_<version>_aarch64.dmg`
and `Spark Agent_<version>_x64.dmg`; a universal build can be added later. Code
signing / notarization needs an Apple
Developer account; a free account cannot notarize, so users may still see an
"unverified" prompt.

### 12.2 Windows

Outputs use the Spark Agent product name for NSIS `Setup.exe` and MSI artifacts.
Prefer the NSIS
`Setup.exe` in v1 for a familiar install experience. Unsigned apps run but may trigger
SmartScreen; formal release needs a code-signing certificate (EV certs earn SmartScreen
reputation faster). Early GitHub Release preview builds may be unsigned, but the README
must say so.

### 12.3 Auto update

Tauri updater with GitHub Releases + `latest.json` + a Tauri updater signature (update
packages must be signed; signature verification cannot be disabled). v0.1 no forced
auto-update; v0.2 adds a GitHub Releases updater; v0.3 adds in-app update prompts.

### 12.4 CI/CD

GitHub Actions build matrix:

```yaml
macos-latest:
  - aarch64-apple-darwin
  - x86_64-apple-darwin
windows-latest:
  - x86_64-pc-windows-msvc
```

The official Tauri GitHub Action builds native binaries for macOS / Linux / Windows and
uploads to a GitHub Release.

## 13. Process model

### 13.1 Startup

```text
User opens app → Tauri starts → Frontend loads → Runtime Manager checks dependencies
→ Start OpenCode sidecar → Connect to Gateway → Load projects → Ready
```

### 13.2 Agent task

```text
User submits goal → Frontend sends selected agent/model to OpenCode
→ Agent scopes and plans → loads skills → uses tools/sub-agents
→ writes and runs workspace code → inspects results and may revise the method
→ tool events stream back → bounded artifact scan updates the dock
→ final synthesis remains in the durable OpenCode session
```

Structured / Verified tasks follow the separate
`@spark/research-sdk → science-core → approval/compiler/runtime/reviewer` path.

## 14. High-performance design

### 14.1 UI

Layered state: UI state in Zustand, server/runtime state in TanStack Query, streaming
events in an event bus. Big-data optimizations: paginated CSV preview, virtualized log
viewer, lazy Markdown render, lazy artifact load. Render optimizations: memoized
tool-call cards, batched message chunks, `requestAnimationFrame` batching, background
task workers.

### 14.2 Runtime

Persistent OpenCode server; reused project sessions; incremental file index; artifact
hash cache; per-project reused Python env; literature metadata cache; cached PDF parse
results; figure preview thumbnails.

### 14.3 Startup targets

```text
App UI cold start: < 3s
Runtime ready: < 10s
First agent response: < 5s after runtime ready
```

Strategy: UI first, runtime after; show runtime-loading state on Home; a failed OpenCode
connection must not block the UI; first-time dependency install happens in onboarding.

## 15. Error handling

### 15.1 Runtime errors

OpenCode not started; Gateway start failure; port in use; missing API key; model
connection failure; workspace permission denied; broken Python env; Docker unavailable;
MCP server start failure. Each must provide: a human-readable explanation, collapsible
technical details, a one-click fix button, and a copy-logs button.

### 15.2 Agent errors

Tool-call failure; literature source rate-limited; dependency install failure; code run
failure; file permission failure; citation check failure. Must show: the failed step,
the cause, a fallback suggestion, a retry button, and an edit-plan button.

## 16. Repository structure

Monorepo:

```text
spark-agent/
  apps/desktop/{src,src-tauri}/
  packages/{ui,shared,sdk}/
  runtime/{manager,opencode-profile,mcp,skills}/
  docs/{PRD.md,TECHNICAL_DESIGN.md}
  examples/bci-trends/
  scripts/{release,dev}/     # dev/fetch-opencode.sh fetches the pinned sidecar
```

- `apps/desktop` — Tauri + React desktop app; `src-tauri/src/runtime.rs` supervises the
  bundled OpenCode sidecar (`OpenCodeClient` lives in `packages/sdk`).
- `runtime/manager` — local runtime manager (detect deps, workspace, provenance, logs).
- `runtime/opencode-profile` — the Spark Agent OpenCode config/skills bundle.
- `runtime/skills` — self-authored scientific skills.
- `examples` — the complete demo project.

## 17. v0.1 task breakdown

### 17.1 Day-one goals

1. Init Tauri + React.
2. Build the main layout.
3. Build a static onboarding page.
4. Build a static project workspace page.
5. Build tool-call card / artifact card / approval dialog.
6. Bundle + auto-start OpenCode; connect via `OpenCodeClient` (HTTP + SSE).
7. Ship the OpenCode config/skills bundle.
8. Write the 3 core skills.
9. Build static artifacts for the BCI demo.
10. Draft the GitHub Actions build.

### 17.2 v0.1 must deliver

macOS app runs; Windows app runs; README has screenshots; a complete demo; API key
config; open a workspace; a bundled OpenCode the app auto-starts and drives (sessions,
streaming, history, skills); show plan / tool / artifact / review; export `report.md`.

## 18. Technical risks

### 18.1 OpenCode desktop integration

Risk: OpenCode API changes across versions. Mitigation: wrap `OpenCodeClient`; never call
OpenCode directly from the UI; **pin the OpenCode version** (`OPENCODE_VERSION`); bundle
the pinned binary so the app is not affected by the user's own OpenCode.

### 18.2 Windows environment complexity

Risk: WebView2, permissions, Defender, SmartScreen, PATH, missing Python / Git / Node.
Mitigation: the Runtime Manager detects the environment; do not hard-depend on system
Python early; provide a portable fallback; code-sign for formal releases.

### 18.3 Installer size

Risk: bundling a large runtime and scientific packages makes the installer huge.
Mitigation: OpenCode is a single ~44 MB-installer sidecar (cheap to bundle); keep the app
body light; install heavy scientific dependencies on demand as optional Science Packs;
defer Docker / Jupyter.

### 18.4 Agent safety

Risk: the agent runs commands, reads/writes files, and accesses the network.
Mitigation: every General Shell/web/MCP/patch operation asks, outside-workspace
file-tool access is denied, Full Access is not selectable, and the product clearly
states that manual approval is not an OS sandbox.
Use the optional sandbox or Verified runtime when a hard isolation boundary is
required; record best-effort provenance without treating it as containment.

## 19. Final stack

```text
Tauri 2
React + TypeScript + Vite
Tailwind + Radix UI
OpenCode as agent runtime (bundled single-binary sidecar, pinned OPENCODE_VERSION)
OpenCode HTTP + SSE API via OpenCodeClient (packages/sdk)
OpenCode skills/agents + optional third-party scientific skills
Local workspace + SQLite + JSONL provenance
DMG / NSIS / MSI installers via GitHub Actions
GitHub Releases (self-contained; sidecar fetched at build time)
```

One line:

**Use Tauri for a high-performance modern desktop shell, a bundled+isolated OpenCode as
the Claude Code alternative layer, scientific skills and MCP as the research capability
layer, and provenance/reviewer as the real moat of an open-source Claude Science alternative.**
