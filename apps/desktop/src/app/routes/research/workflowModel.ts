import type {
  ResearchGenerationMode,
  ResearchWorkflowSnapshot,
  ResearchWorkflowStatus,
  ResearchWorkflowType,
  WorkflowEvent,
} from "@spark/research-domain";

export interface WorkflowCreateIntent {
  projectId: string;
  goal: string;
  workflowType: ResearchWorkflowType;
  datasetSourceId: string | null;
  generationMode: ResearchGenerationMode;
  remoteDataApproved: boolean;
  idempotencyKey: string;
}

export type WorkflowResultReviewState =
  | "passed"
  | "legacy-passed"
  | "needs-revision"
  | "pending";

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function statusLabel(status: string): string {
  return status.split("-").join(" ");
}

export function generationModeForSnapshot(
  snapshot: ResearchWorkflowSnapshot,
): ResearchGenerationMode {
  return snapshot.workflow.generationMode ?? "local-deterministic";
}

export function workflowNeedsAttention(status: ResearchWorkflowStatus): boolean {
  return status === "blocked" || status === "failed" || status === "cancelled";
}

export function resultReviewState(
  snapshot: ResearchWorkflowSnapshot,
): WorkflowResultReviewState {
  const completed = snapshot.workflow.status === "completed";
  const reviewPassed = snapshot.latestReview?.verdict === "passed";
  if (
    completed &&
    reviewPassed &&
    snapshot.result?.integrityStatus === "verified-frozen-v2"
  ) {
    return "passed";
  }
  if (
    completed &&
    reviewPassed &&
    snapshot.latestReview?.result.schemaVersion === "1"
  ) {
    return "legacy-passed";
  }
  return snapshot.latestReview?.verdict === "revision-required"
    ? "needs-revision"
    : "pending";
}

export function snapshotIsOlder(
  candidate: ResearchWorkflowSnapshot,
  current: ResearchWorkflowSnapshot,
): boolean {
  if (candidate.workflow.revision !== current.workflow.revision) {
    return candidate.workflow.revision < current.workflow.revision;
  }
  return candidate.eventCursor < current.eventCursor;
}

export function mergeWorkflowEvents(
  current: WorkflowEvent[],
  incoming: WorkflowEvent[],
): WorkflowEvent[] {
  const byId = new Map(current.map((event) => [event.id, event]));
  for (const event of incoming) byId.set(event.id, event);
  return [...byId.values()]
    .sort((left, right) => left.sequence - right.sequence)
    .slice(-100);
}

export function sameCreateIntent(
  intent: WorkflowCreateIntent | null,
  candidate: Omit<WorkflowCreateIntent, "idempotencyKey">,
): intent is WorkflowCreateIntent {
  return (
    intent?.projectId === candidate.projectId &&
    intent.goal === candidate.goal &&
    intent.workflowType === candidate.workflowType &&
    intent.datasetSourceId === candidate.datasetSourceId &&
    intent.generationMode === candidate.generationMode &&
    intent.remoteDataApproved === candidate.remoteDataApproved
  );
}
