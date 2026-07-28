import { describe, expect, expectTypeOf, it } from "vitest";
import type {
  AgentDecisionOut,
  AgentLoopLimitState,
  AgentResearchWorkflowSnapshot,
  AnalysisSpec,
  StepObservationOut,
  WorkflowEvent,
} from "@spark/research-domain";

const DATASET_HASH = "a".repeat(64);
const PROFILE_HASH = "b".repeat(64);

const proposedSpec = {
  schemaVersion: "1",
  objective: "Compare score between treatment groups.",
  datasetSourceId: "dataset-1",
  datasetContentHash: DATASET_HASH,
  datasetProfileHash: PROFILE_HASH,
  operation: {
    type: "two-group-comparison",
    outcomeColumn: "score",
    groupColumn: "arm",
    groups: ["control", "treatment"],
    method: "mann-whitney-u",
    effectSize: "rank-biserial",
    checkAssumptions: true,
    plot: "boxplot",
  },
  missingValuePolicy: "drop-per-operation",
  confidenceLevel: 0.95,
  randomSeed: 42,
  assumptions: ["Observations are independent."],
  limitations: ["One group has zero variance."],
} satisfies AnalysisSpec;

const observation = {
  schemaVersion: "1",
  id: "observation-1",
  workflowId: "workflow-1",
  planId: "plan-1",
  taskId: "task-execute",
  sourceJobId: "job-execute-1",
  runId: "run-1",
  reviewId: null,
  observationType: "analysis-execution",
  stepKey: "execute-analysis",
  attempt: 1,
  status: "failed",
  facts: [
    {
      code: "method-used",
      statement: "The approved method was Welch's t-test.",
      value: "welch-t-test",
      sourceType: "analysis-spec",
      sourceId: "analysis-spec-1",
    },
  ],
  warnings: [
    {
      code: "zero-variance",
      message: "The control group has zero variance.",
      severity: "error",
      sourceId: "run-1",
    },
  ],
  unresolvedQuestions: [],
  artifactIds: [],
  failureCategory: "method",
  recommendedActions: ["revise-analysis-spec", "stop"],
  inputSha256: "c".repeat(64),
  outputSha256: "d".repeat(64),
  generator: "deterministic-observer-v1",
  promptVersion: null,
  model: null,
  modelInvocationId: null,
  createdAt: "2026-07-16T10:00:00Z",
} satisfies StepObservationOut;

const pendingDecision = {
  schemaVersion: "1",
  id: "decision-1",
  workflowId: "workflow-1",
  observationId: observation.id,
  decisionRevision: 1,
  action: "revise-analysis-spec",
  reasonCode: "method-zero-variance",
  reason: "Welch's t-test is not suitable when one group has zero variance.",
  targetStepKey: null,
  clarificationRequests: [],
  proposedAnalysisSpec: proposedSpec,
  analysisSpecDiff: {
    changedFields: ["operation.method", "operation.effectSize"],
    previousValues: {
      "operation.method": "welch-t-test",
      "operation.effectSize": "hedges-g",
    },
    proposedValues: {
      "operation.method": "mann-whitney-u",
      "operation.effectSize": "rank-biserial",
    },
    reason: "Use a supported rank-based comparison.",
  },
  requiresUserConfirmation: true,
  status: "waiting-user-confirmation",
  expectedWorkflowRevision: 12,
  generator: "deterministic-action-policy-v1",
  promptVersion: null,
  model: null,
  modelInvocationId: null,
  inputSha256: "e".repeat(64),
  outputSha256: "f".repeat(64),
  researchContextSnapshotId: null,
  researchContextSnapshotSha256: null,
  appliedAt: null,
  createdAt: "2026-07-16T10:00:01Z",
} satisfies AgentDecisionOut;

const agentLoopLimits = {
  agentSteps: { count: 2, limit: 8, reached: false },
  planRevisions: { count: 0, limit: 2, reached: false },
  analysisSpecRevisions: { count: 1, limit: 2, reached: false },
  stepRetries: { count: 0, limit: 2, reached: false },
  clarificationRounds: { count: 1, limit: 3, reached: false },
  modelDecisions: { count: 0, limit: 5, reached: false },
  invalidModelDecisions: { count: 0, limit: 2, reached: false },
} satisfies AgentLoopLimitState;

const snapshot = {
  workflow: {
    id: "workflow-1",
    projectId: "project-1",
    goal: "Compare treatment and control score.",
    mode: "autonomous",
    sourceIds: ["dataset-1"],
    workflowType: "dataset-analysis",
    generationMode: "local-deterministic",
    status: "reviewing",
    revision: 12,
    currentStepId: "task-execute",
    planVersion: 1,
    retryCount: 0,
    statusReason: null,
    cancelRequestedAt: null,
    createdAt: "2026-07-16T09:00:00Z",
    updatedAt: "2026-07-16T10:00:01Z",
    completedAt: null,
  },
  intentDecision: null,
  interactions: [],
  plan: null,
  pendingApprovals: [],
  result: null,
  latestReview: null,
  datasetProfile: null,
  analysisIntent: null,
  analysisRun: null,
  analysisSpec: null,
  structuredResult: null,
  reviewWarningAcceptance: null,
  latestObservation: observation,
  pendingDecision,
  decisionHistory: [
    {
      id: pendingDecision.id,
      observationId: observation.id,
      action: pendingDecision.action,
      reason: pendingDecision.reason,
      status: pendingDecision.status,
      requiresUserConfirmation: true,
      researchContextSnapshotId: pendingDecision.researchContextSnapshotId,
      researchContextSnapshotSha256:
        pendingDecision.researchContextSnapshotSha256,
      createdAt: pendingDecision.createdAt,
      appliedAt: null,
    },
  ],
  agentLoopLimits,
  allowedActions: [
    "approve-agent-decision",
    "reject-agent-decision",
    "cancel",
  ],
  eventCursor: 21,
} satisfies AgentResearchWorkflowSnapshot;

const decisionEvent = {
  id: "event-decision-proposed",
  sequence: 21,
  type: "agent.decision-proposed",
  taskId: "task-execute",
  jobId: "job-decide-1",
  data: {
    observationId: observation.id,
    decisionId: pendingDecision.id,
    action: "revise-analysis-spec",
    taskId: "task-execute",
    targetStepKey: null,
    previousAnalysisSpecId: "analysis-spec-1",
    proposedAnalysisSpecId: null,
    expectedWorkflowRevision: 12,
    reasonCode: "method-zero-variance",
    researchContextSnapshotId: null,
    researchContextSnapshotSha256: null,
    discoverySelection: null,
    discoverySelectionSha256: null,
  },
  createdAt: "2026-07-16T10:00:01Z",
} satisfies WorkflowEvent;

describe("agent loop domain contract", () => {
  it("binds an observation to its durable source job and structured sources", () => {
    expectTypeOf(observation).toMatchTypeOf<StepObservationOut>();
    expect(observation.sourceJobId).toBe("job-execute-1");
    expect(observation.facts[0]?.sourceType).toBe("analysis-spec");
  });

  it("requires a proposed spec and field diff for a revision decision", () => {
    expectTypeOf(pendingDecision).toMatchTypeOf<AgentDecisionOut>();
    expect(pendingDecision.requiresUserConfirmation).toBe(true);
    expect(pendingDecision.analysisSpecDiff.changedFields).toContain(
      "operation.method",
    );
  });

  it("exposes the complete agent loop snapshot and all seven persisted limits", () => {
    expect(snapshot.latestObservation?.id).toBe(observation.id);
    expect(snapshot.pendingDecision?.id).toBe(pendingDecision.id);
    expect(Object.keys(snapshot.agentLoopLimits)).toHaveLength(7);
  });

  it("keeps agent decision events strictly typed and revision-bound", () => {
    expectTypeOf(decisionEvent).toMatchTypeOf<WorkflowEvent>();
    expect(decisionEvent.data.expectedWorkflowRevision).toBe(12);
    expect(decisionEvent.data.previousAnalysisSpecId).toBe("analysis-spec-1");
  });
});
