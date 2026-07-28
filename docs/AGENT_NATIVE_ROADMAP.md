# Spark Agent — Agent-Native Research Operator Roadmap

Status: approved implementation roadmap and single execution order
Updated: 2026-07-23
Applies to: Agent-native Phases 0 through 4

## 1. Purpose

Spark Agent is a local-first, agent-powered research workspace with autonomous
execution inside explicitly approved research boundaries. The product is the Research
Workspace; the Agent is the capability layer that advances a research goal through
durable, bounded, evidence-aware work.

The target is not a chatbot, a coding IDE, a multi-agent role-playing interface,
or an autonomous scientist. The target is a Research Operator that can:

1. understand a research goal and its constraints;
2. propose a typed, reviewable plan;
3. execute one approved step at a time;
4. validate the real state change caused by each tool call;
5. continue, retry, ask, replan, or stop from structured observations;
6. preserve evidence, artifacts, decisions, and recovery state; and
7. learn reusable procedures only through a controlled, testable skill lifecycle.

The governing autonomy rule is:

> High autonomy in execution; bounded autonomy in scientific decisions.

The continuous-learning goal is deliberately external to the foundation model:

> Keep model weights frozen. Improve over time through project-scoped,
> evidence-bound Research Memory and typed Procedural Skills that are verifiable,
> reviewable, versioned, and exactly reversible.

### 1.1 Non-goals

The current program does not include:

- online model training or weight mutation;
- automatically learning scientific conclusions;
- treating chat history as authoritative state;
- global automatic memory across projects;
- automatic Skill activation;
- a multi-agent product UI;
- permission, disclosure, provider, query, or budget expansion through learning; or
- LoRA, Adapter, or other parameter training in the current delivery cycle.

## 2. Current Baseline

The v1.2 baseline is complete according to `PROGRESS.md`. It already provides the
real substrate required for an agent-native product:

- a single Research Agent rather than a multi-agent UI;
- intent routing and clarification;
- typed plans and immutable approval envelopes;
- durable Workflow, Plan, Task, Job, Review, Event, and Approval state;
- a persisted observe -> decide -> apply loop;
- retry, no-progress, recovery, replan, stop, and completion semantics;
- crash and restart recovery;
- bounded Dataset and local Literature agent loops;
- EvidenceSpan, Claim, Answer, Review, Artifact, and provenance contracts;
- approved Python and Jupyter execution in the existing sandbox;
- the pinned OpenCode runtime, MCP support, and project-local skills; and
- reusable Outcome, Process, and Trust evaluation gates.

The v1.3 capability baseline includes the strict Discovery contract, persistence,
bounded stdio MCP transport, durable multi-operation Agent loop, typed Candidate
read model, and verified Evidence Coverage. Crossref and the exact OpenAlex
relevance/no-year scope are independently accepted through the reproducible
`paper-search-mcp==0.1.4+spark.2` fork. arXiv and PubMed remain pre-spawn disabled
until their connectors preserve the same explicit failure semantics. The exact
create, approval, execution-authority, and workflow-scoped read handoff is
independently accepted.

The v1.4 foundation now persists project/workflow-scoped immutable Memory revisions,
commits confirmed user decisions, creates reviewable open-question and deterministic
failure-lesson candidates, and injects only committed items through bounded,
hash-verified, restart-stable Context Snapshots into real next-action decisions.
Candidate accept/reject/invalidate APIs exist; Memory domain/SDK/UI, dependency
invalidation, broader Context assembly, and packaged restart gates remain incomplete.

The zero-Source desktop main journey now has a typed Crossref/no-download create
mutation, Home-to-proposal handoff, existing exact `WorkflowPlanCard` approval,
Candidate-only Papers/Screening states, and a per-query/provider execution ledger.
It does not auto-approve, download, promote a Candidate, or invoke a public provider
before approval. The remaining Phase 0 behavior gap is selecting the next operation
within the approved query set from persisted novelty and coverage rather than always
choosing the first pending operation. Public-network benchmarking, packaged QA,
Apple signing, and notarization remain separate release work.

## 3. Product and Architecture Invariants

The roadmap must preserve all of the following:

- The Research Workspace remains the product center.
- The existing Elicit-led Home, Papers, Screening, Extraction, Reader, and Report
  surfaces remain the visual and interaction baseline.
- Agent progress appears inside the existing workspace as plans, observations,
  decisions, tool results, evidence, artifacts, and review states.
- No separate Agent destination, multi-agent interface, chat-first shell, or
  chain-of-thought transcript is added.
- React accesses Science Core only through `@spark/research-sdk`.
- Science Core remains the canonical research control plane and source of truth.
- OpenCode remains a replaceable runtime, not a second source of workflow truth.
- PaperQA, Jupyter, MCP servers, and skills remain replaceable capabilities.
- Approval, sandbox, EvidenceSpan, Artifact, provenance, and
  `remoteDataApproved` semantics remain fail-closed.
- A Candidate is not a Source; a model suggestion is not evidence; a valid hash is
  not proof of scientific correctness.
- Unknown external outcomes are never replayed as if they were known failures.
- External documents, metadata, web content, and tool output are untrusted data,
  never instructions.
- Private project data is never used for public-network or fixture evaluation.

## 4. Target Agent Architecture

```text
Research Goal and Constraints
            |
            v
     Research Controller
            |
            v
  Current Plan and Step
            |
            v
   Capability Executor  <---- Tool / MCP / Python / Skill Registry
            |
            v
 Postcondition Validator ---- Evidence / Artifact / Provenance
            |
            v
 Structured Observation
            |
            v
 Continue / Retry / Ask / Replan / Stop / Complete
            |
            +----> Research Memory Candidate
            |
            +----> Skill Candidate Pipeline
```

The architecture has four planes.

### 4.1 Control Plane

Owns:

- research goals and constraints;
- plans and plan revisions;
- allowed actions and budgets;
- task and job state;
- approval and disclosure boundaries;
- observations and decisions;
- recovery and completion invariants; and
- cancel, retry, stop, and resume behavior.

### 4.2 Capability Plane

Owns typed, replaceable capabilities:

- local file and PDF operations;
- literature search MCP tools;
- PaperQA and deterministic extraction;
- Python and notebook execution;
- artifact inspection and export;
- installed OpenCode skills; and
- future read-only internal workers.

### 4.3 Knowledge Plane

Owns durable research assets:

- Projects, Questions, Sources, and Datasets;
- EvidenceSpan and Claim relationships;
- Screening and Extraction decisions;
- Artifacts and reports;
- project-scoped Research Memory; and
- content identity, lineage, and provenance.

### 4.4 Evaluation Plane

Evaluates three different things:

- Outcome: whether the user goal and useful artifact were completed.
- Process: whether planning, tool use, recovery, and stopping were correct.
- Trust: whether evidence, approvals, provenance, and data boundaries remained
  correct.

## 5. Core Vocabulary

The implementation must keep these concepts distinct.

| Object | Meaning |
| --- | --- |
| Tool | One atomic action with typed input and output. |
| Capability | A versioned tool or bounded service plus permissions and postconditions. |
| Skill | Procedural knowledge describing how to use capabilities reliably. |
| Workflow | One durable execution instance for a research goal. |
| Memory | A project-scoped fact, decision, assumption, question, or verified lesson. |
| Policy | What the Agent may do automatically and when a person must intervene. |
| Evidence | A source-bound passage or result supporting evaluation of a claim. |
| Artifact | A preserved research output with content identity and dependencies. |

### 5.1 Three learning layers

| Layer | Authority and purpose |
| --- | --- |
| Immutable episodic trace | What actually happened: approved inputs, tool calls, observations, corrections, outputs, hashes, and recovery identity. It is append-only evaluation evidence, not reusable behavior by itself. |
| Semantic Research Memory | Project-scoped decisions, open questions, assumptions, operational facts, and evidence-bound lessons. It enters Context only through explicit commit and invalidation rules. |
| Procedural Skill | A typed, sanitized, versioned procedure with tool, permission, precondition, postcondition, replay, approval, activation, and rollback contracts. |

External papers, web pages, Candidate metadata, tool output, and model output remain
untrusted data. None may directly write long-term behavior or activate a Skill.

### 5.2 Learning state machine

```text
episode
  -> verified episode
  -> candidate
  -> sanitize
  -> replay / evaluate
  -> human approval
  -> exact-hash activation
  -> monitor
  -> retire or exact rollback
```

Every transition is durable and project-scoped. A failed transition leaves the last
accepted version active; it never widens permissions or silently skips evaluation.

## 6. Delivery Sequence

```text
Phase 0  Zero-Source Discovery Main Journey          CURRENT
   |
Phase 1  Research Memory Completion
   |
Phase 2  Manual Project-Local Skill Candidate
   |
Phase 3  Evidence-Based Skill Suggestion
   |
Phase 4  Adaptive Skill Selection
   |
Future   Offline Explicit-Opt-In LoRA / Adapter
```

Each version has a hard Go/No-Go gate. A later phase must not begin merely because
the previous phase looks complete in the UI.

## 7. Phase 0 — Zero-Source Discovery and Evidence Coverage

### 7.1 User Outcome

Given a research question, Spark should:

1. create or continue a project;
2. ask only material scope questions;
3. propose exact queries, providers, year filters, result budgets, download
   budgets, and stop conditions;
4. obtain one explicit approval for that exact discovery scope;
5. choose the order of approved searches;
6. observe novelty, duplication, failures, and remaining coverage;
7. continue, stop early, retry safely, or request a scope revision;
8. show candidates in the existing Papers and Screening surfaces;
9. keep screening judgments human-owned; and
10. convert a candidate to a Source only through verified import and ingestion.

### 7.2 Discovery Contract

Use explicit per-provider semantics that match the pinned upstream tool:

```typescript
interface DiscoveryQuery {
  id: string;
  query: string;
  providers: Array<"arxiv" | "crossref" | "openalex" | "pubmed">;
  yearFrom: number | null;
  yearTo: number | null;
  sort: "relevance" | "newest";
  maxResultsPerProvider: number;
}
```

The approved snapshot and UI must also expose the deterministic derived maximum:

```text
derivedMaximumResults = maxResultsPerProvider * providerCount
```

The contract must bind:

- exact query IDs and normalized query text;
- exact provider set and canonical order;
- year and sort semantics per provider;
- per-provider and derived total result bounds;
- maximum discovery attempts;
- consecutive no-novelty stop threshold;
- open-access download permission and exact download count; and
- immutable schema version and canonical payload hash.

Any query, provider, year range, result count, download permission, or budget not
in the approved snapshot requires a new revision. A transient retry does not grant
new discovery scope.

### 7.3 Persistence

Add the minimum records needed for durable external discovery.

#### DiscoverySpecRecord

Stores the approved discovery boundary and revision lineage:

- project and workflow identity;
- revision and previous specification;
- canonical JSON and SHA-256;
- approval identity;
- status: pending-approval, approved, rejected, superseded;
- creator and creation reason; and
- timestamps.

#### ToolInvocationRecord

Do not misuse `ModelInvocationRecord` for MCP calls. A generic tool invocation
record should bind:

- workflow, plan, task, and job identity;
- capability and connector name/version;
- operation key and attempt;
- canonical request and request hash;
- response hash when known;
- prepared/pending/succeeded/failed/outcome-unknown status;
- normalized error category; and
- start and finish timestamps.

The pending identity must be committed before the external request is sent. If a
crash leaves the outcome unknowable, recovery must fail closed instead of silently
reissuing the call.

#### DiscoveryCandidateRecord

Stores normalized, explicitly untrusted metadata:

- provider and provider paper ID;
- normalized DOI, arXiv ID, or PMID when present;
- title, authors, abstract, and publication date;
- reported landing and open-access PDF URLs;
- normalized candidate identity and candidate hash;
- first-seen and last-seen timestamps; and
- trust classification `untrusted-metadata`.

It must not contain a workspace path or Source identity.

#### CandidateOccurrenceRecord

Records which approved query/provider invocation produced a candidate, its rank,
and the raw response hash. This provides query-ledger provenance and prevents
deduplication from erasing discovery history.

### 7.4 Tool Adapter

Use the repository-pinned `paper-search-mcp==0.1.4` contract. Execute one provider
operation at a time so the exact provider budget, error, observation, and recovery
identity remain explicit.

The adapter must:

- map only approved provider names to exact upstream tools;
- map year and sort parameters without silently dropping or broadening them;
- parse the upstream standardized paper representation;
- bound response bytes and item count before normalization;
- normalize DOI/arXiv/PMID identity deterministically;
- preserve collisions rather than merging incompatible records;
- classify provider, timeout, rate-limit, malformed-output, and policy failures;
- store raw-response identity without trusting raw content; and
- never treat a returned URL as download authorization.

Open-access download must explicitly disable Sci-Hub fallback, require independent
HTTPS/redirect/host validation, use bounded streaming, validate PDF content, write
atomically inside the workspace, hash the result, and pass normal Source ingestion.

### 7.5 Agent Loop Integration

Materialize approved discovery work as bounded search tasks. The Agent may select
only a currently ready task generated from the approved specification.

Each terminal search operation produces a structured observation containing IDs,
counts, status, and hashes rather than paper prose:

```json
{
  "queryId": "query-hallucination-benchmark",
  "provider": "openalex",
  "returnedCount": 20,
  "novelCandidateCount": 13,
  "duplicateCount": 7,
  "candidateSetSha256": "...",
  "consecutiveNoNovelty": 0,
  "remainingApprovedOperations": 2
}
```

Allowed decisions remain bounded:

- `continue` to another approved ready search task;
- `retry-step` only for a classified safe retry;
- `request-clarification` when an out-of-scope query or scope decision is needed;
- `stop` for no progress, unsupported capability, or user cancellation; and
- `complete` only when the discovery completion invariant holds.

An accepted clarification creates a new DiscoverySpec revision and a new exact
approval. It does not mutate the old approval.

### 7.6 Coverage Semantics

Keep two products separate.

#### Discovery Coverage

Derived from title and abstract metadata. It may describe topical, provider, year,
and query coverage, but must be labeled unverified candidate coverage.

#### Evidence Coverage

Derived only from imported, parsed, hash-bound sources and verified EvidenceSpan
records. It may describe supported facets, contradictory evidence, missing facets,
study context, and source diversity.

Discovery metadata must never be presented as full-text evidence.

### 7.7 UI Integration

Do not add a new destination.

In Papers and Screening, add compact states for:

- approved query/provider progress;
- candidates, duplicates, and remaining operations;
- provider-specific errors and bounded retry;
- untrusted candidate versus imported Source;
- import and open-full-text availability;
- stop and request-scope-change actions; and
- an explicit `Not verified from full text` state.

In Extraction and Synthesis, add a compact Evidence Coverage summary with links to
the existing evidence and source context. Preserve the current dense table and
split-reader patterns.

### 7.8 Phase 0 Go/No-Go Gate

Go only when all are true:

- exact provider/result/download budget tests pass;
- unapproved queries and providers invoked: zero;
- Sci-Hub invocations: zero;
- candidates converted to Sources without verified import: zero;
- external operations with durable pre-send identity: 100%;
- duplicate external side effects after recovery: zero;
- outcome-unknown operations automatically replayed: zero;
- crash-point recovery matrix: 100%;
- malicious metadata cannot alter Agent instructions or permissions;
- query ledger and budgets survive restart exactly;
- fixed local provider fixtures pass;
- explicitly approved public-network baseline is reported separately;
- packaged macOS workflow and restart QA pass; and
- independent high-reasoning review reports P0/P1=0.

### 7.9 Phase 0 remaining deliverables and gate

Deliverables:

- preserve the completed zero-Source Home -> immutable Crossref/no-download proposal
  -> exact approval -> Candidate/query-ledger desktop path;
- preserve pre-approval external invocation count at zero and Candidate/Source
  separation; and
- select the next operation only from the approved query/provider set using
  persisted novelty, duplicate, remaining-operation, and coverage signals.

Go/No-Go and minimum validation:

- unapproved query/provider calls, PDF downloads, Sci-Hub calls, Candidate
  promotions, unknown-outcome replays, and pre-approval invocations are all zero;
- SDK URL/auth/idempotency/abort and project-switch stale isolation pass;
- query ledger identity, budgets, stop reason, and retry classification survive
  restart;
- fixed Core, Desktop, i18n, malicious-metadata, and restart fixtures pass; and
- independent high-reasoning review reports P0/P1=0.

Remaining single-writer estimate: **1-2 engineering days**. Public-network baseline
and packaged release QA are excluded.

## 8. Phase 1 — Research Memory Completion

### 8.1 Goal

Maintain research continuity without turning chat history into authority.

### 8.2 Memory Model

```typescript
interface ResearchMemory {
  id: string;
  projectId: string;
  type:
    | "user-decision"
    | "assumption"
    | "open-question"
    | "failure-lesson"
    | "operational-fact";
  content: unknown;
  status: "candidate" | "committed" | "rejected" | "superseded" | "invalidated";
  sourceRefs: string[];
  artifactRefs: string[];
  previousMemoryId: string | null;
  invalidationRule: unknown | null;
  contentHash: string;
  createdBy: string;
}
```

Use immutable revisions rather than in-place semantic edits.

### 8.3 Commit Policy

- User scope and method decisions are committed as user decisions.
- Operational facts such as file hashes and dataset schema may be committed by a
  deterministic check.
- Scientific facts proposed by a model remain candidates until the relevant
  evidence and report review path accepts them.
- Assumptions are always visibly unverified.
- Open questions may be recorded automatically but never interpreted as facts.
- Failure lessons require a reproducible failure or deterministic reviewer finding.
- No memory entry grants tools, permissions, network access, or data disclosure.
- Memory is project-scoped by default; cross-project memory is not part of Phase 1.

### 8.4 Context Assembly

Each decision receives a deterministic, hash-bound context snapshot containing only:

- the current goal and approved constraints;
- the current approved plan and ready step;
- committed user decisions;
- relevant verified or explicitly open memory;
- Evidence Coverage;
- recent structured observations;
- remaining budgets; and
- the current allowed action set.

Exclude full chat history, arbitrary full-text papers, superseded memory, other
projects, unapproved skill candidates, and private chain-of-thought.

Start with SQLite filters and FTS5. Do not add a vector database until a benchmark
shows that deterministic retrieval is insufficient.

### 8.5 UI

Use the existing Project Overview and inspectors to show:

- current understanding;
- decisions;
- assumptions;
- open questions;
- evidence gaps; and
- recent memory changes.

Users must be able to inspect sources, accept or reject candidates, mark an entry
stale, create a revision, and see why a memory item entered the current context.

### 8.6 Phase 1 Go/No-Go Gate

- cross-project leakage: zero;
- uncommitted candidate injection: zero;
- stale or invalidated memory injection: zero;
- memory-derived permission expansion: zero;
- every committed scientific fact resolves to reviewed evidence;
- source/review mutation invalidates dependent memory correctly;
- prompt-injected documents cannot commit memory;
- context snapshots have strict size and item budgets;
- restart recovery is exact; and
- independent high-reasoning review reports P0/P1=0.

### 8.7 Phase 1 remaining deliverables and gate

Deliverables:

- typed Research Memory domain/SDK and compact review UI in the existing workspace;
- dependency-bound automatic invalidation for changed Sources, Evidence, Reviews,
  schemas, and tool versions;
- Context assembly containing current Evidence Coverage, remaining budgets, and the
  deterministic allowed-action set without raw prompts or chat history;
- explicit disclosure of why an item entered Context and which revision was used;
  and
- migration, restart, cross-project, workflow-revision, and rollback coverage.

Go/No-Go and minimum validation:

- unauthorized committed Memory, candidate injection, stale Memory injection,
  cross-project leakage, private-data leakage, and Memory-derived permission
  expansion are all zero;
- every scientific semantic entry is evidence/review-bound and automatically
  invalidates when that dependency changes;
- exact snapshot identity and restart recovery are 100%;
- focused API/SDK/UI/i18n, migration, tamper, rollback, and packaged restart tests
  pass; and
- independent high-reasoning review reports P0/P1=0.

Remaining single-writer estimate: **3-4 engineering days**.

## 9. Phase 2 and Phase 3 — Controlled Skill Learning

### 9.1 Goal

Learn reusable procedures from verified work without allowing the Agent to modify
its permanent behavior silently.

Spark already has the OpenCode skill format, bundled version-pinned skills, a live
skill catalog, and project-local `.opencode/skills/`. Phases 2 and 3 add governance and
learning, not a second skill system.

### 9.2 Trigger Policy

Support two paths:

- Manual: the user requests `Save this workflow as a reusable skill` after one
  successful run.
- Suggested: the Agent may propose a candidate after at least three independent
  successful runs with a stable capability signature, passed reviews, no unresolved
  P0/P1 finding, stable postconditions, and no repeated user correction of the same
  procedure.

Neither path activates a skill automatically.

Phase 2 implements only the Manual path for one local, deterministic,
project-scoped procedure already proven by the repository. It projects an immutable
verified episode into a typed candidate, strips project/private content, runs all
six replay classes, and requires exact-hash human approval before project-local
activation. Activation rollback restores the prior exact hash.

Phase 3 adds only the Suggested path. A suggestion requires at least three
independent successful runs: different durable workflow/run identities, the same
stable capability signature, passed postconditions and reviews, no unresolved
P0/P1 finding, and no repeated user correction of the procedure. A suggestion is
still only a candidate and cannot activate itself.

Phase 3 must define how user corrections reset or split the success series, when
candidates merge, retirement after repeated verified failure, parent/child version
lineage, and tool/schema/provider/runtime version drift.

### 9.3 SkillCandidate

```typescript
interface SkillCandidate {
  id: string;
  name: string;
  description: string;
  scope: "project" | "user";
  trigger: unknown;
  inputs: unknown;
  preconditions: unknown[];
  allowedTools: string[];
  requiredPermissions: unknown[];
  procedure: unknown[];
  expectedArtifacts: unknown[];
  postconditions: unknown[];
  failurePolicy: unknown;
  provenanceRequirements: unknown[];
  originTraceIds: string[];
  sanitizedSourceHash: string;
  parentSkillId: string | null;
  version: string;
  contentHash: string;
  status:
    | "draft"
    | "sanitized"
    | "validating"
    | "failed-validation"
    | "awaiting-approval"
    | "active"
    | "rejected"
    | "superseded"
    | "retired";
}
```

### 9.4 Sanitization

Candidate generation may consume structural trajectory data such as tool names,
schema versions, generic parameter shapes, error classes, state transitions,
postconditions, and fixture IDs.

It must remove paper text, private dataset values, project paths, identities, API
keys, provider tokens, private URLs, and project-specific research content.

### 9.5 Replay Validation

Every candidate must pass at least:

1. a happy-path fixture;
2. malformed input;
3. tool failure;
4. permission denial;
5. prompt injection; and
6. restart/recovery when the procedure is durable.

Validation checks:

- valid skill frontmatter and referenced files;
- every tool exists in the approved allowlist;
- tool parameters satisfy the pinned schema;
- no permission or disclosure scope is added;
- deterministic postconditions pass;
- no private content is embedded;
- normal approval and sandbox policy still apply; and
- produced artifacts and provenance are complete.

### 9.6 Approval and Activation

The approval view must disclose:

- purpose and trigger;
- originating successful runs;
- exact content diff from any parent version;
- tools, network access, files, and permissions;
- fixture results; and
- any permission change.

Install to project-local `.opencode/skills/<name>/` by default. User-wide activation
requires a separate explicit decision. Activation binds the exact skill hash, tool
versions, schema versions, approval, and evaluation result.

### 9.7 Rollback and Retirement

Disable or retire a skill when:

- a tool or schema becomes incompatible;
- the same verified failure repeats;
- postconditions stop passing;
- permission requirements increase;
- provenance is missing; or
- the user rolls back.

The Agent may propose an updated candidate but cannot overwrite the active version.

### 9.8 Phase 2/3 Go/No-Go Gate

- unapproved skill activation: zero;
- private data in generated skills: zero;
- silent permission expansion: zero;
- required fixture replay coverage: 100%;
- rollback restores the prior exact hash: 100%;
- incompatible tool versions fail closed;
- project-local skills visible in another project: zero;
- skill execution still obeys normal approval and sandbox policy; and
- independent high-reasoning review reports P0/P1=0.

### 9.9 Phase-specific gates and estimates

Phase 2 minimum validation:

- one deterministic project-local Skill Candidate;
- structural projection rejects paper text, private values, identities, paths,
  credentials, and private URLs;
- happy path, malformed input, tool failure, permission denial, prompt injection,
  and restart/recovery replay all pass;
- unauthorized activation, permission expansion, and cross-project visibility are
  zero; exact rollback is 100%; and
- independent P0/P1=0.

Phase 2 single-writer estimate: **3-4 engineering days**.

Phase 3 minimum validation:

- no suggestion before three independent successful runs;
- stable capability signature and correction/version-drift fixtures pass;
- merge, retire, supersede, and rollback lineage is deterministic;
- suggested candidates remain inactive until exact-hash approval; and
- independent P0/P1=0.

Phase 3 single-writer estimate: **2-3 engineering days**.

## 10. Phase 4 — Adaptive Skill Selection

### 10.1 Capability Registry

Introduce a common descriptor for capabilities:

```typescript
interface CapabilityDescriptor {
  name: string;
  version: string;
  inputSchema: unknown;
  outputSchema: unknown;
  sideEffectClass: "read-only" | "workspace-write" | "external-write";
  requiredPermissions: unknown[];
  disclosureCategories: string[];
  preconditions: unknown[];
  postconditions: unknown[];
  retryPolicy: unknown;
  costModel: unknown;
}
```

The model receives only the current `AllowedCapabilitySet`; availability does not
grant permission.

### 10.2 Adaptive Planning

Allow a plan revision when a verified observation shows:

- a research facet lacks evidence;
- counterevidence was found;
- a source has no usable full text;
- an analysis method assumption failed;
- Python execution failed safely;
- required artifacts are incomplete;
- a claim lacks support; or
- the user changed the goal.

New direction, provider, query scope, scientific method, disclosure, installation,
deletion, budget, or final conclusion still requires human confirmation.

Skill selection is filter-then-rank, never rank-then-authorize:

1. filter by project scope, active exact version, permission/disclosure boundary,
   input schema, preconditions, postconditions, tool availability, and the current
   allowed-action set;
2. rank only surviving Skills using goal fit, verified success history, estimated
   latency/token cost, and recent stability; and
3. begin in shadow mode, comparing the selected Skill with the baseline trajectory
   without changing execution.

### 10.3 Internal Parallel Workers

Optional internal workers may improve latency and independent checking:

- discovery worker;
- evidence locator;
- statistical checker;
- citation auditor; and
- artifact verifier.

They are not separate product personas. They must be isolated, read-only unless
explicitly approved, return structured candidates, and never modify canonical
workflow truth directly. The main Research Agent remains the only coordinator.

Internal workers are permitted only after a fixed benchmark demonstrates that the
single Research Agent has a material outcome, latency, or independent-verification
bottleneck that cannot be resolved with a simpler bounded capability. They are not a
Phase 4 completion requirement.

### 10.4 Scheduled Work

Opt-in watchers may later monitor new papers or refresh a report. Every watcher must
bind project, provider, schedule, network permission, result budget, expiry, cancel
behavior, and durable last-run state. Scheduled networking remains off by default.

Scheduled work is an optional later capability and is not part of the core Research
Operator completion gate.

### 10.5 Phase 4 Go/No-Go Gate

- capability selection respects the current allowlist in every fixture;
- plan revisions are justified by a persisted observation;
- permission and disclosure expansion always interrupts for confirmation;
- internal workers cannot mutate canonical state directly;
- no-progress loops terminate within the configured bound;
- counterevidence and method-failure fixtures produce the correct ask/replan/stop;
- repeated runs report mean, worst case, and variance; and
- independent high-reasoning review reports P0/P1=0.

### 10.6 Phase 4 gate and estimate

- unauthorized or incompatible Skill selection: zero;
- Skill usage faithfulness and postcondition satisfaction meet the fixed threshold;
- forward transfer improves held-out tasks without old-task regression or stability
  loss beyond the fixed threshold;
- shadow-mode, held-out generalization, restart, latency, and token fixtures pass;
- permission/disclosure/budget expansion remains zero; and
- independent P0/P1=0.

Phase 4 single-writer estimate: **3-5 engineering days**.

### 10.7 Future: parameter-efficient training

Offline LoRA or Adapter experiments may be considered only after Phases 0-4 pass.
They must be explicit opt-in, isolated from the production base model, evaluated
against held-out and regression suites, and exactly removable. They are not part of
the current 12-18 day program.

## 11. Evaluation Program

### 11.1 Outcome

- task completion gain over the fixed baseline;
- time to first useful artifact;
- final artifact correctness;
- citation reopen success;
- appropriate blocked/abstain behavior; and
- restart continuity.

### 11.2 Process

- human intervention count and rate;
- Skill usage faithfulness;
- forward transfer to new tasks;
- old-task regression and stability loss;
- held-out generalization;
- token use and end-to-end latency;
- plan validity;
- tool selection and argument validity;
- execution, state, and semantic postcondition success;
- recovery rate;
- no-progress and premature-stop rate;
- justified replan rate;
- repeated query/tool-call rate; and
- budget deviation.

### 11.3 Trust

- unauthorized activation;
- private-data leakage;
- permission or disclosure expansion;
- cross-project leakage;
- exact rollback success;
- approval violations;
- out-of-scope network calls;
- private-data disclosure;
- exact claim support and citation completeness;
- provenance and artifact-lineage completeness;
- Candidate/Source boundary violations;
- cross-project memory leakage; and
- unauthorized skill activation.

Deterministic safety and identity invariants require 100%. Non-deterministic public
search and model benchmarks must report repeated-run mean, worst case, and variance,
never only the best trajectory.

Required zero/absolute targets are: unauthorized activation = 0, private-data
leakage = 0, permission/disclosure/budget expansion = 0, cross-project leakage = 0,
and exact rollback success = 100%.

## 12. Implementation Map

The only approved code sequence is:

1. finish Phase 0 novelty/coverage next-operation selection and its fixed/restart
   gate;
2. finish Phase 1 Memory domain/SDK/UI, dependency invalidation, complete Context,
   and migration/restart/isolation gate;
3. implement one Phase 2 manual, deterministic, project-local Skill Candidate and
   exact-hash activation/rollback;
4. add Phase 3 three-independent-success suggestion only after Phase 2 passes; and
5. add Phase 4 shadow-mode filter-then-rank selection only after Phase 3 passes.

Do not start automatic Skill generation, adaptive selection, internal multi-agent
work, or parameter training early.

## 13. Work Organization

Maintain one code writer at a time. Safe parallel work is read-only:

- architecture and contract review;
- security and threat review;
- fixture and evaluation review;
- visual and accessibility QA;
- packaged/restart QA; and
- final independent acceptance.

Use high-reasoning review for product decisions, action and permission semantics,
memory commit policy, skill activation, and final acceptance. Use bounded execution
agents only after the exact file targets and acceptance checklist are frozen.

Each phase uses one writer. Parallel tasks are read-only. Each phase ends with one
independent high-reasoning review; only confirmed P0/P1 findings are fixed.
`PROGRESS.md` records only real completed milestones.

## 14. Schedule

| Phase | Remaining scope | Single-writer estimate |
| --- | --- | ---: |
| 0 | Approved-set novelty/coverage operation selection and gate | 1-2 days |
| 1 | Complete project-scoped Memory and Context | 3-4 days |
| 2 | One manual project-local Skill Candidate | 3-4 days |
| 3 | Three-success Skill suggestion governance | 2-3 days |
| 4 | Shadow-mode adaptive Skill selection | 3-5 days |

Total rough estimate: **12-18 engineering days**. This excludes final Apple
signing/notarization, explicitly approved public-network benchmarking, and external
service delays.

## 15. Immediate Approved Order

1. **COMPLETE — PHASE 0:** strict Crossref-only Discovery contract, durable
   invocation, approval, recovery, Candidate read model, Evidence Coverage, typed
   desktop create, exact approval handoff, Candidate/query ledger, no-demo
   zero-Source boundary, and approved-set novelty/coverage operation selection with
   restart-stable decision and terminal-result identities.
2. **CURRENT — PHASE 1:** complete the project-scoped Memory control plane,
   typed domain/SDK, and compact existing-workspace review UI before dependency
   invalidation, Context expansion, migration gates, or Skills.
3. **NEXT — PHASE 1:** add dependency invalidation, complete Context assembly,
   and migration/restart/isolation gates.
4. **THEN — PHASE 2:** implement one manual project-local deterministic Skill
   Candidate with six replay classes and exact-hash approval/activation/rollback.
5. **THEN — PHASE 3:** suggest, but never activate, a Skill only after three
   independent successful runs and correction/version-drift checks.
6. **THEN — PHASE 4:** filter then rank active Skills in shadow mode before any
   adaptive execution.
7. **FUTURE ONLY:** explicit-opt-in offline LoRA/Adapter experiments after all
   current phase gates pass.

The sequencing rule is:

> First make the Agent act reliably, then make it remember reliably, and only then
> allow it to learn reusable procedures.
