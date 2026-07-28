import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  DatasetAnalysisWorkflowSnapshot,
  ResearchProject,
} from "@spark/research-domain";
import { NotebooksPage } from "./NotebooksPage";

const core = vi.hoisted(() => ({
  listProjects: vi.fn(),
  listWorkflows: vi.fn(),
  fetchArtifactBlob: vi.fn(),
}));

vi.mock("@/lib/scienceCore", () => ({ scienceCore: core }));

const PROJECT: ResearchProject = {
  id: "project-1",
  title: "Trial analysis",
  description: "",
  projectPath: "/projects/project-1",
  researchDomain: null,
  executionMode: "safe",
  rowVersion: 1,
  archivedAt: null,
  createdAt: "2026-07-24T08:00:00Z",
  updatedAt: "2026-07-24T08:00:00Z",
};

function completedSnapshot(
  projectId = "project-1",
): DatasetAnalysisWorkflowSnapshot {
  return {
    workflow: {
      id: "workflow-1",
      projectId,
      workflowType: "dataset-analysis",
      datasetSourceId: "dataset-1",
      datasetContentHash: "a".repeat(64),
      goal: "Compare trial outcomes",
      generationMode: "local-deterministic",
      status: "completed",
      revision: 8,
      planVersion: 1,
      currentStepId: null,
      retryCount: 0,
      blockingReason: null,
      cancelRequestedAt: null,
      createdAt: "2026-07-24T08:00:00Z",
      updatedAt: "2026-07-24T08:02:00Z",
      completedAt: "2026-07-24T08:02:00Z",
    },
    analysisSpec: {
      id: "spec-1",
      revision: 3,
      status: "approved",
      selectorKind: "local-deterministic",
      selectorReason: "Exact requested comparison",
      promptVersion: null,
      datasetProfileSha256: "b".repeat(64),
      specSha256: "c".repeat(64),
      spec: {},
      createdAt: "2026-07-24T08:00:30Z",
    },
    analysisRun: {
      id: "run-1",
      status: "completed",
      artifacts: [
        {
          id: "notebook-1",
          artifactType: "notebook-executed",
          path: "runs/run-1/a/very/long/path/executed-analysis.ipynb",
          mimeType: "application/x-ipynb+json",
          contentHash: "d".repeat(64),
          sizeBytes: 512,
          createdAt: "2026-07-24T08:01:30Z",
        },
      ],
    },
  } as unknown as DatasetAnalysisWorkflowSnapshot;
}

function renderPage() {
  return render(
    <MemoryRouter
      initialEntries={["/notebooks?projectId=project-1&workflowId=workflow-1"]}
    >
      <NotebooksPage />
    </MemoryRouter>,
  );
}

describe("NotebooksPage project continuity", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    core.listProjects.mockResolvedValue([PROJECT]);
    core.listWorkflows.mockResolvedValue([completedSnapshot()]);
    core.fetchArtifactBlob.mockResolvedValue({
      text: async () =>
        JSON.stringify({
          cells: [
            {
              cell_type: "code",
              execution_count: 1,
              metadata: {},
              source: ["summary = dataset.describe()"],
              outputs: [
                {
                  output_type: "stream",
                  name: "stdout",
                  text: ["analysis complete"],
                },
              ],
            },
          ],
          metadata: {},
          nbformat: 4,
          nbformat_minor: 5,
        }),
    } as Blob);
  });

  it("lists the completed project notebook with exact lineage and recovers it read-only", async () => {
    renderPage();

    expect(
      await screen.findByText("executed-analysis.ipynb"),
    ).toBeInTheDocument();
    expect(screen.getByText(/dataset-1/)).toHaveTextContent("a".repeat(64));
    expect(screen.getByText(/Revision 3/)).toHaveTextContent("c".repeat(64));
    expect(
      screen.getByTitle("runs/run-1/a/very/long/path/executed-analysis.ipynb"),
    ).toHaveTextContent("runs/run-1/a/very/long/path/executed-analysis.ipynb");

    await userEvent.click(
      screen.getByRole("button", { name: "Open notebook" }),
    );
    expect(await screen.findByText("Recovered")).toBeInTheDocument();
    expect(screen.getByText("summary = dataset.describe()")).toBeInTheDocument();
    expect(screen.getByText("analysis complete")).toBeInTheDocument();
  });

  it("reloads the same persisted project/workflow after remount without runtime state", async () => {
    const first = renderPage();
    expect(
      await screen.findByText("executed-analysis.ipynb"),
    ).toBeInTheDocument();
    first.unmount();

    renderPage();
    expect(
      await screen.findByText("executed-analysis.ipynb"),
    ).toBeInTheDocument();
    await waitFor(() => expect(core.listWorkflows).toHaveBeenCalledTimes(2));
    expect(core.listWorkflows).toHaveBeenLastCalledWith(
      "project-1",
      expect.objectContaining({ activeOnly: false, limit: 100 }),
    );
    expect(core).not.toHaveProperty("health");
  });
});
