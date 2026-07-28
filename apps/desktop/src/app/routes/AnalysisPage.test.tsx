import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  AnalysisRun,
  DatasetAnalysisWorkflowSnapshot,
  ResearchProject,
  ResearchSource,
  ScienceCoreHealth,
} from "@spark/research-domain";
import i18n from "@/i18n";
import { AnalysisPage } from "./AnalysisPage";

const core = vi.hoisted(() => ({
  health: vi.fn(),
  listProjects: vi.fn(),
  listSources: vi.fn(),
  listAnalysisRuns: vi.fn(),
  listWorkflows: vi.fn(),
  listAgentRuns: vi.fn(),
  createWorkflow: vi.fn(),
  createAgentRun: vi.fn(),
  getWorkflow: vi.fn(),
  getAgentRun: vi.fn(),
  approveWorkflowPlan: vi.fn(),
  decideWorkflowAnalysisIntent: vi.fn(),
  acceptWorkflowReviewWarnings: vi.fn(),
  fetchArtifactBlob: vi.fn(),
}));

vi.mock("@/lib/scienceCore", () => ({ scienceCore: core }));

const project = {
  id: "project-1",
  title: "Climate trends",
  projectPath: "/workspace/climate-trends",
} as ResearchProject;

const dataset = {
  id: "dataset-1",
  title: "annual-temperature.csv",
  sourceKind: "dataset",
  contentHash: "d".repeat(64),
} as ResearchSource;

const analysisRun = (overrides: Partial<AnalysisRun> = {}): AnalysisRun => ({
  id: "run-1234567890",
  intentId: "intent-1",
  taskId: "task-1",
  projectId: project.id,
  datasetSourceId: dataset.id,
  objective: "Describe the annual temperature trend",
  code: "print('analysis')",
  payloadSha256: "p".repeat(64),
  status: "completed",
  environmentHash: "e".repeat(64),
  inputArtifacts: [],
  outputArtifacts: ["summary.csv"],
  stdout: "",
  stderr: "",
  log: "analysis finished",
  logs: "analysis finished",
  artifacts: [
    {
      id: "artifact-1",
      artifactType: "table",
      path: "runs/run-1/summary.csv",
      mimeType: "text/csv",
      contentHash: "a".repeat(64),
      sizeBytes: 128,
      createdAt: "2026-07-25T12:00:00Z",
    },
  ],
  createdAt: "2026-07-25T12:00:00Z",
  finishedAt: "2026-07-25T12:01:00Z",
  error: null,
  ...overrides,
});

const planApprovalWorkflow = (): DatasetAnalysisWorkflowSnapshot =>
  ({
    workflow: {
      id: "workflow-1",
      projectId: project.id,
      goal: "Analyze the relationship between Year and temperature.",
      workflowType: "dataset-analysis",
      datasetSourceId: dataset.id,
      datasetContentHash: dataset.contentHash,
      generationMode: "local-deterministic",
      status: "waiting-plan-approval",
      revision: 2,
      currentStepId: null,
      planVersion: 1,
      retryCount: 0,
      blockingReason: null,
      cancelRequestedAt: null,
      createdAt: "2026-07-25T12:00:00Z",
      updatedAt: "2026-07-25T12:00:01Z",
      completedAt: null,
    },
    plan: {
      id: "plan-1",
      version: 1,
      planSha256: "e".repeat(64),
    },
    pendingApprovals: [
      {
        id: "approval-plan",
        kind: "plan",
      },
    ],
    result: null,
    latestReview: null,
    datasetProfile: null,
    analysisSpec: null,
    analysisIntent: null,
    analysisRun: null,
    structuredResult: null,
    reviewWarningAcceptance: null,
    allowedActions: ["approve-plan", "cancel"],
    eventCursor: 2,
  }) as unknown as DatasetAnalysisWorkflowSnapshot;

const renderPage = () =>
  render(
    <MemoryRouter>
      <AnalysisPage />
    </MemoryRouter>,
  );

describe("AnalysisPage result hierarchy", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("en");
    core.health.mockReset();
    core.listProjects.mockReset();
    core.listSources.mockReset();
    core.listAnalysisRuns.mockReset();
    core.listWorkflows.mockReset();
    core.listAgentRuns.mockReset();
    core.createWorkflow.mockReset();
    core.createAgentRun.mockReset();
    core.getWorkflow.mockReset();
    core.getAgentRun.mockReset();
    core.approveWorkflowPlan.mockReset();
    core.decideWorkflowAnalysisIntent.mockReset();
    core.acceptWorkflowReviewWarnings.mockReset();
    core.fetchArtifactBlob.mockReset();
    core.health.mockResolvedValue({ database: "ok", runtime: "ready" } as ScienceCoreHealth);
    core.listProjects.mockResolvedValue([project]);
    core.listSources.mockResolvedValue([dataset]);
    core.listWorkflows.mockResolvedValue([]);
    core.listAgentRuns.mockResolvedValue([]);
    core.fetchArtifactBlob.mockResolvedValue({
      text: vi.fn().mockResolvedValue("metric,value\nmean,0.82\nmedian,0.74"),
    });
  });

  it("shows the research result inline and keeps logs in the run inspector", async () => {
    core.listAnalysisRuns.mockResolvedValue([analysisRun()]);
    renderPage();

    expect(await screen.findByText("Describe the annual temperature trend")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /summary\.csv/ })).toBeVisible();
    expect(await screen.findByText("mean")).toBeVisible();
    expect(screen.getByText("0.82")).toBeVisible();
    expect(screen.queryByText("analysis finished")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("tab", { name: "Run" }));
    expect(screen.getByText("analysis finished")).toBeVisible();
  });

  it("explains an active run without presenting an empty log panel", async () => {
    core.listAnalysisRuns.mockResolvedValue([
      analysisRun({
        status: "running",
        finishedAt: null,
        logs: "",
        artifacts: [],
      }),
    ]);
    renderPage();

    expect(
      await screen.findByText("Spark is running the approved analysis. Results will appear here."),
    ).toBeVisible();
    expect(screen.getByText("Analysis in progress")).toBeVisible();
    expect(core.fetchArtifactBlob).not.toHaveBeenCalled();
  });

  it("keeps older runs folded and lets the user switch the result in place", async () => {
    const older = analysisRun({
      id: "run-older",
      objective: "Older comparison analysis",
      artifacts: [
        {
          id: "artifact-older",
          artifactType: "table",
          path: "runs/run-older/older-summary.csv",
          mimeType: "text/csv",
          contentHash: "b".repeat(64),
          sizeBytes: 96,
          createdAt: "2026-07-24T12:00:00Z",
        },
      ],
    });
    core.listAnalysisRuns.mockResolvedValue([analysisRun(), older]);
    renderPage();

    expect(await screen.findByRole("button", { name: /summary\.csv/ })).toBeVisible();
    expect(screen.queryByRole("button", { name: /older-summary\.csv/ })).not.toBeInTheDocument();

    await userEvent.click(screen.getByText("Previous runs (1)"));
    await userEvent.click(screen.getByRole("button", { name: /Older comparison analysis/ }));
    expect(screen.getByRole("button", { name: /older-summary\.csv/ })).toBeVisible();
    expect(core.fetchArtifactBlob).toHaveBeenLastCalledWith("artifact-older");
  });

  it("restores a local agent plan and allows its first approval before profiling", async () => {
    const workflow = planApprovalWorkflow();
    core.listAnalysisRuns.mockResolvedValue([]);
    core.listWorkflows.mockResolvedValue([workflow]);
    core.approveWorkflowPlan.mockResolvedValue(workflow);
    renderPage();

    expect(
      await screen.findByDisplayValue("Analyze the relationship between Year and temperature."),
    ).toBeDisabled();
    const approve = screen.getByRole("button", { name: "Approve analysis plan" });
    expect(approve).toBeEnabled();

    await userEvent.click(approve);
    expect(core.approveWorkflowPlan).toHaveBeenCalledWith(
      "workflow-1",
      {
        approvalId: "approval-plan",
        planId: "plan-1",
        planVersion: 1,
        planSha256: "e".repeat(64),
        expectedWorkflowRevision: 2,
      },
      { idempotencyKey: expect.any(String) },
    );
  });

  it("starts dataset questions through the autonomous agent router", async () => {
    core.listAnalysisRuns.mockResolvedValue([]);
    core.createAgentRun.mockResolvedValue(planApprovalWorkflow());
    renderPage();

    const objective = await screen.findByRole("textbox", { name: "Objective" });
    await userEvent.type(objective, "Analyze the relationship between Year and temperature.");
    await userEvent.click(screen.getByRole("button", { name: "Create analysis plan" }));

    expect(core.createAgentRun).toHaveBeenCalledWith(
      project.id,
      {
        goal: "Analyze the relationship between Year and temperature.",
        sourceIds: [dataset.id],
        mode: "autonomous",
        remoteDataApproved: false,
      },
      { idempotencyKey: expect.any(String) },
    );
  });
});
