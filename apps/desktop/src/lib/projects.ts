import { isTauri } from "./tauri";

export type ResearchTemplate =
  | "blank"
  | "literature-review"
  | "dataset-analysis"
  | "papers-and-data"
  | "reproduce-result"
  | "research-report";

export interface ProjectMetadata {
  schemaVersion: 1;
  id: string;
  title: string;
  description?: string;
  template?: string;
  starterPrompt?: string;
  createdAt: string;
  updatedAt: string;
  workspacePath: string;
  lastSessionId?: string;
}

export interface ProjectSummary extends ProjectMetadata {
  path: string;
  lastOpenedAt: string;
}

async function invokeProject<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  if (!isTauri) throw new Error("Projects are available in the Spark Agent desktop app.");
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(command, args);
}

export function createProject(parent: string, title: string, template: ResearchTemplate): Promise<ProjectSummary> {
  return invokeProject("create_project", { parent, title, template });
}

export function openProject(path: string): Promise<ProjectSummary> {
  return invokeProject("open_project", { path });
}

export function listRecentProjects(): Promise<ProjectSummary[]> {
  return isTauri ? invokeProject("list_recent_projects") : Promise.resolve([]);
}

export function removeRecentProject(path: string): Promise<void> {
  return invokeProject("remove_recent_project", { path });
}

export function updateProjectLastSession(path: string, sessionId: string): Promise<void> {
  return isTauri ? invokeProject("update_project_last_session", { path, sessionId }) : Promise.resolve();
}

export function openDemoProject(): Promise<ProjectSummary> {
  return invokeProject("open_demo_project");
}

/** A recent project owns its session association in `.spark/project.json`. */
export function projectRoute(project: Pick<ProjectSummary, "lastSessionId">): string {
  return project.lastSessionId ? `/live/${project.lastSessionId}` : "/live";
}

export const RESEARCH_TEMPLATES: Array<{
  id: ResearchTemplate;
  title: string;
  description: string;
}> = [
  { id: "blank", title: "Blank project", description: "A clean research folder with the standard layout." },
  { id: "literature-review", title: "Literature review", description: "A starter prompt for evidence gathering and synthesis." },
  { id: "dataset-analysis", title: "Dataset analysis", description: "Folders and a General Research prompt for data work." },
  { id: "papers-and-data", title: "Papers + data", description: "Bring literature and local datasets into one research project." },
  { id: "reproduce-result", title: "Reproduce a result", description: "Validate a reported result with code, data, and documented deviations." },
  { id: "research-report", title: "Research report", description: "Organize evidence, artifacts, and findings into a reviewed report." },
];
