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
- Extract page-addressable evidence across local papers, build atomic claims, and
  require a deterministic evidence-integrity Reviewer pass before completion.
- Recover persisted workflow jobs after restart, with revision-checked retry,
  resume, cancel, and an auditable event timeline.
- Ask PaperQA-backed questions with explicit remote-data approval.
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
- an OpenAI-compatible credential only when remote PaperQA questions are needed

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

To enable the current PaperQA model gateway, provide the key through the shell
environment. Do not put the key directly in a shell command, commit it, or paste
it into application logs. On the default macOS zsh, prompt for it without echoing:

```bash
read -s "OPENAI_API_KEY?OpenAI API key: "
printf '\n'
export OPENAI_API_KEY
pnpm mvp:dev
unset OPENAI_API_KEY
```

Without a model key, the local extractive workflow, PDF import, CSV analysis, and
Jupyter execution remain available; remote PaperQA answering is disabled.

Stop the local services with:

```bash
pnpm science:down
```

Normally, pressing `Ctrl-C` in the `mvp:dev` terminal performs the same scoped
cleanup. Project data remains under `.local/science-core`; the ephemeral runtime
exchange and socket volumes are removed.

## Internal MVP boundary

- The durable orchestrator currently supports one coherent workflow type:
  `literature-synthesis` (inspect sources → extract local evidence → synthesize
  extractive claims → deterministic review).
- The plan is intentionally deterministic and local in this slice. OpenCode,
  PaperQA, STORM, MCP, OpenHands, and MLX do not own or mutate canonical workflow
  state.
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
  guards, immutable plan hashes,
  leased jobs, bounded recovery, and fail-closed Reviewer checks.
- Immutable, hashed analysis intents with compare-and-set approval decisions.
- Jupyter execution in a no-network container with a read-only root filesystem,
  resource limits, and a Unix-domain socket instead of a TCP runtime port.
- Artifact paths, regular files, source containment, and SHA-256 are verified
  before files are served or accepted.

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
