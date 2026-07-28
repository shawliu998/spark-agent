const LAST_PROJECT_ID_KEY = "spark.research.lastProjectId";
const LAST_WORKFLOW_ID_KEY = "spark.research.lastWorkflowId";

function readPreference(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writePreference(key: string, value: string | null): void {
  try {
    if (value) window.localStorage.setItem(key, value);
    else window.localStorage.removeItem(key);
  } catch {
    // Selection remains usable for the current session when storage is unavailable.
  }
}

export function readLastProjectId(): string | null {
  return readPreference(LAST_PROJECT_ID_KEY);
}

export function writeLastProjectId(projectId: string | null): void {
  writePreference(LAST_PROJECT_ID_KEY, projectId);
}

export function readLastWorkflowId(): string | null {
  return readPreference(LAST_WORKFLOW_ID_KEY);
}

export function writeLastWorkflowId(workflowId: string | null): void {
  writePreference(LAST_WORKFLOW_ID_KEY, workflowId);
}
