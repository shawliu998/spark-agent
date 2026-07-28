# Spark Agent — Product Requirements

> **Status:** current product requirements, updated 2026-07-23.
> **Product strategy source of truth:** [`../PRODUCT.md`](../PRODUCT.md).
> **Agent delivery details:** [`AGENT_NATIVE_ROADMAP.md`](AGENT_NATIVE_ROADMAP.md).
> **Technical contracts:** [`TECHNICAL_DESIGN.md`](TECHNICAL_DESIGN.md).

This document translates the approved positioning into product behavior. It does not
replace the technical, approval, sandbox, evidence, artifact, provenance, or release
contracts defined elsewhere.

## 1. Product Definition

Spark Agent is an agent-powered research workspace for individual researchers. A user
provides a bounded research question, papers, and optionally a local dataset. Spark
organizes the project, advances approved work, and produces verifiable, reproducible
research artifacts.

The product has three distinct layers:

- **Product category:** Research Workspace.
- **Interaction model:** Research Operator.
- **Technical capability:** one durable, bounded Research Agent.

The Research Workspace owns knowledge and continuity. The Agent performs work inside
that workspace. OpenCode is a replaceable runtime, not the product or the source of
research truth.

### 1.1 Customer-facing promise

> Give Spark a bounded research question, confirm each immutable material-scope
> revision once, and receive a research brief whose claims can be reopened in the
> supporting source and whose analysis can be reproduced from the preserved project.

### 1.2 Governing autonomy rule

> High autonomy in execution; bounded autonomy in scientific decisions.

Spark is not a chatbot, coding IDE, multi-agent command center, compliance dashboard,
or autonomous scientist.

## 2. Target Users

### 2.1 Primary user

The primary user repeatedly needs to turn a bounded question and a set of papers into
one of the following:

- an evidence-backed answer;
- a paper comparison or extraction table;
- a cited research brief or literature synthesis; or
- a reproducible analysis package connected to the reviewed evidence.

Initial users include graduate students, research assistants, doctoral students,
postdoctoral researchers, applied researchers, and research engineers. They are
defined by the recurring research job, not by discipline or familiarity with agents.

### 2.2 Secondary user

The adjacent user is a computational researcher who needs to continue a literature
project into a local dataset, an approved analysis, a notebook, figures, and tables.
Dataset analysis extends the same research project; it is not a standalone data
science product.

### 2.3 Ecosystem user, not product wedge

Skill authors, MCP developers, model-provider users, and agent-framework enthusiasts
may extend Spark, but their tooling needs must not determine the primary navigation or
first-run experience.

### 2.4 Non-target users

The initial product does not target:

- casual web or homework question answering;
- regulated systematic-review automation;
- clinical diagnosis or medical decision making;
- autonomous wet-lab work or an AI Scientist;
- software-development tasks;
- enterprise knowledge management, SSO, or team administration;
- a general browser, coding, or workflow automation agent; or
- users who expect the model to own final scientific judgment.

## 3. Core User Jobs

Spark must help the user:

1. state and scope a research question;
2. discover or import relevant papers;
3. screen and organize a candidate set;
4. read a source and reopen the exact supporting passage;
5. extract and compare structured information across sources;
6. identify evidence gaps, limitations, and potential contradictions;
7. produce and edit a cited answer, comparison, synthesis, or report;
8. analyze a related local dataset and preserve the notebook, figures, and tables;
9. understand current progress, the next action, and what remains unverified; and
10. pause, recover, resume, review, and export the project without reconstructing it
    from chat history.

## 4. Primary Product Journey

The first adoption wedge is intentionally narrow:

```text
Bounded research question
        |
Material scope confirmation
        |
Paper discovery and verified import
        |
Human-owned screening
        |
Full-text evidence and comparison
        |
Editable cited research brief
```

### 4.1 First-minute experience

1. The user enters a question and intended deliverable.
2. Spark asks only questions that materially change scope, method, cost, or data
   disclosure.
3. Spark creates or resumes a project and proposes an exact plan with boundaries,
   budgets, stop conditions, and expected artifacts.
4. The user confirms that immutable material-scope revision once.
5. Spark begins work in the existing research workspace.
6. The workspace shows the current action, completed work, evidence coverage,
   remaining gaps, next decision, and reason for a pause or failure.

Chat may clarify intent, but plans, observations, decisions, evidence, artifacts, and
reviews are the durable product record.

Capability-specific execution, remote-data, installation, deletion, and other
required approvals remain unchanged and are never implied by the scope confirmation.

## 5. Functional Requirements

### 5.1 Project and goal

- A research task must belong to a durable project.
- The project must preserve the question, objective, scope, constraints, source set,
  analysis inputs, decisions, artifacts, and current workflow state.
- Spark must resume from persisted state after cancellation, failure, app restart, or
  runtime restart.
- A material change to the goal or approved boundary creates a new plan revision; it
  must not mutate an approved snapshot silently.

### 5.2 Agent planning and operation

- The Agent must propose typed, reviewable steps with inputs, allowed capabilities,
  expected outputs, postconditions, budgets, and failure policies.
- It must execute one bounded step at a time and validate the real state change before
  marking the step complete.
- Each observation must lead to one explicit outcome: continue, retry, ask, replan,
  stop, block, or complete.
- Transient retries must remain inside the approved scope and be idempotent where side
  effects are possible.
- Unknown external outcomes fail closed and must not be replayed as known failures.
- Repeated actions without progress must stop with an understandable reason.
- The user must be able to cancel, retry, resume, edit a proposed revision, or reject
  an expanded plan.

### 5.3 Literature discovery

- Discovery must bind exact queries, providers, filters, per-provider result budgets,
  download budgets, and stopping conditions to an approved specification.
- Spark may select the order of approved queries and stop early from novelty and
  coverage observations.
- New queries, providers, filters, downloads, or budgets require an explicit revision.
- Every external invocation requires durable pending-before-send identity, structured
  results, retry classification, and restart recovery.
- Results must be normalized and deduplicated deterministically where possible.
- The workspace must show the query ledger, provider failures, duplicate handling,
  result bounds, and remaining discovery coverage without exposing raw agent thought.
- A discovery candidate is not a project Source. Promotion requires verified import,
  content identity, and ingestion.

### 5.4 Screening and extraction

- Inclusion and exclusion decisions remain human-owned and persisted.
- Spark may recommend, prioritize, or explain screening choices but must not present a
  recommendation as a user decision.
- Extraction must preserve source identity, field definition, extracted value, exact
  supporting passage when available, and review state.
- Tables must support dense comparison, long academic titles, missing values, source
  reopening, filtering, and export.

### 5.5 Evidence and claims

- A claim must remain distinct from a source, passage, model suggestion, and review.
- EvidenceSpan must bind source identity, page or location, exact text, and content
  identity according to the existing contracts.
- Citation review must distinguish structural integrity, semantic support, and
  scientific validity.
- A valid source identity or hash proves traceability, not scientific correctness.
- Unsupported, partially supported, conflicting, and unverified claims must be visible
  before final acceptance.
- The user must be able to reopen the exact supporting context from a claim or report.

### 5.6 Synthesis and report

- Spark must produce editable cited answers, comparison tables, syntheses, and reports
  from the current project and exact reviewed source set.
- Reports must preserve claim-to-evidence links, limitations, unverified sections, and
  artifact references.
- A report must fail closed when its project, question, source set, review snapshot, or
  evidence identity no longer matches the approved completed workflow.
- Export must preserve readable output and the available evidence and artifact lineage.

### 5.7 Computational research

- A dataset must remain a project Source with stable identity and visible scope.
- Analysis begins from a reviewable intent and method, not untracked generated code.
- Python, Jupyter, package installation, network use, and other execution retain their
  existing approval and sandbox requirements.
- Results must preserve input data, analysis specification, executed code or notebook,
  environment information, logs, figures, tables, and dependencies.
- Figures and tables prioritize reading, comparison, source tracing, and export rather
  than decorative visualization.
- The product must not become a general code editor, terminal, or notebook IDE.

### 5.8 Artifacts and provenance

- Important outputs must become typed Artifacts rather than disappear into a response.
- An Artifact must preserve content identity, producing activity, inputs, dependencies,
  creator, review state, and available export actions.
- Detailed hashes, manifests, permissions, and execution records belong in inspectors,
  approval surfaces, and export metadata rather than the default hierarchy.
- Logs and provenance may prove what ran; they do not prove the scientific conclusion.

### 5.9 Research continuity

Project-scoped Research Memory is a post-v1.3 capability. It may preserve validated:

- facts with source references;
- user decisions;
- assumptions;
- open questions;
- evidence gaps; and
- verified procedural lessons with explicit invalidation conditions.

Memory candidates require validation before commitment. Free-form model reflection,
cross-project leakage, and silent global memory are prohibited.

### 5.10 Controlled skills

Skills are procedural knowledge, not project facts and not a primary navigation
destination. A learned procedure may become active only through:

```text
Verified repeated success
        -> skill candidate
        -> sanitization
        -> fixture replay
        -> human approval
        -> versioned activation
        -> rollback or retirement
```

Spark must not auto-activate generated skills, embed project secrets or private
content in a skill, or use a skill to expand permissions.

## 6. Autonomy and Approval Boundary

Within an approved scope, Spark may automatically:

- order and run allowed read-only operations and workspace-write steps explicitly
  bound to the current immutable approval envelope;
- normalize, deduplicate, parse, index, and validate results;
- observe coverage, novelty, failures, and lack of progress;
- maintain drafts, comparisons, artifacts, and recovery state;
- stop early when an approved deterministic policy is satisfied; and
- propose a revision when more authority or scientific judgment is required.

The user owns:

- the question, scope, provider set, budget, and disclosure boundary;
- screening decisions;
- method changes and expanded execution permissions;
- interpretation of conflicting or insufficient evidence;
- final scientific conclusions; and
- acceptance, export, publication, or deletion.

File writes, command execution, dependency installation, deletion, remote connection,
and research-data disclosure retain the existing fail-closed approval policies. Low-
risk work inside an already approved boundary should not create repetitive approval
fatigue.

## 7. Information Architecture and Interaction

The approved information architecture is defined in `PRODUCT.md` and remains frozen
during the v1.3 capability phase.

Global destinations use research vocabulary:

- Home
- Library
- Workflows
- Projects
- Data
- Reports
- Settings

Project navigation follows the active research job:

- Literature: Overview, Papers, Screening, Extraction, Synthesis.
- Research answer: Answer, Papers, Notes, Report.
- Dataset analysis: Dataset, Analysis, Results, Notebook.

Agent progress appears contextually as structured plans, observations, decisions,
tool results, evidence, artifacts, and review states. Spark must not add a separate
Agent destination, multi-agent persona UI, chat-first shell, or chain-of-thought
transcript.

The interface should remain dense, calm, and research-oriented. Elicit is the primary
reference for literature workflows, Consensus for question-first search and answer
synthesis, SciSpace for source-in-context reading, and Spark-owned patterns for local
analysis and artifact continuity.

## 8. Trust and Technical Invariants

- The frontend accesses Science Core only through the typed Research SDK.
- Science Core is the canonical control plane and source of workflow truth.
- OpenCode, PaperQA, Jupyter, MCP servers, models, and skills remain replaceable
  capabilities.
- The Agent may access only the current workspace.
- Credentials remain in the OS credential manager and never enter logs, provenance,
  exported projects, or model prompts beyond the required provider request.
- External papers, metadata, web content, and tool output are untrusted data, never
  instructions.
- Approval, sandbox, EvidenceSpan, Artifact, provenance, and remote-data semantics
  remain fail closed.
- A model result, structurally valid citation, or deterministic reviewer pass must not
  be described as proof of scientific truth.

## 9. Accessibility and Responsive Requirements

- All primary workflows must be operable by keyboard.
- Focus must remain visible and predictable after dialogs, drawers, and source jumps.
- State must not be communicated by color alone.
- Long titles, citation excerpts, code, logs, and tables must remain usable across
  supported desktop widths and low-height windows.
- Reduced motion must disable non-essential movement without hiding state changes.
- Dense research tables must preserve column meaning, source tracing, comparison, and
  export at narrower widths.

## 10. Product Success Measures

Primary measures are:

- time from a bounded question to the first useful evidence-backed artifact;
- completion rate for the intended answer, comparison, report, or analysis;
- atomic claim support and exact-passage reopen success;
- unsupported-claim and unverified-strengthening rates;
- pause, failure, runtime, and restart recovery success;
- number of material user interventions per completed run;
- reproducibility and lineage completeness of exported artifacts; and
- time required to understand current state and the next action without reading a
  chat transcript or raw log.

Conversation length, visible model activity, number of agents, number of tool calls,
and generated-token volume are not product success measures.

## 11. Delivery Order

The approved sequence is:

```text
v1.2 Reliable Agent Substrate                 COMPLETE
  |
  v
v1.3 Agent-Native Discovery and Coverage      CURRENT
  |
  v
v1.4 Research Memory and Context Assembly
  |
  v
v1.5 Controlled Skill Learning
  |
  v
v1.6 Adaptive Research Operator
```

Detailed contracts, gates, implementation locations, and evidence requirements live in
`AGENT_NATIVE_ROADMAP.md`. A later phase must not start until the prior phase passes its
Outcome, Process, and Trust gate.

## 12. One-line Positioning

> Spark Agent is an agent-powered research workspace that turns bounded research
> questions, papers, and local data into verifiable, reproducible research artifacts.
