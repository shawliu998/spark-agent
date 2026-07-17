# Spark Agent

**The open-source AI workbench for scientific research.**

Give it a research goal. It can read the literature, form hypotheses, write and
run code, execute experiments, analyze results, and write up what it finds.

Spark Agent is local-first and model-agnostic. General Research runs through a
bundled OpenCode sidecar in the current workspace; it does not require Docker or
the optional Python services. Researchers who need a stricter, auditable path
can choose a Verified Workflow with explicit approvals, isolated execution,
evidence binding, and deterministic review.

> Status: source-run Foundation build for macOS. The General Research vertical
> slice and existing Verified literature/dataset workflows run today. Simple
> provider API keys and Spark-managed Materials Project/FRED connector keys are
> protected at rest by the OS credential manager. MP/FRED migration and broker
> infrastructure are implemented, and the legacy DYLD-sensitive Spark launcher
> has been removed. Managed config now points only to Apple platform-signed
> `/usr/bin/nc -U` and a private Unix-domain socket; the staged Tauri broker binds
> relay identity to the owned OpenCode PID/start-time/generation and validates the
> strict config and canonical target. Credential-bearing execution is nevertheless
> disabled by default and fails closed, so MP/FRED remain security-gated rather
> than available to the runtime. Release requires one P0 gate—immutable,
> signed/verified downloaded targets or isolated execution, plus native per-call
> approval and closure of the OpenCode config-dependency bypass—and two P1 gates:
> a fully hashed transitive lock with staged atomic install, and packaged macOS
> E2E. The broker is staged defense in depth, not a delivered key-delivery or
> hard-confinement guarantee. Spark now replaces any legacy plaintext Jupyter
> token with a fresh credential stored in the OS credential manager; native startup and
> browser opening keep it out of renderer IPC and process arguments. Local
> JupyterLab and in-app kernels remain available, but agent Jupyter MCP access is
> security-gated and fails closed until the same broker, target-integrity,
> approval, supply-chain, and packaged-E2E gates pass. Structured provider
> records, OAuth, broader execution-time secret isolation, packaged-app restart
> E2E, installers, and broader connector parity remain in progress.

## What works today

- Create local projects and durable OpenCode research sessions.
- Select real agents, skills, commands, and models reported by the bundled
  runtime instead of a hardcoded desktop catalog.
- Choose general Research, Biology, Physics, or ML primary agents and delegate
  focused literature, critique, review, writing, exploration, and task work to
  runtime-defined sub-agents.
- Use the Research Agent to work iteratively with local files, Shell, and Python
  under OpenCode permissions, then discover generated tables, figures, scripts,
  reports, and notebooks in the artifact dock.
- Keep General Research sessions and workspace artifacts across app restarts.
- Combine papers and datasets in an open-ended General Research session without
  routing the task into one fixed workflow type.
- Load a foundation pack for literature review, citation management, hypothesis
  generation, scientific critique, exploratory and statistical analysis,
  scientific writing, and Matplotlib.
- Extend the runtime with project or user OpenCode agents, skills, MCP servers,
  commands, and model providers.
- Set up and open an app-managed local JupyterLab environment without exposing
  its authorization token to renderer state. Agent access to that environment is
  intentionally unavailable while its credential-bearing MCP path is security-gated.
- Enable optional credential-free curated scientific MCP connectors, including
  multi-source paper search (arXiv, PubMed, Crossref, and Semantic Scholar),
  BioMCP, space weather, Open-Meteo, and USGS water. Materials Project and FRED
  are visible but security-gated; unified connector-result parity remains in
  progress.

Optional Verified Workflows preserve the existing stricter capabilities:

- Start a durable Verified Literature Synthesis, inspect its
  typed three-step plan, and explicitly approve or cancel it.
- Start a Verified Dataset Analysis from an immutable CSV, approve its
  typed four-step plan and exact Python payload, then review verified run artifacts.
- Extract page-addressable evidence across local papers, build atomic claims, and
  require a deterministic evidence-integrity Reviewer pass before completion.
- Bind approved remote sources to their PDF and parsed-page hashes, then freeze
  the reviewed answer, ordered claims, citations, source identity, and result hash.
- Recover persisted workflow jobs after restart, with revision-checked retry,
  resume, cancel, and an auditable event timeline.
- Ask PaperQA-backed questions with explicit remote-data approval and clearly
  distinguish locally located quotations from unreviewed model claims.
- Inspect claims, exact evidence spans, citations, and source PDF pages.
- Import CSV datasets and prepare editable Python analyses.
- Review an immutable execution payload before approving a run.
- Execute with Jupyter in a no-network, resource-limited container.
- Save notebooks, logs, figures, tables, environment metadata, and hashes.
- Audit projects, sources, approvals, tasks, runs, artifacts, and events in SQLite.

## Architecture

```text
Spark Agent Desktop (Tauri + React)
  |-- General Research workspace (default)
  |     +-- packages/sdk OpenCodeClient
  |           +-- bundled OpenCode sidecar
  |                 +-- sessions, agents, skills, tools, MCP, providers
  |
  |-- Structured / Verified Workflows (optional)
  |     +-- evidence, approvals, review, and Analysis workspaces
  |
  +-- science-core (FastAPI + SQLite + PaperQA2)
        |-- canonical state only for Verified workflows
        |-- leased background worker + restart recovery
        |-- local evidence extraction + deterministic claim review
        |
        +-- Unix-domain socket
              |
              +-- science-runtime (Jupyter, no network, read-only root)
```

The UI accesses OpenCode only through `packages/sdk`. OpenCode is the canonical
runtime for General Research; `science-core` is the canonical control plane only
for Structured / Verified Workflows. The desktop shell reuses MIT-licensed Open
Science Desktop code and adapts selected Apache-2.0 OpenScience agents and skills.
See `docs/OPENSCIENCE_PARITY.md` and `THIRD_PARTY_NOTICES.md` for the precise
reuse boundary.

## Run General Research from source

Requirements:

- macOS with Node.js 20+ and pnpm 9
- Rust stable with the Tauri prerequisites
- a credential for the model provider you choose

```bash
git clone https://github.com/shawliu998/spark-agent.git
cd spark-agent
pnpm install --frozen-lockfile
bash scripts/dev/fetch-opencode.sh
bash scripts/dev/fetch-uv.sh
pnpm --filter @ai4s/desktop tauri dev
```

General Research starts the app-private OpenCode sidecar and works without
Docker, `science-core`, PaperQA, or `science-runtime`.

## Run optional Verified Workflows

Verified Workflows additionally require Docker Desktop or OrbStack:

```bash
pnpm mvp:dev
```

`mvp:dev` performs the Verified Workflow preflight, builds the two isolated services,
applies verified Alembic migrations, allocates an available loopback port, creates
an ephemeral 256-bit Bearer credential, waits for both services to become healthy,
and injects the URL and credential into the desktop web client. The credential is
not printed or placed in a URL. The first container build downloads the pinned
Python dependencies and can take several minutes; later starts reuse Docker's
cache.

To enable the current PaperQA model gateway, store its credential in macOS
Keychain. The secure prompt is handled by `security`; the launcher passes the key
only to Compose's top-level secret source while creating the service, then clears
its temporary variable. The key is not put in command arguments, persisted
outside Keychain, rendered into Compose
configuration, inherited by the desktop, placed in the service/container
environment, or written to logs:

```bash
pnpm model-key:set
pnpm model-key:status
```

Select the non-secret provider model before launch. Set a compatible API base
only when using a provider other than the default endpoint:

```bash
export SPARK_AGENT_LLM_MODEL='your-provider-model-id'
# Optional: export OPENAI_API_BASE='https://your-provider.example/v1'
# Optional: export SPARK_AGENT_EMBEDDING_MODEL='your-compatible-embedding-id'
pnpm mvp:dev
```

For this internal slice, both model variables are raw IDs accepted by the same
OpenAI-compatible endpoint. PaperQA-native local embedding profiles are not wired
to this launcher yet.

Public and LAN model endpoints must use HTTPS. Plain HTTP is accepted only for
the literal `localhost` host or a loopback IP address.

Delete the credential with `pnpm model-key:delete`. At launch, Spark Agent reads
the key into a non-exported variable and exposes it only as a Compose secret file
inside `science-core`. An inherited `OPENAI_API_KEY` causes startup to fail rather
than leaking the key to Vite or another child process; run `unset OPENAI_API_KEY`
before `pnpm mvp:dev` if an existing shell profile exports it.

Without a model key, deterministic Verified workflows, PDF import, CSV analysis,
and Jupyter execution remain available. A stored key also enables optional
remote-model-assisted planning and synthesis as well as PaperQA answering. Remote
workflows require approval of the research goal before planning and a second
approval of the source-bound plan before execution.

Stop the local services with:

```bash
pnpm science:down
```

Normally, pressing `Ctrl-C` in the `mvp:dev` terminal performs the same scoped
cleanup. Project data remains under `.local/science-core`; the ephemeral runtime
exchange and socket volumes are removed.

## Quality checks

Pull requests and pushes to `main` run independent Desktop, Rust, Science Core,
Science Runtime, migration, and Docker integration jobs. To run the same complete
gate locally, install Node.js 20 with pnpm 9, Python 3.12, the stable Rust
toolchain with `rustfmt` and `clippy`, and Docker, then prepare the workspace:

```bash
pnpm install --frozen-lockfile
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e './services/science-core[literature,dev]' \
  -e './services/science-runtime[dev]'
pnpm quality
```

Every check also has a focused root command: `lint:desktop`,
`typecheck:desktop`, `test:desktop`, `fmt:rust`, `lint:rust`, `test:rust`,
`lint:core`, `typecheck:core`, `test:core`, `lint:runtime`,
`typecheck:runtime`, `test:runtime`, and `test:integration`. Invoke one with
`pnpm <command>`. `test:release-assets` verifies the fail-closed SHA-256 policy
for every supported OpenCode and uv sidecar. Rust lint and test use a compile-only
Tauri override that omits packaged sidecars; release builds fetch pinned binaries
and verify their committed SHA-256 digests before unpacking them.

## Optional Verified Workflow boundary

- The optional durable orchestrator supports two coherent workflow types:
  `literature-synthesis` (inspect sources → extract local evidence → synthesize
  extractive claims → deterministic review) and `dataset-analysis` (profile an
  immutable CSV → prepare and approve exact Python → execute in science-runtime
  → verify artifacts and deterministic review).
- The default plan is deterministic and local. The optional model-assisted mode
  proposes a schema-validated plan and extractive claims, while the durable local
  workflow remains canonical and evidence verification stays fail-closed.
- The Reviewer verifies immutable result materialization, exact citation links,
  local quote location, and source-file/page fingerprints. It does not establish
  scientific correctness, methodological quality, entailment, or generalizability.
- Dataset analysis currently provides a deterministic descriptive baseline. Its
  Reviewer verifies input/run/artifact lineage and required output integrity, but
  does not prove causal validity or that a requested inferential method was used.
  Workflow execution accepts only the versioned baseline or bounded repair template
  AST selected by science-core, with the same contract checked again by science-runtime;
  standalone editable analyses retain their separate explicit-approval container policy.
- Workflow activity uses authenticated cursor polling (1.5 seconds while active),
  not SSE yet.
- These constraints do not apply to an ordinary General Research session. This
  remains a source-run build; Tauri-managed packaging of the optional Python
  services is a later release step.

## Safety defaults

- General Research requires manual approval before every workspace write/edit,
  Shell command, MCP action, and OpenCode web fetch/search request. Unknown
  concrete tool IDs ask, and file tools deny access outside the current
  workspace. Approval is not an OS sandbox: once approved, arbitrary code still
  runs with the signed-in user's authority, so hard isolation remains open.
- The Composer does not offer Full Access. A legacy Full Access or external
  custom OpenCode policy is shown as such and can be replaced with Spark's
  manual-approval default; effective project and agent rules are checked before
  each turn so they cannot silently weaken the runtime floor. Verified Workflows
  independently keep strict plan, execution, and remote-data approvals.
- OpenCode's config loader can still install config-directory dependencies
  outside the tool-permission flow. Gating or disabling that path until explicit
  approval is a release blocker.
- App-managed Jupyter metadata is versioned and secretless: `server.json` v2
  contains only its schema version and port. Any exposed legacy plaintext token
  is rotated, its replacement is stored in the OS credential manager before
  OpenCode starts, and legacy Spark-owned MCP
  entries containing Jupyter connection material are scrubbed fail-closed. The
  renderer receives only installed/running/registered booleans; native code starts
  JupyterLab with a child-only token environment variable and opens its tokenized
  URL through macOS NSWorkspace, never a helper command argument. A controlled
  ServerApp also suppresses token-bearing server-info/browser files; startup uses
  a credential-free child-listener ownership check. This removes Spark's plaintext
  metadata, renderer-IPC, launch-argument, and known runtime-file exposures, but does
  not make the secret uncrossable: the child startup environment, browser URL/history,
  same-UID listener races/process introspection, and execution-time isolation
  remain explicit limitations requiring packaged E2E evidence.
- A generated Bearer credential on every internal launch, a dynamically allocated
  loopback-only service port, and an allowlisted CORS boundary.
- No arbitrary direct shell mode in the product UI.
- Compare-and-set workflow revisions, idempotent workflow creation and enqueue
  guards, immutable plan/approval envelopes and frozen reviewed-result hashes,
  leased jobs, bounded recovery, and fail-closed Reviewer checks.
- Immutable, hashed analysis intents with compare-and-set approval decisions.
- Jupyter execution in a no-network container with a read-only root filesystem,
  resource limits, and a Unix-domain socket instead of a TCP runtime port.
- Artifact paths, regular files, source containment, and SHA-256 are verified
  before files are served or accepted.
- "Immutable" in this boundary means the content-addressed identity and its
  approved or reviewed database record. Workspace materializations remain local
  and user-editable, so every trusted read re-verifies SHA-256 and fails closed
  instead of treating the workspace file as WORM storage.

This is research software. Model answers and generated analyses must still be
reviewed before publication or consequential use.

## Upstream and maintenance model

This GitHub repository is independent and is not registered as a GitHub Fork.
Its source history is nevertheless derived from Open Science Desktop v0.1.9,
and selected research-agent behavior and content are adapted from OpenScience.
Upstreams are tracked by pinned revision so updates can be audited and integrated
module by module. Spark does not import the OpenScience runtime or UI monorepo.
Product-specific services and features stay separate where practical.

Internal `@ai4s/*` and Rust crate names are temporarily retained for source
compatibility. They are implementation details, not the Spark Agent brand.

## License and attribution

Inherited Open Science Desktop code is available under the MIT License; its
required original notice is retained in [LICENSE](./LICENSE). Adapted
OpenScience material is used under Apache-2.0 with its copyright and NOTICE
requirements retained alongside the affected source. See
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md) and
[docs/OPENSCIENCE_PARITY.md](./docs/OPENSCIENCE_PARITY.md) for the reuse map.

Spark Agent is maintained and distributed independently from Open Science
Desktop, OpenScience, and their maintainers.
