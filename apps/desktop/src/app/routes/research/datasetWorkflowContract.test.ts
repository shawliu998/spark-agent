import { describe, expect, it } from "vitest";
import {
  DATASET_ANALYSIS_RUNTIME_ARTIFACT_REQUIREMENTS,
  DATASET_ANALYSIS_STEP_SEQUENCE,
} from "@spark/research-domain";
import type {
  DatasetAnalysisPlanSpec,
  WorkflowAnalysisExecutionPendingApproval,
  WorkflowAnalysisIntent,
  WorkflowEvent,
} from "@spark/research-domain";

const DATASET_HASH = "a".repeat(64);

const plan = {
  schemaVersion: "1",
  workflowType: "dataset-analysis",
  goal: "Compare the primary outcome across experimental groups.",
  datasetSourceId: "dataset-1",
  datasetContentHash: DATASET_HASH,
  analysisSpecId: null,
  analysisSpecSha256: null,
  assumptions: [],
  questionsForUser: [],
  steps: [
    {
      key: "inspect-dataset",
      type: "dataset-inspection",
      objective: "Profile the immutable dataset.",
      dependencies: [],
      inputs: {
        datasetSourceId: "dataset-1",
        datasetContentHash: DATASET_HASH,
        samplingMethod: "head-and-reservoir-v1",
        maxSampleRows: 1_000,
      },
      expectedArtifacts: ["dataset-profile"],
      acceptanceCriteria: ["Record the dataset hash."],
      riskLevel: "low",
    },
    {
      key: "prepare-analysis",
      type: "prepare-analysis",
      objective: "Prepare policy-compliant Python.",
      dependencies: ["inspect-dataset"],
      inputs: {
        datasetSourceId: "dataset-1",
        datasetContentHash: DATASET_HASH,
        profileStepKey: "inspect-dataset",
      },
      expectedArtifacts: ["analysis-intent"],
      acceptanceCriteria: ["Bind code to an immutable approval payload."],
      riskLevel: "medium",
    },
    {
      key: "execute-analysis",
      type: "python-data-analysis",
      objective: "Execute only explicitly approved Python.",
      dependencies: ["prepare-analysis"],
      inputs: {
        datasetSourceId: "dataset-1",
        datasetContentHash: DATASET_HASH,
        preparationStepKey: "prepare-analysis",
        expectedOutputs: [
          "executed-notebook",
          "analysis-log",
          "environment-manifest",
        ],
        timeoutSeconds: 600,
      },
      expectedArtifacts: [
        "executed-notebook",
        "analysis-log",
        "environment-manifest",
      ],
      acceptanceCriteria: ["Execute exactly the approved payload hash."],
      riskLevel: "high",
    },
    {
      key: "collect-artifacts",
      type: "collect-artifacts",
      objective: "Verify and record every required artifact.",
      dependencies: ["execute-analysis"],
      inputs: {
        executionStepKey: "execute-analysis",
        expectedOutputs: [
          "executed-notebook",
          "analysis-log",
          "environment-manifest",
        ],
      },
      expectedArtifacts: [
        "executed-notebook",
        "analysis-log",
        "environment-manifest",
      ],
      acceptanceCriteria: ["Verify every artifact hash."],
      riskLevel: "low",
    },
  ],
} satisfies DatasetAnalysisPlanSpec;

const failedJobEvent = {
  id: "event-job-failed",
  sequence: 9,
  type: "job.failed",
  taskId: null,
  jobId: "job-1",
  data: {
    jobId: "job-1",
    kind: "execute-task",
    attempt: 1,
    errorCode: "analysis-runtime-failed",
  },
  createdAt: "2026-07-15T00:00:00Z",
} satisfies WorkflowEvent;

const executionApproval = {
  id: "approval-1",
  workflowId: "workflow-1",
  planId: "plan-1",
  taskId: "task-execute",
  kind: "analysis-execution",
  status: "waiting",
  subjectType: "analysis-intent",
  subjectId: "intent-1",
  action: "execute-python-data-analysis",
  payloadSha256: "b".repeat(64),
  riskLevel: "high",
  reason: "Execute only the displayed immutable analysis intent.",
  affectedResources: ["dataset:dataset-1"],
  createdAt: "2026-07-15T00:00:00Z",
  decidedAt: null,
  approvalSchemaVersion: "analysis-intent-v3",
  expectedWorkflowRevision: 4,
  analysisIntentId: "intent-1",
  planStepId: "execute-analysis",
  datasetSourceId: "dataset-1",
  datasetContentHash: DATASET_HASH,
  expectedOutputs: [
    "executed-notebook",
    "analysis-log",
    "environment-manifest",
  ],
  timeoutSeconds: 600,
  code: "print('approved')",
  codeDiff: null,
  analysisSpecId: null,
  specSha256: null,
  datasetProfileSha256: null,
  compilerVersion: null,
  codeSha256: null,
  runtimePolicyId: null,
} satisfies WorkflowAnalysisExecutionPendingApproval;

const compiledExecutionApproval = {
  ...executionApproval,
  approvalSchemaVersion: "analysis-intent-v4",
  analysisSpecId: "spec-1",
  specSha256: "c".repeat(64),
  datasetProfileSha256: "d".repeat(64),
  compilerVersion: "analysis-spec-compiler-v1",
  codeSha256: "e".repeat(64),
  runtimePolicyId: "dataset-analysis-spec-v1",
} satisfies WorkflowAnalysisExecutionPendingApproval;

const completedInitialIntent = {
  id: "intent-1",
  taskId: "task-execute",
  projectId: "project-1",
  datasetSourceId: "dataset-1",
  datasetContentHash: DATASET_HASH,
  objective: "Compute a reproducible summary.",
  code: executionApproval.code,
  payloadSha256: executionApproval.payloadSha256,
  riskLevel: "high",
  affectedResources: executionApproval.affectedResources,
  status: "completed",
  decision: "approved",
  workflowId: "workflow-1",
  planStepId: "execute-analysis",
  previousIntentId: null,
  expectedOutputs: executionApproval.expectedOutputs,
  timeoutSeconds: executionApproval.timeoutSeconds,
  repairAttempt: 0,
  errorSummary: null,
  codeDiff: null,
  createdAt: "2026-07-15T00:00:00Z",
  updatedAt: "2026-07-15T00:01:00Z",
} satisfies WorkflowAnalysisIntent;

describe("dataset workflow domain contract", () => {
  it("requires complete compiled provenance for v4 execution approval", () => {
    expect(compiledExecutionApproval).toMatchObject({
      approvalSchemaVersion: "analysis-intent-v4",
      analysisSpecId: "spec-1",
      compilerVersion: "analysis-spec-compiler-v1",
      runtimePolicyId: "dataset-analysis-spec-v1",
    });
  });

  it("freezes the registered four-step sequence", () => {
    expect(
      plan.steps.map(({ key, type, riskLevel }) => ({ key, type, riskLevel })),
    ).toEqual(DATASET_ANALYSIS_STEP_SEQUENCE);
  });

  it("maps mandatory execution audit outputs to persisted artifact types", () => {
    expect(DATASET_ANALYSIS_RUNTIME_ARTIFACT_REQUIREMENTS["analysis-log"]).toEqual([
      "stdout",
      "stderr",
      "log",
    ]);
    expect(
      DATASET_ANALYSIS_RUNTIME_ARTIFACT_REQUIREMENTS["executed-notebook"],
    ).toEqual(["notebook-executed"]);
    expect(
      DATASET_ANALYSIS_RUNTIME_ARTIFACT_REQUIREMENTS["environment-manifest"],
    ).toEqual(["environment"]);
  });

  it("keeps persisted workflow job events reachable in the typed union", () => {
    expect(failedJobEvent.type).toBe("job.failed");
    expect(failedJobEvent.data.jobId).toBe(failedJobEvent.jobId);
  });

  it("keeps execution approval action and risk literals immutable", () => {
    expect(executionApproval.action).toBe("execute-python-data-analysis");
    expect(executionApproval.riskLevel).toBe("high");
  });

  it("binds workflow intent lifecycle status to its decision", () => {
    expect(completedInitialIntent.decision).toBe("approved");
    expect(completedInitialIntent.errorSummary).toBeNull();
  });

});
