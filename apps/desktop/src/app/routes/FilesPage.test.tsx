import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  DatasetAnalysisWorkflowSnapshot,
  ResearchProject,
} from "@spark/research-domain";
import { FilesPage } from "./FilesPage";

const core = vi.hoisted(() => ({
  listProjects: vi.fn(),
  listWorkflows: vi.fn(),
  fetchArtifactBlob: vi.fn(),
}));

vi.mock("@/lib/scienceCore", () => ({ scienceCore: core }));

const PROJECTS: ResearchProject[] = ["project-1", "project-2"].map(
  (id, index) => ({
    id,
    title: `Project ${index + 1}`,
    description: "",
    projectPath: `/projects/${id}`,
    researchDomain: null,
    executionMode: "safe",
    rowVersion: 1,
    archivedAt: null,
    createdAt: "2026-07-24T08:00:00Z",
    updatedAt: "2026-07-24T08:00:00Z",
  }),
);

function snapshot(
  projectId: string,
  workflowId: string,
): DatasetAnalysisWorkflowSnapshot {
  return {
    workflow: {
      id: workflowId,
      projectId,
      workflowType: "dataset-analysis",
      datasetSourceId: `dataset-${projectId}`,
      datasetContentHash: projectId === "project-1" ? "a".repeat(64) : "b".repeat(64),
      goal: `Analysis for ${projectId}`,
      generationMode: "local-deterministic",
      status: "completed",
      revision: 4,
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
      id: `spec-${projectId}`,
      revision: 2,
      status: "approved",
      selectorKind: "local-deterministic",
      selectorReason: "Exact approved analysis",
      promptVersion: null,
      datasetProfileSha256: "c".repeat(64),
      specSha256: "d".repeat(64),
      spec: {},
      createdAt: "2026-07-24T08:00:30Z",
    },
    analysisRun: {
      id: `run-${projectId}`,
      status: "completed",
      artifacts: [
        {
          id: `table-${projectId}`,
          artifactType: "table",
          path: `runs/run-${projectId}/tables/summary.csv`,
          mimeType: "text/csv",
          contentHash: "e".repeat(64),
          sizeBytes: 64,
          createdAt: "2026-07-24T08:01:00Z",
        },
        {
          id: `figure-${projectId}`,
          artifactType: "image",
          path: `runs/run-${projectId}/figures/outcome.png`,
          mimeType: "image/png",
          contentHash: "f".repeat(64),
          sizeBytes: 128,
          createdAt: "2026-07-24T08:01:01Z",
        },
      ],
    },
  } as unknown as DatasetAnalysisWorkflowSnapshot;
}

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location-search">{location.search}</output>;
}

function renderPage(
  initialEntry = "/files?projectId=project-1&workflowId=workflow-1",
) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <FilesPage />
      <LocationProbe />
    </MemoryRouter>,
  );
}

describe("FilesPage project continuity", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    core.listProjects.mockResolvedValue(PROJECTS);
    core.listWorkflows.mockImplementation(async (projectId: string) => [
      snapshot(projectId, projectId === "project-1" ? "workflow-1" : "workflow-2"),
    ]);
    core.fetchArtifactBlob.mockResolvedValue({
      text: async () => "group,mean\ncontrol,1.5\ntreated,2.5\n",
    } as Blob);
  });

  it("lists only the selected project artifacts and marks a verified table recovered", async () => {
    renderPage();
    expect(await screen.findByText("summary.csv")).toBeInTheDocument();
    expect(screen.queryByText(/run-project-2/)).not.toBeInTheDocument();

    await userEvent.click(screen.getAllByRole("button", { name: "Open" })[0]);
    expect(await screen.findByText("Recovered")).toBeInTheDocument();
    expect(screen.getByText("treated")).toBeInTheDocument();
    expect(screen.getByText("2.5")).toBeInTheDocument();
  });

  it("fails closed and labels missing and tampered artifacts without opening them", async () => {
    core.fetchArtifactBlob
      .mockRejectedValueOnce(new Error("Artifact file is missing"))
      .mockRejectedValueOnce(
        new Error("Artifact content hash no longer matches its run"),
      );
    renderPage();
    const openButtons = await screen.findAllByRole("button", { name: "Open" });

    await userEvent.click(openButtons[0]);
    expect(await screen.findByText("Missing")).toBeInTheDocument();
    expect(screen.queryByText("Recovered and hash-verified")).not.toBeInTheDocument();

    await userEvent.click(openButtons[1]);
    expect(await screen.findByText("Tampered")).toBeInTheDocument();
    expect(screen.queryByText("Recovered and hash-verified")).not.toBeInTheDocument();
  });

  it.each([
    "/files",
    "/files?projectId=project-1",
    "/files?workflowId=workflow-1",
  ])(
    "keeps both selections empty when the initial URL is incomplete: %s",
    async (initialEntry) => {
      renderPage(initialEntry);

      expect(
        await screen.findByText("Project and workflow are required"),
      ).toBeInTheDocument();
      expect(screen.getByLabelText("Project")).toHaveValue("");
      expect(screen.getByLabelText("Dataset workflow")).toHaveValue("");
      expect(core.listWorkflows).not.toHaveBeenCalled();
      expect(screen.queryByText("summary.csv")).not.toBeInTheDocument();
      expect(core.fetchArtifactBlob).not.toHaveBeenCalled();
    },
  );

  it("keeps both selections empty for an unknown project URL", async () => {
    renderPage("/files?projectId=unknown-project&workflowId=workflow-1");

    expect(
      await screen.findByText("Project selection is invalid"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Project")).toHaveValue("");
    expect(screen.getByLabelText("Dataset workflow")).toHaveValue("");
    expect(core.listWorkflows).not.toHaveBeenCalled();
    expect(screen.queryByText("summary.csv")).not.toBeInTheDocument();
    expect(core.fetchArtifactBlob).not.toHaveBeenCalled();
  });

  it.each([
    {
      name: "unknown",
      workflowId: "unknown-workflow",
      workflows: [snapshot("project-1", "workflow-1")],
    },
    {
      name: "foreign",
      workflowId: "workflow-foreign",
      workflows: [snapshot("project-2", "workflow-foreign")],
    },
  ])(
    "keeps workflow selection empty for a $name workflow URL",
    async ({ workflowId, workflows }) => {
      core.listWorkflows.mockResolvedValue(workflows);
      renderPage(`/files?projectId=project-1&workflowId=${workflowId}`);

      expect(
        await screen.findByText("Workflow selection is invalid"),
      ).toBeInTheDocument();
      expect(screen.getByLabelText("Project")).toHaveValue("project-1");
      expect(screen.getByLabelText("Dataset workflow")).toHaveValue("");
      expect(screen.queryByText("summary.csv")).not.toBeInTheDocument();
      expect(core.fetchArtifactBlob).not.toHaveBeenCalled();
    },
  );

  it("updates the URL and loads artifacts only after explicit project and workflow selection", async () => {
    renderPage("/files");
    const projectSelect = await screen.findByRole("combobox", { name: "Project" });
    await waitFor(() => expect(projectSelect).toBeEnabled());

    await userEvent.selectOptions(
      projectSelect,
      "project-1",
    );
    expect(
      await screen.findByRole("heading", { name: "Choose a dataset workflow" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("summary.csv")).not.toBeInTheDocument();
    expect(screen.getByTestId("location-search")).toHaveTextContent(
      "?projectId=project-1",
    );
    expect(screen.getByTestId("location-search")).not.toHaveTextContent(
      "workflowId",
    );

    const workflowSelect = screen.getByRole("combobox", { name: "Dataset workflow" });
    await waitFor(() => expect(workflowSelect).toBeEnabled());
    await userEvent.selectOptions(
      workflowSelect,
      "workflow-1",
    );
    expect(await screen.findByText("summary.csv")).toBeInTheDocument();
    expect(screen.getByTestId("location-search")).toHaveTextContent(
      "?projectId=project-1&workflowId=workflow-1",
    );
    expect(core.fetchArtifactBlob).not.toHaveBeenCalled();
  });
});
