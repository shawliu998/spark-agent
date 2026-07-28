import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  DiscoveryCandidate,
  ResearchWorkflowSnapshot,
  WorkflowDiscoverySnapshot,
  WorkflowEvidenceCoverage,
} from "@spark/research-domain";
import { ResearchPage } from "./ResearchPage";
import { useToastStore } from "@/lib/toast";

const core = vi.hoisted(() => ({
  health: vi.fn(),
  listProjects: vi.fn(),
  createProject: vi.fn(),
  renameProject: vi.fn(),
  archiveProject: vi.fn(),
  restoreProject: vi.fn(),
  listSources: vi.fn(),
  listCandidateTriageDecisions: vi.fn(),
  upsertCandidateTriageDecision: vi.fn(),
  listScreeningDecisions: vi.fn(),
  upsertScreeningDecision: vi.fn(),
  listEvidenceDirectionJudgments: vi.fn(),
  upsertEvidenceDirectionJudgment: vi.fn(),
  createDiscoveryRun: vi.fn(),
  getWorkflowDiscovery: vi.fn(),
  getWorkflowEvidenceCoverage: vi.fn(),
  importCslJsonCandidates: vi.fn(),
  importPdf: vi.fn(),
  configurationError: null as string | null,
  runtimeStatus: null as null | { state: string },
  getRuntimeStatus: vi.fn(),
  retryRuntime: vi.fn(),
}));

const workflow = vi.hoisted(() => ({
  workflows: [] as ResearchWorkflowSnapshot[],
  selectedWorkflowId: null,
  snapshot: null as ResearchWorkflowSnapshot | null,
  events: [],
  interactions: [],
  loadingList: false,
  loadingSnapshot: false,
  loadingInteractions: false,
  mutating: false,
  connection: "idle" as const,
  error: null,
  selectWorkflow: vi.fn(),
  startNew: vi.fn(),
  refresh: vi.fn(async () => {}),
  create: vi.fn(async () => {}),
  respondToInteraction: vi.fn(async () => {}),
  approvePlan: vi.fn(async () => {}),
  decideAnalysis: vi.fn(async () => {}),
  acceptReviewWarnings: vi.fn(async () => {}),
  cancel: vi.fn(async () => {}),
  retry: vi.fn(async () => {}),
  resume: vi.fn(async () => {}),
}));

vi.mock("@/lib/scienceCore", () => ({
  scienceCore: core,
  get scienceCoreConfigurationError() {
    return core.configurationError;
  },
  getScienceCoreRuntimeStatus: core.getRuntimeStatus,
  retryScienceCoreRuntime: core.retryRuntime,
}));
vi.mock("./research/useResearchWorkflow", () => ({
  useResearchWorkflow: () => workflow,
}));
vi.mock("./research/ResearchMemoryPanel", () => ({
  useResearchMemoryWorkspace: () => ({}),
}));
vi.mock("./research/CompetitiveResearchWorkspace", () => ({
  CompetitiveResearchWorkspace: ({
    projectTitle,
    screeningDecisions,
    evidenceDirections,
    serviceReady,
    serviceUnavailableReason,
    projectListLoading,
    projectListResolved,
    projectListError,
    onRetryProjectList,
    creatingProject,
    createProjectError,
    onCreateProject,
    onOpenWorkflow,
    onAttachCandidatePdfRequest,
    onImportCslJsonRequest,
    readerSourceRequest,
    onStartDiscovery,
    onStartFirstDiscovery,
    onStartSynthesis,
    discoveryActive,
    discoverySnapshot,
    evidenceCoverage,
    onUpsertEvidenceDirection,
  }: {
    projectTitle: string | null;
    screeningDecisions: Array<{ sourceId: string }>;
    evidenceDirections?: Array<{ sourceId: string; direction: string; rowVersion: number }>;
    serviceReady: boolean;
    serviceUnavailableReason: string | null;
    projectListLoading?: boolean;
    projectListResolved?: boolean;
    projectListError?: string | null;
    onRetryProjectList?: () => void;
    creatingProject: boolean;
    createProjectError?: string | null;
    onCreateProject: (title: string) => Promise<boolean>;
    onOpenWorkflow: () => void;
    onAttachCandidatePdfRequest?: (
      candidate: DiscoveryCandidate,
      workflowId: string,
    ) => void;
    onImportCslJsonRequest?: () => void;
    readerSourceRequest?: { sourceId: string } | null;
    onStartDiscovery?: (
      question: string,
      provider: "crossref" | "openalex" | "crossref-openalex",
    ) => Promise<boolean>;
    onStartFirstDiscovery?: (
      question: string,
      provider: "crossref" | "openalex" | "crossref-openalex",
    ) => Promise<boolean>;
    onStartSynthesis?: (goal: string, sourceIds: string[]) => Promise<void>;
    discoveryActive?: boolean;
    discoverySnapshot?: WorkflowDiscoverySnapshot | null;
    evidenceCoverage?: WorkflowEvidenceCoverage | null;
    onUpsertEvidenceDirection?: (
      sourceId: string,
      input: { direction: "supporting" | "mixed" | "insufficient"; expectedVersion: number },
    ) => Promise<unknown>;
  }) => {
    const [newProjectTitle, setNewProjectTitle] = useState("");
    if (projectListLoading && !projectListResolved) return <div aria-busy="true">Loading projects</div>;
    if (!projectListResolved && projectListError) return <div role="alert"><span>{projectListError}</span><button type="button" onClick={onRetryProjectList}>Retry project list</button></div>;
    return (
    <div>
      <span>Literature workspace</span>
      <span>Project: {projectTitle}</span>
      <span>Screening: {screeningDecisions.map((item) => item.sourceId).join(",")}</span>
      <span>Directions: {evidenceDirections?.map((item) => `${item.sourceId}:${item.direction}`).join(",") ?? "none"}</span>
      <span>Core ready: {String(serviceReady)}</span>
      <span>Reader request: {readerSourceRequest?.sourceId ?? "none"}</span>
      <span>Discovery active: {String(discoveryActive)}</span>
      <span>Discovery status: {discoverySnapshot?.workflowStatus ?? "none"}</span>
      <span>Discovery candidates: {discoverySnapshot?.candidates.items.map((candidate) => candidate.title).join(",") ?? "none"}</span>
      <span>Discovery progress: {discoverySnapshot ? `${discoverySnapshot.summary.succeededOperations}/${discoverySnapshot.summary.totalOperations}` : "none"}</span>
      <span>Discovery failures: {discoverySnapshot?.operations.map((operation) => operation.errorCode ?? operation.status).join(",") ?? "none"}</span>
      <span>Claim coverage: {evidenceCoverage ? `${evidenceCoverage.claimCoverage.state}:${evidenceCoverage.claimCoverage.evidenceLinkedClaimCount}/${evidenceCoverage.claimCoverage.totalClaimCount}` : "none"}</span>
      {serviceUnavailableReason && <span>{serviceUnavailableReason}</span>}
      {!projectTitle && (
        <form onSubmit={(event) => { event.preventDefault(); void onCreateProject(newProjectTitle).then((created) => { if (created) setNewProjectTitle(""); }); }}>
          <label htmlFor="test-project-title">Create project</label>
          <input id="test-project-title" value={newProjectTitle} onChange={(event) => setNewProjectTitle(event.target.value)} />
          <button type="submit" disabled={!serviceReady || creatingProject || !newProjectTitle.trim()}>Create project</button>
          {createProjectError && <span role="alert">{createProjectError}</span>}
        </form>
      )}
      <button type="button" onClick={onOpenWorkflow}>Open workflow view</button>
      <button
        type="button"
        onClick={() =>
          onAttachCandidatePdfRequest?.(
            {
              id: "candidate-1",
              provider: "crossref",
              providerId: "10.1/example",
              title: "Candidate title",
              authors: [],
              abstract: null,
              publicationDate: null,
              doi: "10.1/example",
              arxivId: null,
              pmid: null,
              candidateSha256: "c".repeat(64),
              trustClassification: "untrusted-metadata",
              fullTextVerification: "not-verified",
              importAvailability: "manual-pdf-required",
              landingPageAvailability: "not-reported",
              openAccessPdfAvailability: "not-reported",
              occurrences: [{
                invocationId: "invocation-1",
                queryId: "query-1",
                provider: "crossref",
                attempt: 1,
                rank: 1,
                rawItemSha256: "d".repeat(64),
              }],
            },
            "discovery-workflow",
          )
        }
      >
        Attach candidate PDF
      </button>
      <button type="button" onClick={onImportCslJsonRequest}>
        Import candidate CSL-JSON
      </button>
      <button type="button" onClick={() => void onStartDiscovery?.("  Which methods evaluate hallucinations?  ", "openalex")}>Start discovery</button>
      <button type="button" onClick={() => void onStartDiscovery?.("  Which methods evaluate hallucinations?  ", "crossref")}>Start Crossref discovery</button>
      <button type="button" onClick={() => void onStartDiscovery?.("  Which methods evaluate hallucinations?  ", "crossref-openalex")}>Start Agent discovery</button>
      <button type="button" onClick={() => void onStartDiscovery?.("x".repeat(501), "crossref-openalex")}>Start oversized discovery</button>
      <button type="button" onClick={() => void onStartFirstDiscovery?.("  How do research agents preserve provenance?  ", "openalex")}>Start first discovery</button>
      <button type="button" onClick={() => void onStartSynthesis?.("  Compare papers  ", ["paper-b", "paper-a", "paper-a"])}>Start synthesis</button>
      <button
        type="button"
        onClick={() => void onUpsertEvidenceDirection?.("paper-1", {
          direction: "mixed",
          expectedVersion: evidenceDirections?.[0]?.rowVersion ?? 0,
        })}
      >
        Confirm mixed direction
      </button>
    </div>
    );
  },
}));
vi.mock("./research/DatasetResearchWorkspace", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./research/DatasetResearchWorkspace")>();
  return {
    ...actual,
    DatasetResearchWorkspace: ({
      onOpenWorkflow,
      projectTitle,
    }: {
      onOpenWorkflow: () => void;
      projectTitle: string | null;
    }) => (
      <div>
        <span>Dataset workspace</span>
        <span>Dataset project: {projectTitle}</span>
        <button type="button" onClick={onOpenWorkflow}>Workflow details</button>
      </div>
    ),
  };
});
vi.mock("./research/WorkflowWorkspace", () => ({
  WorkflowWorkspace: () => <div>Workflow setup and details</div>,
}));
vi.mock("./research/ResearchLibrarySidebar", () => ({
  ResearchLibrarySidebar: ({
    onProjectChange,
    onRenameProject,
    onArchiveProject,
    onRestoreProject,
  }: {
    onProjectChange: (id: string) => void;
    onRenameProject?: (title: string) => void | Promise<void>;
    onArchiveProject?: () => void | Promise<void>;
    onRestoreProject?: () => void | Promise<void>;
  }) => (
    <div>
    <button type="button" onClick={() => onProjectChange("project-2")}>Switch to project 2</button>
      <button type="button" onClick={() => void onRenameProject?.("Renamed project")}>Rename project</button>
      <button type="button" onClick={() => void onArchiveProject?.()}>Archive project</button>
      <button type="button" onClick={() => void onRestoreProject?.()}>Restore project</button>
    </div>
  ),
}));
vi.mock("./research/ResearchInspector", () => ({ ResearchInspector: () => null }));
vi.mock("./research/LegacyQuestionPanel", () => ({ LegacyQuestionPanel: () => null }));

function persistedTerminalDiscoveryWorkflow(
  status: "completed" | "blocked",
): ResearchWorkflowSnapshot {
  return {
    workflow: {
      id: "persisted-discovery-workflow",
      projectId: "project-1",
      status,
    },
    plan: {
      spec: {
        planType: "paper-discovery",
        discoverySpecId: "persisted-discovery-spec",
        discoverySpecRevision: 1,
        discoverySpecSha256: "a".repeat(64),
      },
    },
  } as unknown as ResearchWorkflowSnapshot;
}

function terminalDiscoverySnapshot(
  status: "completed" | "blocked",
): WorkflowDiscoverySnapshot {
  const outcomeUnknown = status === "blocked";
  return {
    workflowId: "persisted-discovery-workflow",
    projectId: "project-1",
    workflowStatus: status,
    stopReason: null,
    latestAgentSelection: null,
    discoverySpecId: "persisted-discovery-spec",
    discoverySpecRevision: 1,
    discoverySpecSha256: "a".repeat(64),
    discoverySpecStatus: "approved",
    exactScope: {
      schemaVersion: "1",
      question: "Which methods evaluate hallucinations?",
      queries: [{
        id: "query-primary",
        query: "Which methods evaluate hallucinations?",
        providers: ["crossref"],
        yearFrom: null,
        yearTo: null,
        sort: "relevance",
        maxResultsPerProvider: 20,
      }],
      stopPolicy: { minUniqueCandidates: 1, maxAttempts: 1, maxConsecutiveNoNovelty: 1 },
      downloadOpenAccessPdfs: false,
      maxPdfDownloads: 0,
    },
    operations: [{
      operationKey: "crossref:query-primary",
      queryId: "query-primary",
      provider: "crossref",
      status: outcomeUnknown ? "outcome-unknown" : "succeeded",
      attempt: 1,
      invocationId: "invocation-primary",
      returnedCount: 1,
      novelCandidateCount: 1,
      duplicateCount: 0,
      candidateSetSha256: null,
      errorCode: outcomeUnknown ? "crossref-outcome-unknown" : null,
      retryClassification: outcomeUnknown ? "manual-review" : "safe-to-retry",
      createdAt: "2026-07-24T00:00:00Z",
      finishedAt: "2026-07-24T00:01:00Z",
    }],
    summary: {
      totalOperations: 1,
      notStartedOperations: 0,
      inProgressOperations: 0,
      succeededOperations: outcomeUnknown ? 0 : 1,
      failedOperations: 0,
      outcomeUnknownOperations: outcomeUnknown ? 1 : 0,
      cancelledOperations: 0,
      returnedCount: 1,
      novelCandidateCount: 1,
      duplicateCount: 0,
      uniqueCandidateCount: 1,
      occurrenceCount: 1,
    },
    candidates: {
      offset: 0,
      limit: 50,
      total: 1,
      hasMore: false,
      items: [{
        id: "persisted-candidate",
        provider: "crossref",
        providerId: "10.1000/persisted",
        title: "Persisted untrusted Crossref candidate",
        authors: ["Researcher"],
        abstract: null,
        publicationDate: "2026-07-24",
        doi: "10.1000/persisted",
        arxivId: null,
        pmid: null,
        candidateSha256: "b".repeat(64),
        trustClassification: "untrusted-metadata",
        fullTextVerification: "not-verified",
        importAvailability: "manual-pdf-required",
        landingPageAvailability: "not-reported",
        openAccessPdfAvailability: "not-reported",
        occurrences: [{
          invocationId: "invocation-primary",
          queryId: "query-primary",
          provider: "crossref",
          attempt: 1,
          rank: 1,
          rawItemSha256: "c".repeat(64),
        }],
      }],
    },
  };
}

function literatureCoverageWorkflowSnapshot(
  phase: "running" | "completed",
): ResearchWorkflowSnapshot {
  return {
    workflow: {
      id: "literature-workflow",
      projectId: "project-1",
      goal: "Compare the imported papers",
      mode: "autonomous",
      sourceIds: ["paper-1"],
      workflowType: "literature-synthesis",
      generationMode: "local-deterministic",
      status: phase,
      revision: phase === "running" ? 4 : 5,
      currentStepId: phase === "running" ? "task-synthesis" : null,
      planVersion: 1,
      retryCount: 0,
      statusReason: null,
      cancelRequestedAt: null,
      createdAt: "2026-07-24T00:00:00Z",
      updatedAt:
        phase === "running"
          ? "2026-07-24T00:01:00Z"
          : "2026-07-24T00:02:00Z",
      completedAt: phase === "completed" ? "2026-07-24T00:02:00Z" : null,
    },
    plan: {
      id: "literature-plan",
      version: 1,
      status: "approved",
      planSha256: "e".repeat(64),
      spec: {
        schemaVersion: "1",
        goal: "Compare the imported papers",
        steps: [],
      },
    },
    result:
      phase === "completed"
        ? {
            answerId: "literature-answer",
            summary: "The evidence-backed synthesis is ready.",
            generator: "local-deterministic",
            model: null,
            promptVersion: null,
            integrityStatus: "verified-frozen-v2",
            claims: [],
            unresolvedQuestions: [],
          }
        : null,
  } as unknown as ResearchWorkflowSnapshot;
}

function literatureCoverage(
  claimState: "not-generated" | "verified-frozen",
): WorkflowEvidenceCoverage {
  const generated = claimState === "verified-frozen";
  return {
    schemaVersion: "1",
    workflowId: "literature-workflow",
    projectId: "project-1",
    planId: "literature-plan",
    planVersion: 1,
    planSha256: "e".repeat(64),
    state: generated ? "reviewed" : "available",
    sourceSetSha256: "f".repeat(64),
    sourceBreadth: {
      frozenSourceCount: 1,
      sourcesWithCoveredEvidenceCount: generated ? 1 : 0,
      sourcesWithoutCoveredEvidenceCount: generated ? 0 : 1,
      verifiedReferencedSpanCount: generated ? 1 : 0,
    },
    facets: [],
    claimCoverage: {
      state: claimState,
      totalClaimCount: generated ? 1 : 0,
      evidenceLinkedClaimCount: generated ? 1 : 0,
      supportedClaimCount: generated ? 1 : 0,
      unresolvedQuestionCount: 0,
    },
    contradictionAssessment: "not-assessed",
  };
}

describe("ResearchPage dataset presentation navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useToastStore.setState({ toasts: [] });
    workflow.workflows = [];
    workflow.snapshot = null;
    workflow.startNew.mockImplementation(() => undefined);
    workflow.create.mockImplementation(async () => undefined);
    window.localStorage.clear();
    core.configurationError = null;
    core.runtimeStatus = null;
    core.getRuntimeStatus.mockImplementation(async () => core.runtimeStatus);
    core.retryRuntime.mockResolvedValue({ state: "starting" });
    core.health.mockResolvedValue({
      database: "ok",
      paperQa: "unavailable",
      modelGateway: "unconfigured",
      modelDestination: null,
    });
    core.listProjects.mockResolvedValue([
      { id: "project-1", title: "CSV analysis", createdAt: "2026-07-21T00:00:00Z" },
    ]);
    core.createProject.mockResolvedValue({
      id: "created-project",
      title: "Created project",
      projectPath: "projects/created-project",
      rowVersion: 1,
      archivedAt: null,
      createdAt: "2026-07-24T00:00:00Z",
    });
    core.listSources.mockResolvedValue([
      {
        id: "dataset-1",
        projectId: "project-1",
        title: "experiment.csv",
        sourceKind: "dataset",
        authors: [],
        doi: null,
        arxivId: null,
        localPath: "data/raw/experiment.csv",
        publicationDate: null,
        ingestionStatus: "ready",
        contentHash: "a".repeat(64),
        pageCount: null,
        createdAt: "2026-07-21T00:00:00Z",
      },
    ]);
    core.listCandidateTriageDecisions.mockResolvedValue([]);
    core.listScreeningDecisions.mockResolvedValue([]);
    core.listEvidenceDirectionJudgments.mockResolvedValue([]);
    core.upsertEvidenceDirectionJudgment.mockResolvedValue({
      id: "direction-1",
      projectId: "project-1",
      answerId: "literature-answer",
      sourceId: "paper-1",
      direction: "mixed",
      rowVersion: 2,
      createdAt: "2026-07-26T00:00:00Z",
      updatedAt: "2026-07-26T00:00:00Z",
    });
    core.createDiscoveryRun.mockResolvedValue({
      workflow: {
        id: "discovery-workflow",
        projectId: "project-1",
        sourceIds: [],
      },
      plan: { spec: { planType: "paper-discovery" } },
    });
    core.getWorkflowDiscovery.mockResolvedValue(null);
    core.getWorkflowEvidenceCoverage.mockResolvedValue(null);
  });

  it("shows list loading and retries a real project-list failure", async () => {
    core.listProjects.mockReturnValueOnce(new Promise(() => {}));
    const loadingView = render(<MemoryRouter><ResearchPage /></MemoryRouter>);
    expect(await screen.findByText("Loading projects")).toBeInTheDocument();
    loadingView.unmount();

    core.listProjects.mockRejectedValueOnce(new Error("project list unavailable"));
    render(<MemoryRouter><ResearchPage /></MemoryRouter>);
    expect(await screen.findByRole("alert")).toHaveTextContent("project list unavailable");
    core.listProjects.mockResolvedValueOnce([]);
    fireEvent.click(screen.getByRole("button", { name: "Retry project list" }));
    await waitFor(() => expect(core.listProjects).toHaveBeenCalledTimes(3));
  });

  it("creates a real project, preserves failed input, and coalesces double submit", async () => {
    core.listProjects.mockResolvedValue([]);
    let resolveCreate!: (value: unknown) => void;
    core.createProject.mockReturnValueOnce(new Promise((resolve) => { resolveCreate = resolve; }));
    render(<MemoryRouter><ResearchPage /></MemoryRouter>);
    const input = await screen.findByLabelText("Create project");
    fireEvent.change(input, { target: { value: "  Evidence map  " } });
    const submit = screen.getByRole("button", { name: "Create project" });
    fireEvent.click(submit);
    fireEvent.click(submit);
    expect(core.createProject).toHaveBeenCalledTimes(1);
    expect(core.createProject).toHaveBeenCalledWith(
      { title: "Evidence map" },
      { signal: expect.any(AbortSignal) },
    );
    resolveCreate({ id: "created-project", title: "Evidence map", projectPath: "projects/evidence-map", rowVersion: 1, archivedAt: null });
    expect(await screen.findByText("Dataset project: Evidence map")).toBeInTheDocument();
  });

  it("keeps failed project creation input and does not commit an unmounted response", async () => {
    core.listProjects.mockResolvedValue([]);
    core.createProject.mockRejectedValueOnce(new Error("create failed"));
    const view = render(<MemoryRouter><ResearchPage /></MemoryRouter>);
    const input = await screen.findByLabelText("Create project");
    fireEvent.change(input, { target: { value: "Keep this title" } });
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("create failed");
    expect(screen.getByDisplayValue("Keep this title")).toBeInTheDocument();

    let resolveLate!: (value: unknown) => void;
    core.createProject.mockReturnValueOnce(new Promise((resolve) => { resolveLate = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "Create project" }));
    view.unmount();
    resolveLate({ id: "late-project", title: "Late project", projectPath: "projects/late-project", rowVersion: 1, archivedAt: null });
    await waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(0));
  });

  it("does not apply a late project rename after switching projects", async () => {
    let resolveRename!: (value: unknown) => void;
    core.listProjects.mockResolvedValue([
      { id: "project-1", title: "First", rowVersion: 1, archivedAt: null, createdAt: "2026-07-22T00:00:00Z" },
      { id: "project-2", title: "Second", rowVersion: 1, archivedAt: null, createdAt: "2026-07-21T00:00:00Z" },
    ]);
    core.listSources.mockResolvedValue([]);
    core.renameProject.mockReturnValue(new Promise((resolve) => { resolveRename = resolve; }));

    render(<MemoryRouter><ResearchPage /></MemoryRouter>);
    expect(await screen.findByText("Project: First")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open workflow view" }));
    fireEvent.click(screen.getByRole("button", { name: "Rename project" }));
    expect(core.renameProject).toHaveBeenCalledWith(
      "project-1",
      "Renamed project",
      1,
      expect.objectContaining({ idempotencyKey: expect.any(String) }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Switch to project 2" }));
    expect(await screen.findByRole("heading", { name: "Second" })).toBeInTheDocument();
    resolveRename({
      id: "project-1",
      title: "Renamed project",
      rowVersion: 2,
      archivedAt: null,
    });
    await waitFor(() => expect(screen.getByRole("heading", { name: "Second" })).toBeInTheDocument());
  });

  it("surfaces project mutation errors through the existing toast boundary", async () => {
    core.renameProject.mockRejectedValue(new Error("Project changed; reload it."));
    render(<MemoryRouter><ResearchPage /></MemoryRouter>);
    await screen.findByText("Dataset workspace");
    fireEvent.click(screen.getByRole("button", { name: "Workflow details" }));
    fireEvent.click(screen.getByRole("button", { name: "Rename project" }));
    await waitFor(() => expect(useToastStore.getState().toasts.slice(-1)[0]?.message).toMatch(/Could not update project/));
  });

  it("allows a CSV-only default workspace to open workflow details and return to Dataset", async () => {
    render(
      <MemoryRouter>
        <ResearchPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Dataset workspace")).toBeInTheDocument();
    expect(core.listScreeningDecisions).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Workflow details" }));
    expect(await screen.findByText("Workflow setup and details")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Dataset analysis" }));
    expect(await screen.findByText("Dataset workspace")).toBeInTheDocument();
  });

  it("restores a valid saved project across remounts", async () => {
    window.localStorage.setItem("spark.research.lastProjectId", "project-saved");
    core.listProjects.mockResolvedValue([
      { id: "project-newest", title: "Newest", createdAt: "2026-07-22T00:00:00Z" },
      { id: "project-saved", title: "Saved", createdAt: "2026-07-20T00:00:00Z" },
    ]);
    core.listSources.mockResolvedValue([]);

    render(<MemoryRouter><ResearchPage /></MemoryRouter>);
    await waitFor(() => expect(core.listSources).toHaveBeenCalledWith("project-saved"));
    expect(window.localStorage.getItem("spark.research.lastProjectId")).toBe("project-saved");
  });

  it("falls back to the newest valid project when a saved project no longer exists", async () => {
    window.localStorage.setItem("spark.research.lastProjectId", "project-deleted");
    core.listProjects.mockResolvedValue([
      { id: "project-newest", title: "Newest", createdAt: "2026-07-22T00:00:00Z" },
      { id: "project-older", title: "Older", createdAt: "2026-07-20T00:00:00Z" },
    ]);
    core.listSources.mockResolvedValue([]);

    render(<MemoryRouter><ResearchPage /></MemoryRouter>);
    await waitFor(() => expect(core.listSources).toHaveBeenCalledWith("project-newest"));
    await waitFor(() => expect(window.localStorage.getItem("spark.research.lastProjectId")).toBe("project-newest"));
  });

  it("does not carry a cancelled Candidate attachment into the next ordinary PDF import", async () => {
    core.listSources.mockResolvedValue([]);
    core.importPdf.mockResolvedValue({
      id: "ordinary-source",
      projectId: "project-1",
      title: "Ordinary source",
      sourceKind: "pdf",
      authors: [],
      doi: null,
      arxivId: null,
      localPath: "papers/ordinary.pdf",
      publicationDate: null,
      ingestionStatus: "ready",
      contentHash: "e".repeat(64),
      pageCount: 1,
      createdAt: "2026-07-24T00:00:00Z",
    });
    const { container } = render(
      <MemoryRouter>
        <ResearchPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Literature workspace")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Attach candidate PDF" }));
    const candidateInput = container.querySelector<HTMLInputElement>(
      'input[data-pdf-input="candidate"]',
    );
    const standardInput = container.querySelector<HTMLInputElement>(
      'input[data-pdf-input="standard"]',
    );
    expect(candidateInput).not.toBeNull();
    expect(standardInput).not.toBeNull();
    fireEvent(candidateInput!, new Event("cancel", { bubbles: true }));

    const ordinaryPdf = new File(["%PDF-1.7 ordinary"], "ordinary.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(standardInput!, { target: { files: [ordinaryPdf] } });

    await waitFor(() =>
      expect(core.importPdf).toHaveBeenCalledWith(
        "project-1",
        ordinaryPdf,
        undefined,
      ),
    );
    expect(core.importPdf).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Reader request: none")).toBeInTheDocument();
  });

  it("does not send a request when the CSL-JSON picker is cancelled", async () => {
    core.listSources.mockResolvedValue([]);
    const { container } = render(
      <MemoryRouter>
        <ResearchPage />
      </MemoryRouter>,
    );
    expect(await screen.findByText("Literature workspace")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Import candidate CSL-JSON" }));
    const input = container.querySelector<HTMLInputElement>(
      'input[data-csl-json-input="candidate"]',
    );
    expect(input).not.toBeNull();
    fireEvent.change(input!, { target: { files: [] } });
    expect(core.importCslJsonCandidates).not.toHaveBeenCalled();
  });

  it("passes the exact Candidate identity through the dedicated PDF input", async () => {
    core.listSources.mockResolvedValue([]);
    core.importPdf.mockResolvedValue({
      id: "attached-source",
      projectId: "project-1",
      title: "Candidate title",
      sourceKind: "pdf",
      authors: [],
      doi: "10.1/example",
      arxivId: null,
      localPath: "papers/attached.pdf",
      publicationDate: null,
      ingestionStatus: "ready",
      contentHash: "e".repeat(64),
      pageCount: 1,
      createdAt: "2026-07-24T00:00:00Z",
    });
    const { container } = render(
      <MemoryRouter>
        <ResearchPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Literature workspace")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Attach candidate PDF" }));
    const candidateInput = container.querySelector<HTMLInputElement>(
      'input[data-pdf-input="candidate"]',
    );
    expect(candidateInput).not.toBeNull();
    const candidatePdf = new File(["%PDF-1.7 candidate"], "candidate.pdf", {
      type: "application/pdf",
    });
    fireEvent.change(candidateInput!, { target: { files: [candidatePdf] } });

    await waitFor(() =>
      expect(core.importPdf).toHaveBeenCalledWith(
        "project-1",
        candidatePdf,
        {
          workflowId: "discovery-workflow",
          candidateId: "candidate-1",
          candidateSha256: "c".repeat(64),
          occurrenceInvocationId: "invocation-1",
        },
      ),
    );
    expect(screen.getByText("Reader request: attached-source")).toBeInTheDocument();
  });

  it("shows a stable reason and blocks core actions when a valid health response reports a database error", async () => {
    core.health.mockResolvedValue({
      status: "degraded",
      version: "0.1.0",
      database: "error",
      paperQa: "unavailable",
      modelGateway: "unconfigured",
      modelDestination: null,
      runtime: "unavailable",
    });
    core.listProjects.mockResolvedValue([]);
    core.listSources.mockResolvedValue([]);

    render(
      <MemoryRouter>
        <ResearchPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Science core database is unavailable.")).toBeInTheDocument();
    expect(screen.getByText("Core ready: false")).toBeInTheDocument();
  });

  it("does not call science core when its URL or token configuration is unavailable", async () => {
    core.configurationError = "Science core token is not configured";

    render(
      <MemoryRouter>
        <ResearchPage />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText(
        "Open Settings to check the local service configuration, then retry.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("Science core token is not configured"),
    ).not.toBeInTheDocument();
    expect(core.health).not.toHaveBeenCalled();
    expect(core.listProjects).not.toHaveBeenCalled();
  });

  it("coalesces a double-click on Research refresh while retrying a failed runtime", async () => {
    core.runtimeStatus = { state: "failed" };
    render(
      <MemoryRouter>
        <ResearchPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Dataset workspace")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Workflow details" }));
    const refresh = screen.getByRole("button", { name: "Refresh research workspace" });
    fireEvent.click(refresh);
    fireEvent.click(refresh);

    await waitFor(() => expect(core.retryRuntime).toHaveBeenCalledTimes(1));
  });

  it("does not apply a late screening list response to a newly selected project", async () => {
    let resolveOld: ((value: Array<{ sourceId: string }>) => void) | undefined;
    const oldResponse = new Promise<Array<{ sourceId: string }>>((resolve) => {
      resolveOld = resolve;
    });
    core.listProjects.mockResolvedValue([
      { id: "project-1", title: "First", createdAt: "2026-07-22T00:00:00Z" },
      { id: "project-2", title: "Second", createdAt: "2026-07-21T00:00:00Z" },
    ]);
    core.listSources.mockImplementation(async (projectId: string) => [{
      id: projectId === "project-1" ? "source-1" : "source-2",
      projectId,
      title: "Paper",
      sourceKind: "pdf",
      authors: [],
      doi: null,
      arxivId: null,
      localPath: "papers/paper.pdf",
      publicationDate: null,
      ingestionStatus: "ready",
      contentHash: "a".repeat(64),
      pageCount: 1,
      createdAt: "2026-07-22T00:00:00Z",
    }]);
    core.listScreeningDecisions.mockImplementation((projectId: string) =>
      projectId === "project-1" ? oldResponse : Promise.resolve([]),
    );

    render(<MemoryRouter><ResearchPage /></MemoryRouter>);
    await waitFor(() => expect(core.listScreeningDecisions).toHaveBeenCalledWith(
      "project-1",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
    fireEvent.click(screen.getByRole("button", { name: "Open workflow view" }));
    fireEvent.click(screen.getByRole("button", { name: "Switch to project 2" }));
    await waitFor(() => expect(core.listScreeningDecisions).toHaveBeenCalledWith(
      "project-2",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
    resolveOld?.([{ sourceId: "source-1" }]);
    fireEvent.click(screen.getByRole("button", { name: "Literature review" }));

    expect(await screen.findByText("Project: Second")).toBeInTheDocument();
    expect(screen.getByText("Screening:")).toBeInTheDocument();
    expect(screen.queryByText(/source-1/)).not.toBeInTheDocument();
  });

  it("refetches claim coverage when the same literature workflow snapshot advances", async () => {
    const running = literatureCoverageWorkflowSnapshot("running");
    const completed = literatureCoverageWorkflowSnapshot("completed");
    workflow.snapshot = running;
    workflow.workflows = [running];
    core.listSources.mockResolvedValue([
      {
        id: "paper-1",
        projectId: "project-1",
        title: "Evidence paper",
        sourceKind: "pdf",
        authors: [],
        doi: null,
        arxivId: null,
        localPath: "papers/evidence.pdf",
        publicationDate: null,
        ingestionStatus: "ready",
        contentHash: "a".repeat(64),
        pageCount: 1,
        pageManifestHash: "b".repeat(64),
        createdAt: "2026-07-24T00:00:00Z",
      },
    ]);
    core.getWorkflowEvidenceCoverage.mockImplementation(async () =>
      literatureCoverage(
        workflow.snapshot?.workflow.status === "completed"
          ? "verified-frozen"
          : "not-generated",
      ),
    );

    const view = render(<MemoryRouter><ResearchPage /></MemoryRouter>);

    expect(await screen.findByText("Claim coverage: not-generated:0/0")).toBeInTheDocument();
    const callsBeforeAdvance = core.getWorkflowEvidenceCoverage.mock.calls.length;
    expect(callsBeforeAdvance).toBeGreaterThan(0);
    expect(core.getWorkflowEvidenceCoverage).toHaveBeenLastCalledWith(
      "literature-workflow",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );

    workflow.snapshot = completed;
    workflow.workflows = [completed];
    view.rerender(<MemoryRouter><ResearchPage /></MemoryRouter>);

    expect(await screen.findByText("Claim coverage: verified-frozen:1/1")).toBeInTheDocument();
    expect(core.getWorkflowEvidenceCoverage.mock.calls.length).toBeGreaterThan(
      callsBeforeAdvance,
    );
    expect(core.getWorkflowEvidenceCoverage).toHaveBeenLastCalledWith(
      "literature-workflow",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("loads and updates evidence directions for the completed answer identity", async () => {
    const completed = literatureCoverageWorkflowSnapshot("completed");
    workflow.snapshot = completed;
    workflow.workflows = [completed];
    core.listSources.mockResolvedValue([{
      id: "paper-1",
      projectId: "project-1",
      title: "Evidence paper",
      sourceKind: "pdf",
      authors: [],
      doi: null,
      arxivId: null,
      localPath: "papers/evidence.pdf",
      publicationDate: null,
      ingestionStatus: "ready",
      contentHash: "a".repeat(64),
      pageCount: 1,
      createdAt: "2026-07-24T00:00:00Z",
    }]);
    core.listEvidenceDirectionJudgments.mockResolvedValue([{
      id: "direction-1",
      projectId: "project-1",
      answerId: "literature-answer",
      sourceId: "paper-1",
      direction: "supporting",
      rowVersion: 1,
      createdAt: "2026-07-26T00:00:00Z",
      updatedAt: "2026-07-26T00:00:00Z",
    }]);

    render(<MemoryRouter><ResearchPage /></MemoryRouter>);

    expect(await screen.findByText("Directions: paper-1:supporting")).toBeInTheDocument();
    expect(core.listEvidenceDirectionJudgments).toHaveBeenCalledWith(
      "project-1",
      "literature-answer",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Confirm mixed direction" }));
    await waitFor(() => expect(core.upsertEvidenceDirectionJudgment).toHaveBeenCalledWith(
      "project-1",
      "literature-answer",
      "paper-1",
      { direction: "mixed", expectedVersion: 1 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
  });

  it("starts a local autonomous synthesis in startNew then create order", async () => {
    const calls: string[] = [];
    workflow.startNew.mockImplementation(() => calls.push("startNew"));
    workflow.create.mockImplementation(async (...args: unknown[]) => {
      calls.push("create");
      expect(args).toEqual([
        "Compare papers",
        { mode: "autonomous", sourceIds: ["paper-a", "paper-b"], remoteDataApproved: false },
      ]);
    });
    core.listSources.mockResolvedValue([
      {
        id: "paper-a", projectId: "project-1", title: "A", sourceKind: "pdf", authors: [], doi: null, arxivId: null,
        localPath: "papers/a.pdf", publicationDate: null, ingestionStatus: "ready", contentHash: "a".repeat(64), pageCount: 1, createdAt: "2026-07-22T00:00:00Z",
      },
      {
        id: "paper-b", projectId: "project-1", title: "B", sourceKind: "pdf", authors: [], doi: null, arxivId: null,
        localPath: "papers/b.pdf", publicationDate: null, ingestionStatus: "ready", contentHash: "b".repeat(64), pageCount: 1, createdAt: "2026-07-22T00:00:00Z",
      },
    ]);
    render(<MemoryRouter><ResearchPage /></MemoryRouter>);
    await screen.findByText("Literature workspace");
    fireEvent.click(screen.getByRole("button", { name: "Start synthesis" }));
    await waitFor(() => expect(calls).toEqual(["startNew", "create"]));
  });

  it("creates and selects a strict zero-source discovery proposal before showing workflow approval", async () => {
    core.listSources.mockResolvedValue([]);
    render(<MemoryRouter><ResearchPage /></MemoryRouter>);
    await screen.findByText("Literature workspace");

    fireEvent.click(screen.getByRole("button", { name: "Start discovery" }));

    await waitFor(() => expect(core.createDiscoveryRun).toHaveBeenCalledTimes(1));
    const [projectId, input, options] = core.createDiscoveryRun.mock.calls[0];
    expect(projectId).toBe("project-1");
    expect(input).toEqual({
      goal: "Which methods evaluate hallucinations?",
      discoverySpec: {
        schemaVersion: "1",
        question: "Which methods evaluate hallucinations?",
        queries: [{
          id: "query-primary",
          query: "Which methods evaluate hallucinations?",
          providers: ["openalex"],
          yearFrom: null,
          yearTo: null,
          sort: "relevance",
          maxResultsPerProvider: 5,
        }],
        stopPolicy: {
          minUniqueCandidates: 5,
          maxAttempts: 1,
          maxConsecutiveNoNovelty: 1,
        },
        downloadOpenAccessPdfs: false,
        maxPdfDownloads: 0,
      },
    });
    expect(options).toEqual({
      idempotencyKey: expect.any(String),
      signal: expect.any(AbortSignal),
    });
    expect(workflow.selectWorkflow).toHaveBeenCalledWith("discovery-workflow");
    expect(await screen.findByText("Workflow setup and details")).toBeInTheDocument();
  });

  it("creates a first project from the question before creating its discovery proposal", async () => {
    core.listProjects.mockResolvedValue([]);
    core.listSources.mockResolvedValue([]);
    core.createProject.mockResolvedValue({
      id: "created-project",
      title: "How do research agents preserve provenance?",
      projectPath: "projects/created-project",
      rowVersion: 1,
      archivedAt: null,
    });
    core.createDiscoveryRun.mockResolvedValue({
      workflow: {
        id: "first-discovery-workflow",
        projectId: "created-project",
        sourceIds: [],
      },
      plan: { spec: { planType: "paper-discovery" } },
    });

    render(<MemoryRouter><ResearchPage /></MemoryRouter>);
    await screen.findByText("Literature workspace");
    fireEvent.click(screen.getByRole("button", { name: "Start first discovery" }));

    await waitFor(() => expect(core.createProject).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(core.createDiscoveryRun).toHaveBeenCalledTimes(1));
    expect(core.createProject).toHaveBeenCalledWith(
      { title: "How do research agents preserve provenance?" },
      { signal: expect.any(AbortSignal) },
    );
    expect(core.createProject.mock.invocationCallOrder[0]).toBeLessThan(
      core.createDiscoveryRun.mock.invocationCallOrder[0],
    );
    expect(core.createDiscoveryRun.mock.calls[0][0]).toBe("created-project");
    expect(core.createDiscoveryRun.mock.calls[0][1].discoverySpec.queries[0]).toMatchObject({
      query: "How do research agents preserve provenance?",
      providers: ["openalex"],
      maxResultsPerProvider: 5,
    });
  });

  it("preserves Crossref as an explicit single-provider discovery option", async () => {
    core.listSources.mockResolvedValue([]);
    render(<MemoryRouter><ResearchPage /></MemoryRouter>);
    await screen.findByText("Literature workspace");

    fireEvent.click(screen.getByRole("button", { name: "Start Crossref discovery" }));

    await waitFor(() => expect(core.createDiscoveryRun).toHaveBeenCalledTimes(1));
    expect(
      core.createDiscoveryRun.mock.calls[0][1].discoverySpec.queries,
    ).toEqual([
      expect.objectContaining({ providers: ["crossref"] }),
    ]);
  });

  it("creates an approved two-provider scope for the bounded Agent loop", async () => {
    core.listSources.mockResolvedValue([]);
    render(<MemoryRouter><ResearchPage /></MemoryRouter>);
    await screen.findByText("Literature workspace");

    fireEvent.click(screen.getByRole("button", { name: "Start Agent discovery" }));

    await waitFor(() => expect(core.createDiscoveryRun).toHaveBeenCalledTimes(1));
    expect(core.createDiscoveryRun.mock.calls[0][1]).toEqual({
      goal: "Which methods evaluate hallucinations?",
      discoverySpec: {
        schemaVersion: "1",
        question: "Which methods evaluate hallucinations?",
        queries: [{
          id: "query-primary",
          query: "Which methods evaluate hallucinations?",
          providers: ["crossref", "openalex"],
          yearFrom: null,
          yearTo: null,
          sort: "relevance",
          maxResultsPerProvider: 5,
        }],
        stopPolicy: {
          minUniqueCandidates: 5,
          maxAttempts: 2,
          maxConsecutiveNoNovelty: 2,
        },
        downloadOpenAccessPdfs: false,
        maxPdfDownloads: 0,
      },
    });
  });

  it("keeps an oversized exact query local instead of sending an invalid proposal", async () => {
    core.listSources.mockResolvedValue([]);
    render(<MemoryRouter><ResearchPage /></MemoryRouter>);
    await screen.findByText("Literature workspace");

    fireEvent.click(screen.getByRole("button", { name: "Start oversized discovery" }));

    expect(core.createDiscoveryRun).not.toHaveBeenCalled();
  });

  it.each(["completed", "blocked"] as const)(
    "loads persisted %s Crossref discovery after a new mount without polling",
    async (status) => {
      const snapshot = terminalDiscoverySnapshot(status);
      workflow.workflows = [persistedTerminalDiscoveryWorkflow(status)];
      core.listSources.mockResolvedValue([]);
      core.getWorkflowDiscovery.mockResolvedValue(snapshot);
      vi.useFakeTimers();
      try {
        render(<MemoryRouter><ResearchPage /></MemoryRouter>);

        await vi.waitFor(() => expect(core.getWorkflowDiscovery).toHaveBeenCalledWith(
          "persisted-discovery-workflow",
          expect.objectContaining({ offset: 0, limit: 50, signal: expect.any(AbortSignal) }),
        ));
        await vi.waitFor(() => expect(core.listCandidateTriageDecisions).toHaveBeenCalledWith(
          "project-1",
          expect.objectContaining({ signal: expect.any(AbortSignal) }),
        ));
        await vi.waitFor(() => expect(screen.getByText(`Discovery status: ${status}`)).toBeInTheDocument());
        expect(screen.getByText("Discovery candidates: Persisted untrusted Crossref candidate")).toBeInTheDocument();
        expect(screen.getByText(`Discovery progress: ${status === "blocked" ? "0/1" : "1/1"}`)).toBeInTheDocument();
        expect(screen.getByText(
          `Discovery failures: ${status === "blocked" ? "crossref-outcome-unknown" : "succeeded"}`,
        )).toBeInTheDocument();
        await act(async () => {
          await vi.advanceTimersByTimeAsync(5_001);
        });
        expect(core.getWorkflowDiscovery).toHaveBeenCalledTimes(1);
      } finally {
        vi.useRealTimers();
      }
    },
  );

  it("shows the discovery run selected in the workflow sidebar instead of an older run", async () => {
    const older = persistedTerminalDiscoveryWorkflow("blocked");
    const selected = {
      ...persistedTerminalDiscoveryWorkflow("blocked"),
      workflow: {
        ...persistedTerminalDiscoveryWorkflow("blocked").workflow,
        id: "selected-discovery-workflow",
        updatedAt: "2026-07-25T16:31:00Z",
      },
      plan: {
        ...persistedTerminalDiscoveryWorkflow("blocked").plan,
        spec: {
          ...persistedTerminalDiscoveryWorkflow("blocked").plan?.spec,
          discoverySpecId: "selected-discovery-spec",
          discoverySpecSha256: "b".repeat(64),
        },
      },
    } as ResearchWorkflowSnapshot;
    const selectedSnapshot = {
      ...terminalDiscoverySnapshot("blocked"),
      workflowId: "selected-discovery-workflow",
      discoverySpecId: "selected-discovery-spec",
      discoverySpecSha256: "b".repeat(64),
    };
    workflow.workflows = [older, selected];
    workflow.snapshot = selected;
    core.listSources.mockResolvedValue([]);
    core.getWorkflowDiscovery.mockResolvedValue(selectedSnapshot);

    render(<MemoryRouter><ResearchPage /></MemoryRouter>);

    await waitFor(() => expect(core.getWorkflowDiscovery).toHaveBeenCalledWith(
      "selected-discovery-workflow",
      expect.objectContaining({ offset: 0, limit: 50, signal: expect.any(AbortSignal) }),
    ));
    expect(screen.getByText("Discovery status: blocked")).toBeInTheDocument();
  });

  it("reuses an exact active synthesis instead of creating another run", async () => {
    workflow.workflows = [{
      workflow: {
        id: "cancelled", projectId: "project-1", goal: "Compare papers", mode: "autonomous",
        sourceIds: ["paper-a", "paper-b"], workflowType: "literature-synthesis", generationMode: "local-deterministic", status: "cancelled",
      },
    } as unknown as ResearchWorkflowSnapshot, {
      workflow: {
        id: "existing", projectId: "project-1", goal: "Compare papers", mode: "autonomous",
        sourceIds: ["paper-a", "paper-b"], workflowType: "literature-synthesis", generationMode: "local-deterministic", status: "running",
      },
    } as unknown as ResearchWorkflowSnapshot];
    core.listSources.mockResolvedValue([
      {
        id: "paper-a", projectId: "project-1", title: "A", sourceKind: "pdf", authors: [], doi: null, arxivId: null,
        localPath: "papers/a.pdf", publicationDate: null, ingestionStatus: "ready", contentHash: "a".repeat(64), pageCount: 1, createdAt: "2026-07-22T00:00:00Z",
      },
      {
        id: "paper-b", projectId: "project-1", title: "B", sourceKind: "pdf", authors: [], doi: null, arxivId: null,
        localPath: "papers/b.pdf", publicationDate: null, ingestionStatus: "ready", contentHash: "b".repeat(64), pageCount: 1, createdAt: "2026-07-22T00:00:00Z",
      },
    ]);
    render(<MemoryRouter><ResearchPage /></MemoryRouter>);
    await screen.findByText("Literature workspace");
    fireEvent.click(screen.getByRole("button", { name: "Start synthesis" }));
    await waitFor(() => expect(workflow.selectWorkflow).toHaveBeenCalledWith("existing"));
    expect(workflow.startNew).not.toHaveBeenCalled();
    expect(workflow.create).not.toHaveBeenCalled();
  });
});
