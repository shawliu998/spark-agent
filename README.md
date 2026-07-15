# Spark Agent

Spark Agent is a local-first, evidence-first research agent for macOS. It joins
papers, citations, datasets, Python analysis, execution approvals, artifacts,
and provenance in one auditable desktop workspace.

> Status: internal source MVP. The research and analysis loops run locally, but
> the Python services are currently started with Docker Compose and are not yet
> packaged as Tauri-managed sidecars.

## What works today

- Create local research projects and import PDF papers.
- Start a durable literature-synthesis workflow from a research goal, inspect its
  typed three-step plan, and explicitly approve or cancel it.
- Start a durable dataset-analysis workflow from an immutable CSV, approve its
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
  |-- OpenCode-compatible agent shell
  |-- Research workflow, evidence, review, and Analysis workspaces
  |-- explicit permission and approval UI
  |
  +-- science-core (FastAPI + SQLite + PaperQA2)
        |-- canonical Workflow / Plan / Task / Approval / Job / Review / Event state
        |-- leased background worker + restart recovery
        |-- local evidence extraction + deterministic claim review
        |
        +-- Unix-domain socket
              |
              +-- science-runtime (Jupyter, no network, read-only root)
```

The desktop shell reuses MIT-licensed Open Science Desktop code. Spark-specific
research pages, domain contracts, science services, approval state, and the
isolated execution runtime are maintained in this repository.

## Run the internal MVP

Requirements:

- macOS with Node.js 20+ and pnpm 9
- Docker Desktop or OrbStack
- an OpenAI-compatible credential only for remote model-assisted workflows or
  PaperQA questions

```bash
git clone https://github.com/shawliu998/spark-agent.git
cd spark-agent
pnpm install
pnpm mvp:dev
```

`mvp:dev` performs the internal preflight, builds the two isolated services,
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

Without a model key, the default deterministic workflow, PDF import, CSV analysis,
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

## Internal MVP boundary

- The durable orchestrator supports two coherent workflow types:
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
- This remains a source-run internal build. Tauri-managed packaging of the Python
  services is a later release step.

## Safety defaults

- Project-scoped file access and explicit approval for high-risk operations.
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
Its source history is nevertheless derived from Open Science Desktop v0.1.9.
The `upstream` Git remote is retained so selected upstream releases can be
merged intentionally. Product-specific services and features are kept separate
where practical to reduce merge conflicts.

Internal `@ai4s/*` and Rust crate names are temporarily retained for source
compatibility. They are implementation details, not the Spark Agent brand.

## License and attribution

The inherited Open Science Desktop code is available under the MIT License; the
required original notice is retained in [LICENSE](./LICENSE). See
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md) for major reused components.

Spark Agent is maintained and distributed independently from Open Science
Desktop and its maintainers.
