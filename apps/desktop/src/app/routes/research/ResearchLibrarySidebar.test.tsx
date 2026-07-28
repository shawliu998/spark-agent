import { fireEvent, render, screen } from "@testing-library/react";
import type {
  ResearchProject,
  ResearchWorkflowSnapshot,
} from "@spark/research-domain";
import { describe, expect, it, vi } from "vitest";
import { ResearchLibrarySidebar } from "./ResearchLibrarySidebar";

const activeProject: ResearchProject = {
  id: "project-1",
  title: "Active project",
  description: "",
  projectPath: "/tmp/project-1",
  researchDomain: null,
  executionMode: "safe",
  rowVersion: 3,
  archivedAt: null,
  createdAt: "2026-07-24T00:00:00Z",
  updatedAt: "2026-07-24T00:00:00Z",
};

const archivedProject: ResearchProject = {
  ...activeProject,
  id: "project-2",
  title: "Archived project",
  rowVersion: 5,
  archivedAt: "2026-07-24T01:00:00Z",
};

const completedLiteratureWorkflow = {
  workflow: {
    id: "workflow-report",
    goal: "Summarize the evidence",
    status: "completed",
    workflowType: "literature-synthesis",
    generationMode: "local-deterministic",
    cancelRequestedAt: null,
  },
  allowedActions: [],
  result: {},
} as unknown as ResearchWorkflowSnapshot;

function renderSidebar(overrides: Partial<React.ComponentProps<typeof ResearchLibrarySidebar>> = {}) {
  const props: React.ComponentProps<typeof ResearchLibrarySidebar> = {
    health: null,
    booting: false,
    serviceReady: true,
    projects: [activeProject],
    projectId: activeProject.id,
    workflows: [],
    snapshot: null,
    selectedWorkflowId: null,
    loadingWorkflows: false,
    workflowMutating: false,
    sources: [],
    loadingSources: false,
    selection: null,
    importing: false,
    projectMutating: false,
    showArchivedProjects: false,
    onProjectChange: vi.fn(),
    onNewProject: vi.fn(),
    onSelectWorkflow: vi.fn(),
    onOpenWorkflowReport: vi.fn(),
    onNewWorkflow: vi.fn(),
    onSelectSource: vi.fn(),
    onImportPdf: vi.fn(),
    onToggleArchivedProjects: vi.fn(),
    onRenameProject: vi.fn(),
    onArchiveProject: vi.fn(),
    onRestoreProject: vi.fn(),
    ...overrides,
  };
  return { ...render(<ResearchLibrarySidebar {...props} />), props };
}

describe("ResearchLibrarySidebar project actions", () => {
  it("requires confirmation before archiving and exposes keyboard form controls", () => {
    const { props } = renderSidebar();
    fireEvent.click(screen.getByRole("button", { name: "Archive" }));
    expect(props.onArchiveProject).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent("Archive this project");
    fireEvent.click(screen.getByRole("button", { name: "Archive project" }));
    expect(props.onArchiveProject).toHaveBeenCalledTimes(1);
  });

  it("renames through an inline labelled form", () => {
    const { props } = renderSidebar();
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    const input = screen.getByRole("textbox", { name: "Rename project" });
    fireEvent.change(input, { target: { value: "Renamed project" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(props.onRenameProject).toHaveBeenCalledWith("Renamed project");
  });

  it("hides archived projects by default and can show and restore them", () => {
    const onToggleArchivedProjects = vi.fn();
    const onRestoreProject = vi.fn();
    const hidden = renderSidebar({
      projects: [activeProject, archivedProject],
      showArchivedProjects: false,
      onToggleArchivedProjects,
    });
    expect(screen.queryByRole("option", { name: /Archived project/ })).not.toBeInTheDocument();
    hidden.unmount();

    const first = renderSidebar({
      projects: [activeProject, archivedProject],
      showArchivedProjects: true,
      projectId: archivedProject.id,
      onToggleArchivedProjects,
      onRestoreProject,
    });
    expect(screen.getByRole("option", { name: /Archived project/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(onRestoreProject).toHaveBeenCalledTimes(1);
    first.unmount();

    const hiddenToggle = vi.fn();
    renderSidebar({
      projects: [activeProject],
      onToggleArchivedProjects: hiddenToggle,
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "Show archived" }));
    expect(hiddenToggle).toHaveBeenCalledWith(true);
  });

  it("opens a completed literature report without changing task-row selection", () => {
    const onOpenWorkflowReport = vi.fn();
    const onSelectWorkflow = vi.fn();
    renderSidebar({
      workflows: [completedLiteratureWorkflow],
      onOpenWorkflowReport,
      onSelectWorkflow,
    });

    fireEvent.click(screen.getByRole("button", { name: "View report" }));

    expect(onOpenWorkflowReport).toHaveBeenCalledWith("workflow-report");
    expect(onSelectWorkflow).not.toHaveBeenCalled();
  });
});
