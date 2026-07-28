# Spark Agent Product Context

## Register

product

## Platform

macOS desktop

## Users

Spark Agent is primarily for individual researchers who repeatedly turn a bounded
research question into an evidence-backed answer, comparison table, research brief,
or reproducible analysis package. The initial users are graduate students, research
assistants, postdoctoral researchers, applied researchers, and research engineers.
They are defined by this recurring job rather than by discipline or familiarity with
AI agents.

These users need to move quickly between a research question, papers, extracted
information, local data, analysis outputs, and a usable report without managing a
developer toolchain or reconstructing the project from chat history.

### Primary adoption wedge

The first user is an individual researcher who needs to answer a bounded research
question from papers and, when relevant, a local dataset. Literature research is the
primary entry point; reproducible dataset analysis is the adjacent capability inside
the same project. Spark should first win workflows that end in an evidence-backed
review, comparison table, cited answer, or reproducible analysis package.

The first release is not aimed at general web research, regulated systematic-review
automation, autonomous wet-lab science, collaborative lab administration, or software
development. Research teams may use Spark, but team collaboration is not the initial
product wedge.

### Secondary expansion

After Spark wins the literature task, the adjacent user is a computational researcher
who needs to connect the reviewed evidence to a local dataset, an approved analysis,
a notebook, figures, and tables in the same project. Dataset analysis strengthens the
research project; it is not a separate data-science product or an IDE.

Agent-framework enthusiasts, skill authors, and MCP developers are ecosystem users,
not the primary product audience. Clinical decision makers, casual homework users,
enterprise knowledge-management teams, and users seeking an autonomous scientist are
not initial targets.

## Product Purpose

Spark Agent is a desktop research workspace that helps a user find, import, screen,
read, compare, and synthesize papers and, when the research goal requires it, analyze
a related local dataset and export useful research outputs. Success means a researcher
can complete a recognizable research task with less manual switching between search
tools, PDF readers, spreadsheets, notebooks, and report editors.

The primary user-facing jobs are:

- Find papers for a research question.
- Screen and organize a paper set.
- Read a paper and reopen the exact supporting passage.
- Extract and compare structured information across papers.
- Produce and edit a cited synthesis or report.
- Import a dataset and obtain readable tables, figures, and a notebook.

## Positioning

An agent-powered research workspace for individual researchers that turns a bounded
research question, papers, and local data into verifiable, reproducible research
artifacts. It combines Elicit-style literature workflows, source-in-context paper
reading, and reproducible dataset analysis in one desktop project.

The customer-facing promise is:

> Give Spark a bounded research question, confirm each immutable material-scope
> revision once, and receive a research brief whose claims can be reopened in the
> supporting source and whose analysis can be reproduced from the preserved project.

The product category, interaction model, and technical capability are distinct:

- **Product category:** Research Workspace.
- **Interaction model:** Research Operator.
- **Technical capability:** one durable, bounded Research Agent.

The Research Workspace is the product; the Agent is the capability layer. Product
value is measured by usable papers, evidence, comparisons, notebooks, figures,
tables, and reports rather than conversation length, visible model activity, or the
number of agents and tools involved.

Local-first storage, execution approval, provenance, hashes, and sandboxing are
trust infrastructure. They must remain correct, but they are not the primary
navigation, product story, or visual emphasis.

## Research Operator Contract

Agent-native means that Spark continues an approved research task from real workspace
state instead of waiting for the user to orchestrate every tool call. The target is a
Research Operator, not an autonomous scientist.

The governing rule is **high autonomy in execution and bounded autonomy in scientific
decisions**.

Within an approved scope, Spark may:

- decompose the goal into typed steps and choose their execution order;
- run allowed read-only operations and approved tools;
- normalize, deduplicate, parse, index, and validate results;
- observe novelty, evidence coverage, failures, and lack of progress;
- continue, stop early, safely retry, or propose a plan revision;
- maintain drafts, comparison tables, artifacts, and recovery state; and
- resume from durable project state after cancellation, failure, or restart.

The user retains control of:

- the research question, material scope, budget, providers, and data disclosure;
- screening inclusion and exclusion judgments;
- method changes and actions that expand permissions or execution scope;
- interpretation of conflicting or insufficient evidence; and
- final scientific conclusions, acceptance, export, and publication.

Spark must never silently expand an approved query, provider, budget, method,
permission, or disclosure boundary; treat a model suggestion as evidence; present a
structural citation check as scientific correctness; activate a learned skill without
review; or carry project memory into another project without an explicit product
contract.

## Core Service Experience

The first-minute experience should be question-first:

1. The user states a bounded research question and intended deliverable.
2. Spark asks only questions that materially change the scope or method.
3. Spark creates or resumes a project and proposes the exact plan, boundaries,
   budgets, stopping policy, and expected artifacts.
4. The user confirms that immutable material-scope revision once.
5. Spark advances the task in the existing Papers, Screening, Extraction, Analysis,
   and Report workspaces.
6. The workspace always exposes the current action, completed work, remaining evidence
   gaps, next decision, and reason for any pause or failure.

Chat may help clarify intent, but it is not the product record. Plans, observations,
decisions, evidence, artifacts, and reviews are the durable record.

Capability-specific execution, remote-data, installation, deletion, and other
required approvals remain unchanged and are never implied by the scope confirmation.

## Capability Priorities

The product sequence is deliberately asymmetric:

1. **Literature Research Operator:** discovery, deduplication, screening, full-text
   evidence, structured comparison, cited synthesis, and restart recovery.
2. **Computational Research:** local dataset analysis, approved Python and notebook
   execution, figures, tables, and artifact lineage inside the same project.
3. **Research Continuity:** project-scoped facts, decisions, assumptions, open
   questions, evidence gaps, and context assembly.
4. **Controlled Learning:** reusable procedures proposed from verified experience,
   replay-tested, approved, versioned, reversible, and project-scoped by default.
5. **Adaptive Operation:** better tool selection, coverage-aware replanning, and
   bounded internal parallelism without a multi-agent product interface.

Multi-agent personas, a separate Agent destination, chain-of-thought, a general code
editor or terminal, global autonomous memory, a skill marketplace, team administration,
and autonomous scientific judgment are not near-term product priorities.

## Product Success Measures

Spark should optimize for research completion and trust, not AI activity. Primary
measures are:

- time from a bounded question to the first useful evidence-backed artifact;
- completion rate for the intended cited answer, comparison, report, or analysis;
- atomic claim support and exact-passage reopen success;
- unsupported-claim and unverified-strengthening rates;
- successful pause, failure, and restart recovery;
- number of material user interventions per completed run;
- reproducibility and lineage completeness of exported artifacts; and
- the user's ability to understand current state and next required action without
  reading a chat transcript or raw log.

## Brand Personality

Rigorous, calm, and capable. Spark should feel like a mature research product used
for sustained reading and comparison, not an AI demo, developer console, generic
chat application, or compliance dashboard.

## Anti-references

- A permanent seven-stage audit rail as the main information architecture.
- Security, local-storage, hash, or provenance labels repeated on every object.
- A terminal, code editor, or chat transcript as the dominant workspace.
- Large card walls, oversized empty states, ornamental metrics, and decorative AI
  gradients.
- Combining unrelated visual patterns from several competitors on one screen.
- Invented workflows or controls that are not grounded in a competitor reference,
  an implemented capability, or an explicit user request.

## Design Principles

1. **Research job first.** Organize the product around finding, screening, reading,
   extracting, comparing, analyzing, and writing.
2. **Competitor fidelity before invention.** Use Elicit as the primary interaction
   and layout reference. Use Consensus for question-first search and answer
   synthesis, and SciSpace for PDF reading and extraction, only on the relevant
   surfaces.
3. **One primary reference per screen.** A screen must name the competitor screen or
   captured reference it follows. Do not blend several products into a novel
   composition without user approval.
4. **Progressive trust details.** Show citations and source context where they help
   evaluate research. Put hashes, manifests, execution records, and detailed
   permissions in inspectors, details, and approval moments.
5. **Dense and familiar.** Prefer tables, split readers, filters, toolbars, and
   report structure over dashboards of equal-weight cards.

## Product Information Architecture

Global navigation should expose user-recognizable destinations:

- Home
- Library
- Workflows
- Projects
- Data
- Reports
- Settings

Project-level navigation depends on the research job:

- Literature review: Overview, Papers, Screening, Extraction, Synthesis.
- Research answer: Answer, Papers, Notes, Report.
- Dataset analysis: Dataset, Analysis, Results, Notebook.

Project, Sources, Plan, Execution, Evidence, Results, and Review describe an internal
workflow lifecycle. They may appear as compact contextual progress while a task is
running, but they are not the default global navigation or a mandatory full-page
sequence.

## Competitive Reference Hierarchy

- **Elicit — primary:** home and workflow entry, paper search, screening tables,
  extraction tables, supporting quotes, reports, and export.
- **Consensus — secondary:** question-first search, synthesized answer, filters,
  paper result presentation, and full-text passage reopening.
- **SciSpace — secondary:** library, PDF reader, paper chat, custom extraction
  columns, and side-by-side source context.
- **Spark-owned:** dataset analysis, notebook and artifact continuity, desktop
  integration, and trust infrastructure.

## Current Design Phase

The active phase is **v1.3 Agent-native Discovery and Evidence Coverage**. The v1
packaged research loop, the Elicit-led Home, Papers, Screening, Extraction, Reader,
and Report surfaces, the Spark-owned Dataset workspace, the v1.1 single Research
Agent model connection, and the bounded v1.2 Dataset and local Literature Agent
loops are implemented. Do not resume the earlier competitive-capture or
reproduction sequence.

The approved visual and information-architecture surfaces remain frozen. Do not
add a separate Agent destination, a multi-Agent interface, a chat-first shell, or
a chain-of-thought transcript. Agent progress belongs in the existing research
workspace as structured plans, observations, decisions, tool results, evidence,
artifacts, and review states.

"Agent-native" means that the existing single Research Agent can advance
an approved research goal through a durable, bounded loop:

`understand -> plan -> execute one approved step -> observe -> decide -> apply,
ask, replan, or stop -> verify -> preserve`

Science Core owns the allowed actions, budgets, state transitions, recovery, and
completion invariants. A model may choose only among the currently allowed bounded
actions; it cannot expand data disclosure, permissions, methods, or execution
scope. Every material tool or model operation must have a durable identity,
structured result, postcondition, and provenance record. Unknown outcomes fail
closed and are never repeated as if they were known failures.

The v1.3 implementation sequence is deliberately narrow:

1. Add a real, provider-bounded paper discovery contract with an exact approved
   query set, provider set, result budget, download budget, and stopping policy.
2. Execute the approved queries through the pinned paper-search MCP with durable
   pending-before-send identity, structured observations, retry classification,
   restart recovery, deterministic normalization, and deduplication.
3. Let the Agent choose query order and stop early from novelty and evidence
   coverage observations. A query or scope outside the approved set requires an
   explicit revision; screening judgments and scientific conclusions remain human
   decisions.
4. Present real candidates, progress, failures, and inclusion in the existing
   Elicit-led Papers and Screening surfaces. Do not add an Agent page or redesign
   the frozen information architecture.
5. Evaluate outcome, process, and trust with fixed local provider fixtures plus an
   explicitly approved public-network baseline and packaged restart QA.

Literature research remains the default entry. When the user's approved goal also
requires a local dataset, the user selects which supported path runs first; Spark does
not silently combine the two workflows or expand the approved scope. Settings still
owns non-secret endpoint and model configuration, macOS Keychain owns the secret, and
each remote workflow still requires its existing per-run remote-data approval before
research content can leave the device. Public distribution still requires a clean
source baseline and Apple Developer ID signing/notarization.

## Accessibility & Inclusion

Support keyboard operation, visible focus, readable contrast, non-color-only state
communication, long academic titles, dense tables, narrow desktop windows, and
reduced motion. Accessibility should be built into the reproduced interaction, not
presented as a separate product feature.
