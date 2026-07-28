import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  DatasetAnalysisWorkflowSnapshot,
  ResearchSource,
} from "@spark/research-domain";
import { DatasetWorkflowDetails } from "./DatasetWorkflowDetails";
import {
  DatasetResearchWorkspace,
  datasetWorkflowDisplayState,
  shouldDefaultToDatasetWorkspace,
} from "./DatasetResearchWorkspace";
import { ResearchLibrarySidebar } from "./ResearchLibrarySidebar";
import { WorkflowProgress } from "./WorkflowExecution";
import { WorkflowWorkspace } from "./WorkflowWorkspace";
import i18n from "@/i18n";

const core = vi.hoisted(() => ({
  fetchArtifactBlob: vi.fn(),
}));

vi.mock("@/lib/scienceCore", () => ({ scienceCore: core }));

const DATASET_HASH = "a".repeat(64);
const PAYLOAD_HASH = "b".repeat(64);
const REVIEW_HASH = "c".repeat(64);
const SPEC_HASH = "1".repeat(64);
const PROFILE_HASH = "2".repeat(64);
const CODE_HASH = "3".repeat(64);
const RESULT_HASH = "4".repeat(64);

const READY_DATASET: ResearchSource = {
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
  contentHash: DATASET_HASH,
  pageCount: null,
  createdAt: "2026-07-15T00:00:00Z",
};

const READY_PDF: ResearchSource = {
  ...READY_DATASET,
  id: "pdf-1",
  title: "study.pdf",
  sourceKind: "pdf",
  localPath: "sources/study.pdf",
  contentHash: "d".repeat(64),
  pageCount: 2,
};

const handlers = {
  remoteDestination: null,
  onCreate: vi.fn(async () => {}),
  onApprovePlan: vi.fn(async () => {}),
  onDecideAnalysis: vi.fn(async () => {}),
  onAcceptReviewWarnings: vi.fn(async () => {}),
  onImportDataset: vi.fn(),
  onCancel: vi.fn(async () => {}),
  onRetry: vi.fn(async () => {}),
  onResume: vi.fn(async () => {}),
  onRefresh: vi.fn(async () => {}),
  onNew: vi.fn(),
  onSelectEvidence: vi.fn(),
  onOpenReview: vi.fn(),
  onOpenActivity: vi.fn(),
};

beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(URL, "createObjectURL", {
    configurable: true,
    value: vi.fn(() => "blob:verified-figure"),
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    configurable: true,
    value: vi.fn(),
  });
  core.fetchArtifactBlob.mockImplementation(async (artifactId: string) => {
    if (artifactId === "artifact-table") {
      return {
        text: async () => "group,mean\ncontrol,1.5\ntreated,2.5\n",
      } as Blob;
    }
    if (artifactId === "artifact-environment") {
      return { text: async () => '{"python":"3.12"}' } as Blob;
    }
    if (artifactId === "artifact-notebook") {
      return {
        text: async () => JSON.stringify({
          cells: [
            {
              cell_type: "code",
              execution_count: 1,
              metadata: {},
              source: ["summary = data.groupby('group').mean()\n", "summary"],
              outputs: [{ output_type: "stream", name: "stdout", text: ["group outcome\n", "treated 2.5\n"] }],
            },
          ],
          metadata: { kernelspec: { language: "python" } },
          nbformat: 4,
          nbformat_minor: 5,
        }),
      } as Blob;
    }
    return new Blob(["verified figure"], { type: "image/png" });
  });
});

describe("dataset workflow UI", () => {
  it("uses the unified composer for type selection, ready dataset binding, and CSV import", async () => {
    const { container } = render(
      <WorkflowWorkspace
        {...handlers}
        snapshot={null}
        sources={[READY_PDF, READY_DATASET]}
        loading={false}
        mutating={false}
        connection="idle"
        error={null}
        canStart
        importingDataset={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Choose workflow/i }));
    fireEvent.click(
      screen.getByRole("button", { name: /^Dataset Analysis\b/i }),
    );
    expect(screen.getByLabelText("Dataset")).toHaveValue("dataset-1");
    expect(screen.getByText(DATASET_HASH)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Research question"), {
      target: { value: "Summarize outcomes by group" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create plan" }));

    await waitFor(() =>
      expect(handlers.onCreate).toHaveBeenCalledWith(
        "Summarize outcomes by group",
        {
          mode: "advanced",
          workflowType: "dataset-analysis",
          datasetSourceId: "dataset-1",
          generationMode: "local-deterministic",
          remoteDataApproved: false,
        },
      ),
    );

    const input = container.querySelector<HTMLInputElement>(
      'input[accept=".csv,text/csv"]',
    );
    expect(input).not.toBeNull();
    const file = new File(["group,value\na,1\n"], "new.csv", {
      type: "text/csv",
    });
    fireEvent.change(input!, { target: { files: [file] } });
    expect(handlers.onImportDataset).toHaveBeenCalledTimes(1);
  });

  it("renders the typed four-step dataset plan and approves only the plan", () => {
    render(
      <WorkflowWorkspace
        {...handlers}
        snapshot={planApprovalSnapshot()}
        sources={[READY_DATASET]}
        loading={false}
        mutating={false}
        connection="live"
        error={null}
        canStart
        importingDataset={false}
      />,
    );

    expect(screen.getByText("Review the dataset analysis plan")).toBeInTheDocument();
    expect(screen.getByText("Profile the immutable dataset")).toBeInTheDocument();
    expect(screen.getByText("Prepare policy-compliant Python")).toBeInTheDocument();
    expect(screen.getAllByText("Execute approved Python").length).toBeGreaterThan(0);
    expect(screen.getByText("Verify required artifacts")).toBeInTheDocument();
    expect(screen.getAllByText(DATASET_HASH).length).toBeGreaterThan(0);
    expect(screen.getByText("600 seconds")).toBeInTheDocument();
    expect(screen.getByText("Separate approval required")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Plan approval may prepare code, but the exact payload remains blocked until a separate runtime approval.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Approving this plan does not approve or execute Python."),
    ).toBeInTheDocument();
    expect(screen.getByText("Audit details")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Approve plan" }));
    expect(handlers.onApprovePlan).toHaveBeenCalledTimes(1);
  });

  it("shows profile and exact read-only repair approval with approve, reject, and cancel only", () => {
    const snapshot = executionApprovalSnapshot();
    render(
      <DatasetWorkflowDetails
        snapshot={snapshot}
        mutating={false}
        onDecision={handlers.onDecideAnalysis}
        onCancel={handlers.onCancel}
        onAcceptReviewWarnings={handlers.onAcceptReviewWarnings}
      />,
    );

    expect(screen.getByText("Dataset profile")).toBeInTheDocument();
    expect(screen.getByText("outcome")).toBeInTheDocument();
    expect(screen.getByText("mixed type")).toBeInTheDocument();
    expect(screen.getByLabelText("Analysis code")).toHaveTextContent(
      "summary.to_csv",
    );
    expect(screen.getAllByText(PAYLOAD_HASH).length).toBeGreaterThan(0);
    expect(
      screen.getByText("Repair diff — requires this new approval"),
    ).toBeInTheDocument();
    expect(screen.getByText("Payload changes")).toBeInTheDocument();
    expect(screen.getByText("Isolated Python runtime")).toBeInTheDocument();
    expect(screen.getByLabelText("Previous attempt error output")).toHaveTextContent("ValueError");
    expect(screen.getByLabelText("Recorded repair diff")).toHaveTextContent("summary.to_csv");
    expect(
      screen.getByText("The runtime/container applies the recorded runtime policy and affected resource scope; recorded artifacts are hash-checked after execution."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Previous attempt failed safely/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Approval queues only this immutable payload. Any repair requires a new approval.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /execute/i }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Approve exact payload" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Reject payload" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel task" }));
    expect(handlers.onDecideAnalysis).toHaveBeenNthCalledWith(1, "approved");
    expect(handlers.onDecideAnalysis).toHaveBeenNthCalledWith(2, "rejected");
    expect(handlers.onCancel).toHaveBeenCalledTimes(1);
  });

  it("keeps a non-pending analysis intent as an immutable execution record", () => {
    const pending = executionApprovalSnapshot();
    const snapshot = {
      ...pending,
      pendingApprovals: [],
      analysisIntent: {
        ...pending.analysisIntent!,
        status: "completed" as const,
        decision: "approved" as const,
      },
      allowedActions: ["cancel" as const],
    } as DatasetAnalysisWorkflowSnapshot;

    render(
      <DatasetWorkflowDetails
        snapshot={snapshot}
        mutating={false}
        onDecision={handlers.onDecideAnalysis}
        onCancel={handlers.onCancel}
        onAcceptReviewWarnings={handlers.onAcceptReviewWarnings}
      />,
    );

    expect(screen.getByText("Immutable execution record — Completed")).toBeInTheDocument();
    expect(screen.getByText("This immutable record preserves the recorded payload and provenance. It is not an approval request.")).toBeInTheDocument();
    expect(screen.getByText("Recorded Python payload")).toBeInTheDocument();
    expect(screen.queryByText("Approval queues only this immutable payload. Any repair requires a new approval.")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve exact payload|reject payload|cancel task/i })).not.toBeInTheDocument();
  });

  it.each([
    ["approved", "approved", "Approved"],
    ["executing", "approved", "Executing"],
    ["completed", "approved", "Completed"],
    ["failed", "approved", "Failed"],
    ["rejected", "rejected", "Rejected"],
    ["waiting-approval", null, "Execution envelope unavailable"],
  ] as const)("renders %s historical intent as a non-approval record", (status, decision, label) => {
    const pending = executionApprovalSnapshot();
    const snapshot = {
      ...pending,
      pendingApprovals: [],
      analysisIntent: { ...pending.analysisIntent!, status, decision },
      allowedActions: [] as const,
    } as DatasetAnalysisWorkflowSnapshot;
    render(
      <DatasetWorkflowDetails
        snapshot={snapshot}
        mutating={false}
        onDecision={handlers.onDecideAnalysis}
        onCancel={handlers.onCancel}
        onAcceptReviewWarnings={handlers.onAcceptReviewWarnings}
      />,
    );
    expect(screen.getByText(`Immutable execution record — ${label}`)).toBeInTheDocument();
    expect(screen.queryByText("Exact payload approval required")).not.toBeInTheDocument();
    if (decision !== "approved") {
      expect(screen.getByText("Expected outputs")).toBeInTheDocument();
      expect(screen.getAllByText("This record does not authorize or prove execution.").length).toBeGreaterThan(0);
      expect(screen.queryByText(/approval|approved|queues|new immutable approval/i)).not.toBeInTheDocument();
    }
  });

  it("localizes visible dataset approval and execution-record statuses in Simplified Chinese", async () => {
    await act(async () => { await i18n.changeLanguage("zh-Hans"); });
    const pending = executionApprovalSnapshot();
    pending.analysisSpec = {
      ...analysisSpecSnapshot(),
      status: "pending-approval",
    };
    const view = render(
      <DatasetWorkflowDetails
        snapshot={pending}
        mutating={false}
        onDecision={handlers.onDecideAnalysis}
        onCancel={handlers.onCancel}
        onAcceptReviewWarnings={handlers.onAcceptReviewWarnings}
      />,
    );
    expect(screen.getByText("等待审批")).toBeInTheDocument();

    const completed = {
      ...pending,
      pendingApprovals: [],
      analysisSpec: { ...analysisSpecSnapshot(), status: "approved" as const },
      analysisIntent: {
        ...pending.analysisIntent!,
        status: "completed" as const,
        decision: "approved" as const,
      },
      allowedActions: [] as const,
    } as DatasetAnalysisWorkflowSnapshot;
    view.rerender(
      <DatasetWorkflowDetails
        snapshot={completed}
        mutating={false}
        onDecision={handlers.onDecideAnalysis}
        onCancel={handlers.onCancel}
        onAcceptReviewWarnings={handlers.onAcceptReviewWarnings}
      />,
    );
    expect(screen.getByText("不可变执行记录 — 已完成")).toBeInTheDocument();
    expect(screen.getAllByText("已批准").length).toBeGreaterThan(0);

    await act(async () => { await i18n.changeLanguage("en"); });
  });

  it("shows completed count and the exact step waiting for approval", () => {
    render(<WorkflowProgress snapshot={executionApprovalSnapshot()} />);

    expect(screen.getByText("2 of 4 completed")).toBeInTheDocument();
    expect(screen.getByText(/Waiting for approval/)).toBeInTheDocument();
    expect(screen.getAllByText("execute analysis").length).toBeGreaterThan(0);
  });

  it("dispatches blocked resume and failed retry for dataset workflows", () => {
    const { rerender } = render(
      <WorkflowWorkspace
        {...handlers}
        snapshot={recoverySnapshot("blocked", "resume", 11)}
        sources={[READY_DATASET]}
        loading={false}
        mutating={false}
        connection="live"
        error={null}
        canStart
        importingDataset={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(handlers.onResume).toHaveBeenCalledTimes(1);
    expect(screen.getByText("blocked-for-test")).toBeInTheDocument();
    expect(screen.getByText("Retryable from saved state")).toBeInTheDocument();
    expect(screen.getByText("This task needs attention")).toHaveClass("text-error");

    rerender(
      <WorkflowWorkspace
        {...handlers}
        snapshot={recoverySnapshot("failed", "retry", 12)}
        sources={[READY_DATASET]}
        loading={false}
        mutating={false}
        connection="live"
        error={null}
        canStart
        importingDataset={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(handlers.onRetry).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Execution failed safely")).toBeInTheDocument();
  });

  it("uses Resume for the canonical no-ready-pdf dataset blocker", () => {
    render(
      <WorkflowWorkspace
        {...handlers}
        snapshot={recoverySnapshot("blocked", "resume", 13, "no-ready-pdf")}
        sources={[READY_DATASET]}
        loading={false}
        mutating={false}
        connection="live"
        error={null}
        canStart
        importingDataset={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(handlers.onResume).toHaveBeenCalledTimes(1);
    expect(handlers.onRetry).not.toHaveBeenCalled();
  });

  it.each([
    "analysis-execution-rejected",
    "analysis-repair-not-safe",
    "analysis-repair-limit-exceeded",
    "analysis-compiled-execution-failed",
    "analysis-review-required",
  ])("uses resume for blocked dataset repair blocker %s", (code) => {
    const snapshot = recoverySnapshot("blocked", "resume", 14, code);
    snapshot.allowedActions = ["resume", "cancel"];
    render(
      <WorkflowWorkspace
        {...handlers}
        snapshot={snapshot}
        sources={[READY_DATASET]}
        loading={false}
        mutating={false}
        connection="live"
        error={null}
        canStart
        importingDataset={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Create revised plan" }));
    expect(handlers.onResume).toHaveBeenCalledTimes(1);
    expect(handlers.onRetry).not.toHaveBeenCalled();
  });

  it("disables saved workflow selection while a workflow mutation is active", () => {
    const onSelectWorkflow = vi.fn();
    render(
      <ResearchLibrarySidebar
        health={null}
        booting={false}
        serviceReady
        projects={[]}
        projectId="project-1"
        workflows={[executionApprovalSnapshot()]}
        snapshot={executionApprovalSnapshot()}
        selectedWorkflowId="workflow-1"
        loadingWorkflows={false}
        workflowMutating
        sources={[READY_DATASET]}
        loadingSources={false}
        selection={null}
        importing={false}
        projectMutating={false}
        showArchivedProjects={false}
        onProjectChange={vi.fn()}
        onNewProject={vi.fn()}
        onSelectWorkflow={onSelectWorkflow}
        onOpenWorkflowReport={vi.fn()}
        onNewWorkflow={vi.fn()}
        onSelectSource={vi.fn()}
        onImportPdf={vi.fn()}
        onToggleArchivedProjects={vi.fn()}
        onRenameProject={vi.fn()}
        onArchiveProject={vi.fn()}
        onRestoreProject={vi.fn()}
      />,
    );

    const workflowButton = screen.getByRole("button", {
      name: /Summarize outcomes by group/i,
    });
    expect(workflowButton).toBeDisabled();
    expect(screen.getAllByText("experiment.csv").length).toBeGreaterThan(0);
    expect(screen.getByText("CSV dataset")).toBeInTheDocument();
    fireEvent.click(workflowButton);
    expect(onSelectWorkflow).not.toHaveBeenCalled();
  });

  it.each(["running", "completed", "failed"])(
    "keeps a CSV-only %s dataset workflow out of the literature fixture",
    (status) => {
      expect(
        shouldDefaultToDatasetWorkspace(
          [READY_DATASET],
          { workflowType: "dataset-analysis", status },
        ),
      ).toBe(true);
      expect(
        shouldDefaultToDatasetWorkspace(
          [READY_DATASET, READY_PDF],
          { workflowType: "dataset-analysis", status },
        ),
      ).toBe(true);
    },
  );

  it("keeps canonical dataset workflow states distinct", () => {
    expect(datasetWorkflowDisplayState("waiting-plan-approval", [])).toBe("Awaiting approval");
    expect(datasetWorkflowDisplayState("running", [])).toBe("Running");
    expect(datasetWorkflowDisplayState("reviewing", [])).toBe("Reviewing");
    expect(datasetWorkflowDisplayState("failed", [])).toBe("Failed");
    expect(datasetWorkflowDisplayState("completed", [])).toBe("Completed");
    expect(datasetWorkflowDisplayState("completed", [{ type: "job.retried" } as never])).toBe("Completed");
  });

  it("renders canonical run streams, environment, dataset-classified CSV table, figure, artifacts, and warning acceptance", async () => {
    const snapshot = reviewSnapshot();
    const { container } = render(
      <WorkflowWorkspace
        {...handlers}
        snapshot={snapshot}
        sources={[READY_DATASET]}
        loading={false}
        mutating={false}
        connection="live"
        error={null}
        canStart
        importingDataset={false}
      />,
    );

    expect(screen.getByText("analysis complete")).toBeInTheDocument();
    expect(screen.getByText("minor numerical warning")).toBeInTheDocument();
    expect(screen.getByText("Environment SHA-256")).toBeInTheDocument();
    expect(screen.getAllByText("summary.csv").length).toBeGreaterThan(0);
    expect(screen.getAllByText("figure.png").length).toBeGreaterThan(0);
    expect(screen.getByText("method-scope-limited")).toBeInTheDocument();
    expect(screen.getByText("Approved analysis method")).toBeInTheDocument();
    expect(screen.getByText("Structured statistical results")).toBeInTheDocument();
    expect(screen.getAllByText("welch-t-test").length).toBeGreaterThan(0);
    expect(screen.getByText("p-value")).toBeInTheDocument();
    expect(screen.getByText("hedges-g: 0.81")).toBeInTheDocument();
    expect(screen.getByText("Artifact browser (9)")).toBeInTheDocument();
    expect(screen.getByText("Reproducibility")).toBeInTheDocument();
    expect(screen.getByText("Results")).toBeInTheDocument();
    expect(screen.getAllByText("Artifact provenance")).toHaveLength(9);
    expect(screen.getAllByText("Preview").length).toBeGreaterThan(0);
    const previewLink = screen.getAllByRole("link", { name: "Preview" })[0];
    fireEvent.click(previewLink);
    const previewTarget = container.querySelector(
      previewLink.getAttribute("href") ?? "",
    );
    expect(previewTarget).toHaveFocus();
    expect(screen.getAllByText("analysis-spec.json").length).toBeGreaterThan(0);
    expect(screen.getAllByText("results.json").length).toBeGreaterThan(0);
    expect(screen.getByText("analysis-spec-compiler-v1")).toBeInTheDocument();
    expect(screen.getByText("dataset-analysis-spec-v1")).toBeInTheDocument();
    expect(
      screen.getByText("The observed group difference is statistically compatible with a non-zero effect."),
    ).toBeInTheDocument();
    expect(screen.getAllByText(RESULT_HASH).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/not, by itself, evidence of causation/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/workflow completed, but no research result/i),
    ).not.toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getAllByText("treated").length).toBeGreaterThan(0);
      expect(screen.getAllByText("2.5").length).toBeGreaterThan(0);
      expect(screen.getByText('{"python":"3.12"}')).toBeInTheDocument();
      expect(
        screen.getByAltText("Analysis figure figure.png"),
      ).toHaveAttribute("src", "blob:verified-figure");
    });

    fireEvent.click(
      screen.getByRole("button", { name: "Accept warnings and complete" }),
    );
    expect(handlers.onAcceptReviewWarnings).toHaveBeenCalledTimes(1);
  });

  it("turns a completed workflow into a result-first report with process records collapsed", async () => {
    const reviewing = reviewSnapshot();
    const snapshot = {
      ...reviewing,
      workflow: {
        ...reviewing.workflow,
        status: "completed" as const,
        currentStepId: null,
        completedAt: "2026-07-15T00:02:00Z",
      },
      reviewWarningAcceptance: {
        eventId: "event-warning-accepted",
        reviewId: "review-1",
        reviewInputSha256: REVIEW_HASH,
        expectedWorkflowRevision: 8,
        decision: "accepted" as const,
        acceptedAt: "2026-07-15T00:01:30Z",
      },
      allowedActions: [] as const,
    } as unknown as DatasetAnalysisWorkflowSnapshot;

    render(
      <WorkflowWorkspace
        {...handlers}
        snapshot={snapshot}
        sources={[READY_DATASET]}
        loading={false}
        mutating={false}
        connection="live"
        error={null}
        canStart
        importingDataset={false}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "Analysis results" }),
    ).toBeInTheDocument();
    const structuredResult = screen
      .getByText("Structured statistical results")
      .closest("section");
    const figuresAndTables = screen
      .getByText("Figures and tables")
      .closest("section");
    const review = screen
      .getByText("Deterministic analysis integrity review")
      .closest("section");
    expect(structuredResult).not.toBeNull();
    expect(figuresAndTables).not.toBeNull();
    expect(review).not.toBeNull();
    expect(
      structuredResult!.compareDocumentPosition(figuresAndTables!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      figuresAndTables!.compareDocumentPosition(review!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    const runDetails = screen
      .getByText("Run and reproducibility record")
      .closest("details");
    expect(runDetails).not.toHaveAttribute("open");
    expect(runDetails).toHaveTextContent("Environment SHA-256");

    const methodDetails = screen
      .getByText("Method and reproducibility")
      .closest("details");
    expect(methodDetails).not.toHaveAttribute("open");
    expect(methodDetails).toHaveTextContent("Dataset profile");
    expect(methodDetails).toHaveTextContent("Approved analysis method");
    await waitFor(() => {
      expect(screen.getAllByText("treated").length).toBeGreaterThan(0);
      expect(screen.getByAltText("Analysis figure figure.png")).toHaveAttribute(
        "src",
        "blob:verified-figure",
      );
      const chartDetails = screen
        .getByText("Explore this table as a chart")
        .closest("details");
      expect(chartDetails).not.toHaveAttribute("open");
    });
  });

  it("connects canonical dataset, analysis, results, notebook, and artifact surfaces", async () => {
    const snapshot = reviewSnapshot();
    render(
      <DatasetResearchWorkspace
        projectTitle="EEG analysis"
        sources={[READY_DATASET]}
        snapshot={snapshot}
        events={[]}
        mutating={false}
        importing={false}
        serviceReady
        literatureAvailable={false}
        onImportDatasetRequest={vi.fn()}
        onOpenWorkflow={vi.fn()}
        onOpenLiterature={vi.fn()}
        onRefresh={handlers.onRefresh}
        onDecision={handlers.onDecideAnalysis}
        onCancel={handlers.onCancel}
        onRetry={handlers.onRetry}
        onResume={handlers.onResume}
        onAcceptReviewWarnings={handlers.onAcceptReviewWarnings}
      />,
    );

    expect(screen.getByRole("heading", { name: "Dataset summary" })).toBeInTheDocument();
    expect(screen.getAllByText("experiment.csv").length).toBeGreaterThan(0);
    expect(screen.getByText("Dataset profile")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Analysis" }));
    expect(screen.getByText("Reviewing the run and verified artifacts")).toBeInTheDocument();
    expect(screen.getByText("Approved analysis method")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Results" }));
    expect(screen.getByText("Structured statistical results")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByText("treated").length).toBeGreaterThan(0));
    expect(screen.getByRole("button", { name: "line" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Notebook" }));
    await waitFor(() => expect(screen.getByText(/summary = data\.groupby/)).toBeInTheDocument());
    expect(screen.getByText("Executed · read-only")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Artifacts" }));
    expect(screen.getByText("Artifact browser (9)")).toBeInTheDocument();
    expect(screen.getAllByText("Artifact provenance")).toHaveLength(9);
    await waitFor(() => expect(screen.getByText('{"python":"3.12"}')).toBeInTheDocument());
  });

  it("links a completed dataset workflow to its project-bound notebook and artifacts", async () => {
    const reviewing = reviewSnapshot();
    const completed = {
      ...reviewing,
      workflow: {
        ...reviewing.workflow,
        status: "completed" as const,
        currentStepId: null,
        completedAt: "2026-07-15T00:02:00Z",
      },
      allowedActions: [] as const,
    } as unknown as DatasetAnalysisWorkflowSnapshot;

    render(
      <DatasetResearchWorkspace
        projectTitle="EEG analysis"
        sources={[READY_DATASET]}
        snapshot={completed}
        events={[]}
        mutating={false}
        importing={false}
        serviceReady
        literatureAvailable={false}
        onImportDatasetRequest={vi.fn()}
        onOpenWorkflow={vi.fn()}
        onOpenLiterature={vi.fn()}
        onRefresh={handlers.onRefresh}
        onDecision={handlers.onDecideAnalysis}
        onCancel={handlers.onCancel}
        onRetry={handlers.onRetry}
        onResume={handlers.onResume}
        onAcceptReviewWarnings={handlers.onAcceptReviewWarnings}
      />,
    );

    expect(screen.getByRole("link", { name: "Open notebook" })).toHaveAttribute(
      "href",
      "/notebooks?projectId=project-1&workflowId=workflow-1",
    );
    expect(screen.getByRole("link", { name: "View artifacts" })).toHaveAttribute(
      "href",
      "/files?projectId=project-1&workflowId=workflow-1",
    );
    expect(screen.getByRole("button", { name: "Results" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(
      screen.getByRole("heading", { name: "Analysis results" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Structured statistical results")).toBeInTheDocument();
    const figuresAndTables = screen
      .getByText("Figures and tables")
      .closest("section");
    const review = screen
      .getByText("Deterministic analysis integrity review")
      .closest("section");
    expect(figuresAndTables).not.toBeNull();
    expect(review).not.toBeNull();
    expect(
      figuresAndTables!.compareDocumentPosition(review!) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    await waitFor(() => {
      expect(screen.getAllByText("treated").length).toBeGreaterThan(0);
      expect(screen.getByAltText("Analysis figure figure.png")).toHaveAttribute(
        "src",
        "blob:verified-figure",
      );
      expect(
        screen
          .getByText("Explore this table as a chart")
          .closest("details"),
      ).not.toHaveAttribute("open");
    });
  });

  it("labels an unapproved AnalysisSpec as proposed", () => {
    const snapshot = planApprovalSnapshot();
    snapshot.analysisSpec = {
      ...analysisSpecSnapshot(),
      status: "pending-approval",
    };
    render(
      <WorkflowWorkspace
        {...handlers}
        snapshot={snapshot}
        sources={[READY_DATASET]}
        loading={false}
        mutating={false}
        connection="live"
        error={null}
        canStart
        importingDataset={false}
      />,
    );

    expect(screen.getByText("Proposed analysis method")).toBeInTheDocument();
    expect(screen.queryByText("Approved analysis method")).not.toBeInTheDocument();
  });

  it("renders spreadsheet-formula CSV cells as inert preview text", async () => {
    const formula = '=HYPERLINK("https://evil.example","click")';
    core.fetchArtifactBlob.mockImplementation(async (artifactId: string) => {
      if (artifactId === "artifact-table") {
        return {
          text: async () =>
            'kind,value\nequals,"=HYPERLINK(""https://evil.example"",""click"")"\nplus,+SUM(1)\nminus,-SUM(1)\nat,"@SUM(1,2)"\n',
        } as Blob;
      }
      if (artifactId === "artifact-environment") {
        return { text: async () => '{"python":"3.12"}' } as Blob;
      }
      return new Blob(["verified figure"], { type: "image/png" });
    });

    const { container } = render(
      <WorkflowWorkspace
        {...handlers}
        snapshot={reviewSnapshot()}
        sources={[READY_DATASET]}
        loading={false}
        mutating={false}
        connection="live"
        error={null}
        canStart
        importingDataset={false}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText(formula)).toBeInTheDocument();
      expect(screen.getByText("+SUM(1)")).toBeInTheDocument();
      expect(screen.getByText("-SUM(1)")).toBeInTheDocument();
      expect(screen.getByText("@SUM(1,2)")).toBeInTheDocument();
    });
    expect(screen.queryByRole("link", { name: "click" })).not.toBeInTheDocument();
    expect(container.querySelector('a[href*="evil.example"]')).toBeNull();
  });
});

function planApprovalSnapshot(): DatasetAnalysisWorkflowSnapshot {
  return {
    ...datasetSnapshotBase(),
    workflow: {
      ...datasetSnapshotBase().workflow,
      status: "waiting-plan-approval",
      revision: 2,
      currentStepId: null,
    },
    plan: {
      ...datasetPlan(),
      status: "pending-approval",
      approvedAt: null,
      steps: datasetPlan().steps.map((step) => ({
        ...step,
        status: "pending",
      })) as unknown as ReturnType<typeof datasetPlan>["steps"],
    },
    pendingApprovals: [
      {
        id: "approval-plan",
        workflowId: "workflow-1",
        planId: "plan-1",
        taskId: null,
        kind: "plan",
        status: "waiting",
        subjectType: "plan",
        subjectId: "plan-1",
        action: "approve-plan",
        payloadSha256: "e".repeat(64),
        riskLevel: "medium",
        reason: "Approve the immutable dataset plan.",
        affectedResources: ["dataset:dataset-1"],
        createdAt: "2026-07-15T00:00:01Z",
        decidedAt: null,
        approvalSchemaVersion: "workflow-plan-approval-v3",
        workflowType: "dataset-analysis",
        planVersion: 1,
        planSha256: "e".repeat(64),
        expectedWorkflowRevision: 2,
        datasetSourceId: "dataset-1",
        datasetContentHash: DATASET_HASH,
      },
    ],
    datasetProfile: null,
    analysisIntent: null,
    analysisRun: null,
    latestReview: null,
    allowedActions: ["approve-plan", "cancel"],
  } as unknown as DatasetAnalysisWorkflowSnapshot;
}

function executionApprovalSnapshot(): DatasetAnalysisWorkflowSnapshot {
  const base = datasetSnapshotBase();
  return {
    ...base,
    workflow: {
      ...base.workflow,
      status: "running",
      revision: 5,
      currentStepId: "task-execute",
    },
    plan: {
      ...datasetPlan(),
      steps: datasetPlan().steps.map((step, index) => ({
        ...step,
        status: index < 2 ? "completed" : index === 2 ? "waiting-approval" : "pending",
      })) as unknown as ReturnType<typeof datasetPlan>["steps"],
    },
    pendingApprovals: [
      {
        id: "approval-execute",
        workflowId: "workflow-1",
        planId: "plan-1",
        taskId: "task-execute",
        kind: "analysis-execution",
        status: "waiting",
        subjectType: "analysis-intent",
        subjectId: "intent-repair",
        action: "execute-python-data-analysis",
        payloadSha256: PAYLOAD_HASH,
        riskLevel: "high",
        reason: "Execute only the exact displayed payload.",
        affectedResources: ["dataset:dataset-1"],
        createdAt: "2026-07-15T00:00:02Z",
        decidedAt: null,
        approvalSchemaVersion: "analysis-intent-v3",
        expectedWorkflowRevision: 5,
        analysisIntentId: "intent-repair",
        planStepId: "execute-analysis",
        datasetSourceId: "dataset-1",
        datasetContentHash: DATASET_HASH,
        expectedOutputs: executionOutputs(),
        timeoutSeconds: 600,
        code: "summary.to_csv('summary.csv', index=False)",
        codeDiff: "+ summary.to_csv('summary.csv', index=False)",
      },
    ],
    datasetProfile: datasetProfile(),
    analysisIntent: {
      id: "intent-repair",
      taskId: "task-execute",
      projectId: "project-1",
      datasetSourceId: "dataset-1",
      datasetContentHash: DATASET_HASH,
      objective: "Summarize outcomes by group.",
      code: "summary.to_csv('summary.csv', index=False)",
      payloadSha256: PAYLOAD_HASH,
      riskLevel: "high",
      affectedResources: ["dataset:dataset-1"],
      status: "waiting-approval",
      decision: null,
      workflowId: "workflow-1",
      planStepId: "execute-analysis",
      previousIntentId: "intent-initial",
      expectedOutputs: executionOutputs(),
      timeoutSeconds: 600,
      repairAttempt: 1,
      errorSummary: {
        schemaVersion: "1",
        category: "runtime",
        code: "analysis-runtime-failed",
        userMessage: "The initial analysis failed safely.",
        stderrExcerpt: "ValueError",
        retryable: true,
      },
      codeDiff: "+ summary.to_csv('summary.csv', index=False)",
      createdAt: "2026-07-15T00:00:02Z",
      updatedAt: "2026-07-15T00:00:02Z",
    },
    analysisRun: null,
    latestReview: null,
    reviewWarningAcceptance: null,
    allowedActions: ["approve-analysis", "reject-analysis", "cancel"],
  } as unknown as DatasetAnalysisWorkflowSnapshot;
}

function reviewSnapshot(): DatasetAnalysisWorkflowSnapshot {
  const approval = executionApprovalSnapshot();
  return {
    ...approval,
    workflow: {
      ...approval.workflow,
      status: "reviewing",
      revision: 8,
      currentStepId: null,
    },
    plan: {
      ...datasetPlan(),
      steps: datasetPlan().steps.map((step) => ({
        ...step,
        status: "completed",
      })) as unknown as ReturnType<typeof datasetPlan>["steps"],
    },
    pendingApprovals: [],
    analysisIntent: {
      id: "intent-completed",
      taskId: "task-execute",
      projectId: "project-1",
      datasetSourceId: "dataset-1",
      datasetContentHash: DATASET_HASH,
      objective: "Summarize outcomes by group.",
      code: "summary.to_csv('summary.csv', index=False)",
      payloadSha256: PAYLOAD_HASH,
      riskLevel: "high",
      affectedResources: ["dataset:dataset-1"],
      status: "completed",
      decision: "approved",
      workflowId: "workflow-1",
      planStepId: "execute-analysis",
      previousIntentId: null,
      expectedOutputs: executionOutputs(),
      timeoutSeconds: 600,
      repairAttempt: 0,
      analysisSpecId: "spec-1",
      specSha256: SPEC_HASH,
      datasetProfileSha256: PROFILE_HASH,
      compilerVersion: "analysis-spec-compiler-v1",
      codeSha256: CODE_HASH,
      runtimePolicyId: "dataset-analysis-spec-v1",
      errorSummary: null,
      codeDiff: null,
      createdAt: "2026-07-15T00:00:02Z",
      updatedAt: "2026-07-15T00:01:02Z",
    },
    analysisSpec: analysisSpecSnapshot(),
    analysisRun: analysisRun(),
    structuredResult: structuredResultSnapshot(),
    latestReview: {
      id: "review-1",
      reviewType: "deterministic-analysis-v1",
      verdict: "passed-with-warnings",
      inputSha256: REVIEW_HASH,
      createdAt: "2026-07-15T00:01:03Z",
      result: {
        schemaVersion: "1",
        verdict: "passed-with-warnings",
        checks: [
          {
            code: "artifact-integrity",
            status: "passed",
            message: "All required artifact hashes match.",
            artifactId: null,
          },
          {
            code: "method-scope-limited",
            status: "warning",
            message: "The deterministic baseline is descriptive only.",
            artifactId: null,
          },
        ],
        artifactIssues: [],
        numericIssues: [],
        methodWarnings: [
          {
            code: "method-scope-limited",
            message: "Confirm the descriptive method is acceptable.",
            artifactId: null,
          },
        ],
        requiredRevisions: [],
        runId: "run-1",
        analysisIntentId: "intent-completed",
        inputDatasetContentHash: DATASET_HASH,
        conclusion:
          "The observed group difference is statistically compatible with a non-zero effect.",
        analysisSpecId: "spec-1",
        structuredResultSha256: RESULT_HASH,
      },
    },
    reviewWarningAcceptance: null,
    allowedActions: ["accept-review-warnings", "cancel"],
  } as unknown as DatasetAnalysisWorkflowSnapshot;
}

function recoverySnapshot(
  status: "blocked" | "failed",
  action: "resume" | "retry",
  revision: number,
  code = `${status}-for-test`,
): DatasetAnalysisWorkflowSnapshot {
  const base = datasetSnapshotBase();
  return {
    ...base,
    workflow: {
      ...base.workflow,
      status,
      revision,
      currentStepId: null,
      blockingReason: {
        code,
        userMessage: `Dataset workflow ${status}.`,
        retryable: true,
      },
    },
    allowedActions: [action],
    eventCursor: revision,
  } as unknown as DatasetAnalysisWorkflowSnapshot;
}

function datasetSnapshotBase() {
  return {
    workflow: {
      id: "workflow-1",
      projectId: "project-1",
      goal: "Summarize outcomes by group.",
      workflowType: "dataset-analysis" as const,
      datasetSourceId: "dataset-1",
      datasetContentHash: DATASET_HASH,
      generationMode: "local-deterministic" as const,
      status: "running" as const,
      revision: 3,
      currentStepId: "task-inspect",
      planVersion: 1,
      retryCount: 0,
      blockingReason: null,
      cancelRequestedAt: null,
      createdAt: "2026-07-15T00:00:00Z",
      updatedAt: "2026-07-15T00:00:01Z",
      completedAt: null,
    },
    plan: datasetPlan(),
    pendingApprovals: [],
    result: null,
    latestReview: null,
    datasetProfile: null,
    analysisSpec: null,
    analysisIntent: null,
    analysisRun: null,
    structuredResult: null,
    reviewWarningAcceptance: null,
    allowedActions: ["cancel" as const],
    eventCursor: 3,
  };
}

function datasetPlan() {
  return {
    id: "plan-1",
    workflowId: "workflow-1",
    version: 1,
    status: "approved" as const,
    planSha256: "e".repeat(64),
    generator: "dataset-analysis-template-v1",
    promptVersion: null,
    model: null,
    spec: {
      schemaVersion: "1" as const,
      workflowType: "dataset-analysis" as const,
      goal: "Summarize outcomes by group.",
      datasetSourceId: "dataset-1",
      datasetContentHash: DATASET_HASH,
      assumptions: ["The CSV header is authoritative."],
      questionsForUser: [],
      steps: [
        {
          key: "inspect-dataset" as const,
          type: "dataset-inspection" as const,
          objective: "Profile the immutable dataset",
          dependencies: [] as [],
          inputs: {
            datasetSourceId: "dataset-1",
            datasetContentHash: DATASET_HASH,
            samplingMethod: "head-and-reservoir-v1" as const,
            maxSampleRows: 1_000,
          },
          expectedArtifacts: ["dataset-profile" as const],
          acceptanceCriteria: ["Record the exact dataset hash"],
          riskLevel: "low" as const,
        },
        {
          key: "prepare-analysis" as const,
          type: "prepare-analysis" as const,
          objective: "Prepare policy-compliant Python",
          dependencies: ["inspect-dataset" as const],
          inputs: {
            datasetSourceId: "dataset-1",
            datasetContentHash: DATASET_HASH,
            profileStepKey: "inspect-dataset" as const,
          },
          expectedArtifacts: ["analysis-intent" as const],
          acceptanceCriteria: ["Bind immutable code and dataset identity"],
          riskLevel: "medium" as const,
        },
        {
          key: "execute-analysis" as const,
          type: "python-data-analysis" as const,
          objective: "Execute approved Python",
          dependencies: ["prepare-analysis" as const],
          inputs: {
            datasetSourceId: "dataset-1",
            datasetContentHash: DATASET_HASH,
            preparationStepKey: "prepare-analysis" as const,
            expectedOutputs: executionOutputs(),
            timeoutSeconds: 600,
          },
          expectedArtifacts: [
            "executed-notebook" as const,
            "summary-table" as const,
            "figure" as const,
            "analysis-log" as const,
            "environment-manifest" as const,
          ],
          acceptanceCriteria: ["Execute only the exact approved payload"],
          riskLevel: "high" as const,
        },
        {
          key: "collect-artifacts" as const,
          type: "collect-artifacts" as const,
          objective: "Verify required artifacts",
          dependencies: ["execute-analysis" as const],
          inputs: {
            executionStepKey: "execute-analysis" as const,
            expectedOutputs: executionOutputs(),
          },
          expectedArtifacts: [
            "executed-notebook" as const,
            "summary-table" as const,
            "figure" as const,
            "analysis-log" as const,
            "environment-manifest" as const,
          ],
          acceptanceCriteria: ["Verify each artifact hash"],
          riskLevel: "low" as const,
        },
      ],
    },
    steps: [
      materializedStep("task-inspect", "inspect-dataset", "dataset-inspection", 0),
      materializedStep("task-prepare", "prepare-analysis", "prepare-analysis", 1),
      materializedStep("task-execute", "execute-analysis", "python-data-analysis", 2),
      materializedStep("task-collect", "collect-artifacts", "collect-artifacts", 3),
    ],
    createdAt: "2026-07-15T00:00:01Z",
    approvedAt: "2026-07-15T00:00:02Z",
  };
}

function materializedStep(
  id: string,
  key: "inspect-dataset" | "prepare-analysis" | "execute-analysis" | "collect-artifacts",
  type: "dataset-inspection" | "prepare-analysis" | "python-data-analysis" | "collect-artifacts",
  orderIndex: number,
) {
  return {
    id,
    key,
    orderIndex,
    type,
    objective: key.split("-").join(" "),
    status: "queued" as const,
    retryCount: 0,
    startedAt: null,
    completedAt: null,
    outputSummary: null,
  };
}

function datasetProfile() {
  return {
    schemaVersion: "1" as const,
    datasetSourceId: "dataset-1",
    filename: "experiment.csv",
    contentHash: DATASET_HASH,
    fileSizeBytes: 128,
    encoding: "utf-8",
    delimiter: ",",
    rowCount: 3,
    columnCount: 2,
    columns: [
      {
        index: 0,
        name: "group",
        inferredType: "categorical" as const,
        missingCount: 0,
        uniqueCount: 2,
        numericRange: null,
        lowCardinality: { values: ["control", "treated"], truncated: false },
        potentialDate: false,
        potentialId: false,
        mixedType: false,
      },
      {
        index: 1,
        name: "outcome",
        inferredType: "mixed" as const,
        missingCount: 1,
        uniqueCount: 2,
        numericRange: { minimum: 1, maximum: 3 },
        lowCardinality: null,
        potentialDate: false,
        potentialId: false,
        mixedType: true,
      },
    ],
    sampling: {
      method: "head-and-reservoir-v1" as const,
      rowsRead: 3,
      rowsProfiled: 3,
      maxSampleRows: 1_000,
      seed: 7,
    },
    warnings: [
      {
        code: "mixed-column-type" as const,
        message: "Column contains mixed values.",
        columnName: "outcome",
      },
    ],
  };
}

function executionOutputs() {
  return [
    "executed-notebook",
    "summary-table",
    "figures",
    "analysis-log",
    "environment-manifest",
  ] as const;
}

function analysisSpecSnapshot() {
  return {
    id: "spec-1",
    revision: 1,
    status: "approved" as const,
    selectorKind: "local-deterministic" as const,
    selectorReason: "Two independent groups and a numeric outcome were confirmed.",
    promptVersion: null,
    specSha256: SPEC_HASH,
    datasetProfileSha256: PROFILE_HASH,
    spec: {
      schemaVersion: "1" as const,
      objective: "Compare outcomes between treated and control groups.",
      datasetSourceId: "dataset-1",
      datasetContentHash: DATASET_HASH,
      datasetProfileHash: PROFILE_HASH,
      missingValuePolicy: "drop-per-operation" as const,
      confidenceLevel: 0.95,
      randomSeed: 42,
      assumptions: ["Rows represent independent observations."],
      limitations: ["This observational comparison does not establish causation."],
      operation: {
        type: "two-group-comparison" as const,
        outcomeColumn: "outcome",
        groupColumn: "group",
        groups: ["control", "treated"] as [string, string],
        method: "welch-t-test" as const,
        effectSize: "hedges-g" as const,
        checkAssumptions: true,
        plot: "boxplot" as const,
      },
    },
    createdAt: "2026-07-15T00:00:01Z",
  };
}

function structuredResultSnapshot() {
  return {
    id: "structured-result-1",
    analysisSpecId: "spec-1",
    analysisIntentId: "intent-completed",
    runId: "run-1",
    resultSha256: RESULT_HASH,
    createdAt: "2026-07-15T00:01:02Z",
    result: {
      schemaVersion: "1" as const,
      objective: "Compare outcomes between treated and control groups.",
      datasetSourceId: "dataset-1",
      datasetContentHash: DATASET_HASH,
      datasetProfileHash: PROFILE_HASH,
      operationType: "two-group-comparison" as const,
      requestedMethod: "welch-t-test" as const,
      resolvedMethod: "welch-t-test" as const,
      methodSelectionReason: "Welch's test does not assume equal variances.",
      sampleSummary: { totalRows: 8, analyzedRows: 6, missingRows: 2 },
      warnings: ["Small samples reduce precision."],
      limitations: ["Observed differences are not causal estimates."],
      result: {
        type: "two-group-comparison" as const,
        groupColumn: "group",
        outcomeColumn: "outcome",
        groups: ["control", "treated"] as [string, string],
        sampleSizes: { control: 3, treated: 3 },
        missingCounts: { control: 1, treated: 1 },
        descriptiveStatistics: {
          control: { mean: 1.5, standardDeviation: 0.5 },
          treated: { mean: 2.5, standardDeviation: 0.7 },
        },
        testStatistic: 2.13,
        pValue: 0.041,
        effectSizeName: "hedges-g" as const,
        effectSize: 0.81,
        confidenceInterval: [0.02, 1.6] as [number, number],
      },
    },
  };
}

function analysisRun() {
  const artifacts = [
    artifact("artifact-notebook", "notebook-executed", "executed.ipynb", "application/x-ipynb+json"),
    artifact("artifact-environment", "environment", "environment.json", "application/json"),
    artifact("artifact-stdout", "stdout", "stdout.txt", "text/plain"),
    artifact("artifact-stderr", "stderr", "stderr.txt", "text/plain"),
    artifact("artifact-log", "log", "execution.log", "text/plain"),
    artifact("artifact-spec", "structured-data", "analysis-spec.json", "application/json"),
    artifact("artifact-results", "structured-data", "results.json", "application/json"),
    artifact("artifact-table", "dataset", "summary.csv", "text/csv"),
    artifact("artifact-figure", "figure", "figure.png", "image/png"),
  ];
  return {
    id: "run-1",
    intentId: "intent-completed",
    taskId: "task-execute",
    projectId: "project-1",
    datasetSourceId: "dataset-1",
    objective: "Summarize outcomes by group.",
    code: "summary.to_csv('summary.csv', index=False)",
    payloadSha256: PAYLOAD_HASH,
    status: "completed" as const,
    environmentHash: "f".repeat(64),
    inputArtifacts: ["dataset-1"] as [string],
    outputArtifacts: artifacts.map((item) => item.path) as [
      string,
      string,
      string,
      string,
      string,
      ...string[],
    ],
    stdout: "analysis complete",
    stderr: "minor numerical warning",
    log: "completed safely",
    logs: "completed safely",
    error: null,
    artifacts: artifacts as [
      (typeof artifacts)[number],
      (typeof artifacts)[number],
      (typeof artifacts)[number],
      (typeof artifacts)[number],
      (typeof artifacts)[number],
      ...(typeof artifacts)[number][],
    ],
    createdAt: "2026-07-15T00:01:00Z",
    finishedAt: "2026-07-15T00:01:01Z",
  };
}

function artifact(
  id: string,
  artifactType:
    | "notebook-executed"
    | "environment"
    | "stdout"
    | "stderr"
    | "log"
    | "dataset"
    | "structured-data"
    | "figure",
  path: string,
  mimeType: string,
) {
  return {
    id,
    artifactType,
    path: `runs/run-1/${path}`,
    mimeType,
    contentHash: id.slice(-1).repeat(64),
    sizeBytes: 64,
    createdAt: "2026-07-15T00:01:01Z",
  };
}
