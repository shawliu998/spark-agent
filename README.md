# Spark Agent

Spark Agent is a local-first, evidence-first research agent for macOS. It joins
papers, citations, datasets, Python analysis, execution approvals, artifacts,
and provenance in one auditable desktop workspace.

> Status: internal source MVP. The research and analysis loops run locally, but
> the Python services are currently started with Docker Compose and are not yet
> packaged as Tauri-managed sidecars.

## What works today

- Create local research projects and import PDF papers.
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
  |-- Research and Analysis workspaces
  |-- explicit permission and approval UI
  |
  +-- science-core (FastAPI + SQLite + PaperQA2)
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

To enable the current PaperQA model gateway, provide the key through the shell
environment. Do not commit it or paste it into application logs.

```bash
OPENAI_API_KEY=your-key pnpm mvp:dev
```

Without a model key, PDF import, CSV analysis, and Jupyter execution remain
available; remote literature answering is disabled.

Stop the local services with:

```bash
pnpm science:down
```

## Safety defaults

- Project-scoped file access and explicit approval for high-risk operations.
- No arbitrary direct shell mode in the product UI.
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
