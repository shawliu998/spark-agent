export type ResearchTaskStatus =
  | "draft-plan"
  | "waiting-approval"
  | "searching-sources"
  | "building-evidence"
  | "preparing-run"
  | "waiting-execution-approval"
  | "running"
  | "reviewing"
  | "needs-revision"
  | "completed"
  | "failed"
  | "cancelled";

/** Canonical workflow states owned by science-core. */
export type ResearchWorkflowStatus =
  | "planning"
  | "waiting-plan-approval"
  | "running"
  | "reviewing"
  | "completed"
  | "blocked"
  | "failed"
  | "cancelled";

/** Actions science-core currently permits for a workflow snapshot. */
export type ResearchWorkflowAllowedAction =
  | "approve-plan"
  | "approve-analysis"
  | "reject-analysis"
  | "accept-review-warnings"
  | "cancel"
  | "retry"
  | "resume";

export type LiteratureResearchWorkflowAllowedAction =
  | "approve-plan"
  | "cancel"
  | "retry"
  | "resume";

export type ResearchWorkflowType =
  | "literature-synthesis"
  | "dataset-analysis";

export type WorkflowRiskLevel = "low" | "medium" | "high";

/**
 * Describes how a workflow generates its plan and narrative output.
 * Evidence verification remains deterministic in both modes.
 */
export type ResearchGenerationMode =
  | "local-deterministic"
  | "remote-model-assisted";

export interface CreateLiteratureResearchWorkflowInput {
  goal: string;
  workflowType: "literature-synthesis";
  generationMode: ResearchGenerationMode;
  /** Explicit approval to send the goal when remote assistance is selected. */
  remoteDataApproved: boolean;
}

export interface CreateDatasetAnalysisWorkflowInput {
  goal: string;
  workflowType: "dataset-analysis";
  datasetSourceId: string;
  /** Dataset analysis is local-only in the current product contract. */
  generationMode: "local-deterministic";
  remoteDataApproved: false;
}

/** Creation is discriminated by workflowType and cannot omit dataset identity. */
export type CreateResearchWorkflowInput =
  | CreateLiteratureResearchWorkflowInput
  | CreateDatasetAnalysisWorkflowInput;

export type WorkflowAnalysisIntentDecision = "approved" | "rejected";

export interface DecideWorkflowAnalysisIntentInput {
  /** Encoded in the request path and checked against the approval subject. */
  intentId: string;
  approvalId: string;
  decision: WorkflowAnalysisIntentDecision;
  payloadSha256: string;
  expectedWorkflowRevision: number;
}

export interface AcceptWorkflowReviewWarningsInput {
  reviewId: string;
  reviewInputSha256: string;
  expectedWorkflowRevision: number;
  decision: "accepted";
}

export interface WorkflowApiErrorDetail {
  code: string;
  userMessage: string;
  retryable: boolean;
}

export interface WorkflowBlockingReason {
  code: string;
  userMessage: string;
  retryable: boolean;
}

interface ResearchWorkflowBase {
  id: string;
  projectId: string;
  goal: string;
  /** Optional while reading snapshots created before model-assisted v2. */
  generationMode?: ResearchGenerationMode;
  status: ResearchWorkflowStatus;
  revision: number;
  currentStepId: string | null;
  planVersion: number | null;
  retryCount: number;
  blockingReason: WorkflowBlockingReason | null;
  cancelRequestedAt: string | null;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
}

export interface LiteratureResearchWorkflow extends ResearchWorkflowBase {
  workflowType: "literature-synthesis";
  /** science-core includes explicit nulls in current response snapshots. */
  datasetSourceId?: null;
  datasetContentHash?: null;
}

export interface DatasetAnalysisWorkflow extends ResearchWorkflowBase {
  workflowType: "dataset-analysis";
  /** Immutable source identity captured when the workflow is created. */
  datasetSourceId: string;
  /** SHA-256 of the exact dataset bytes approved for this workflow. */
  datasetContentHash: string;
}

export type ResearchWorkflow =
  | LiteratureResearchWorkflow
  | DatasetAnalysisWorkflow;

export type ResearchWorkflowPlanStatus =
  | "pending-approval"
  | "approved"
  | "rejected"
  | "superseded";

export type ResearchWorkflowTaskStatus =
  | "pending"
  | "queued"
  | "running"
  | "waiting-approval"
  | "completed"
  | "blocked"
  | "failed"
  | "cancelled";

export type LiteratureResearchWorkflowTaskType =
  | "inspect-sources"
  | "extract-local-evidence"
  | "synthesize-extractive-claims";

/** Registered science-core handlers for the fixed dataset workflow. */
export type DatasetAnalysisTaskType =
  | "dataset-inspection"
  | "prepare-analysis"
  | "python-data-analysis"
  | "collect-artifacts";

export type ResearchWorkflowTaskType =
  | LiteratureResearchWorkflowTaskType
  | DatasetAnalysisTaskType;

export interface FrozenResearchSource {
  sourceId: string;
  title: string;
  contentHash: string;
  pageManifestHash: string;
}

export interface InspectSourcesInput {
  sourceKind: "pdf";
  /** Retained for snapshots created before content-bound source descriptors. */
  sourceIds: string[] | null;
  /** Remote plans bind approval to both file bytes and parsed-page content. */
  frozenSources?: FrozenResearchSource[] | null;
}

export interface ExtractLocalEvidenceInput {
  query: string;
  maxPassages: number;
  maxPerSource: number;
}

export interface SynthesizeExtractiveClaimsInput {
  maxClaims: number;
}

export type ResearchWorkflowStepInput =
  | InspectSourcesInput
  | ExtractLocalEvidenceInput
  | SynthesizeExtractiveClaimsInput;

export type ResearchWorkflowExpectedOutput =
  | "sources"
  | "evidence"
  | "claims"
  | "evidence-map";

export type ResearchWorkflowAcceptanceCriterion =
  | "at-least-one-ready-pdf"
  | "at-least-one-verified-evidence"
  | "at-least-one-claim"
  | "every-claim-has-verified-evidence";

export interface ResearchWorkflowStepSpec {
  key: string;
  type: LiteratureResearchWorkflowTaskType;
  objective: string;
  inputs: ResearchWorkflowStepInput;
  expectedOutputs: ResearchWorkflowExpectedOutput[];
  acceptanceCriteria: ResearchWorkflowAcceptanceCriterion[];
}

export interface ResearchWorkflowPlanSpec {
  schemaVersion: "1";
  goal: string;
  steps: ResearchWorkflowStepSpec[];
}

export type DatasetAnalysisStepKey =
  | "inspect-dataset"
  | "prepare-analysis"
  | "execute-analysis"
  | "collect-artifacts";

export type DatasetAnalysisArtifactKind =
  | "dataset-profile"
  | "analysis-intent"
  | "executed-notebook"
  | "summary-table"
  | "figure"
  | "analysis-log"
  | "environment-manifest";

export type DatasetAnalysisExpectedOutput =
  | "dataset-profile"
  | "analysis-code"
  | "executed-notebook"
  | "summary-table"
  | "figures"
  | "analysis-log"
  | "environment-manifest";

/** Canonical output order makes mandatory runtime evidence unskippable. */
export type DatasetAnalysisExecutionExpectedOutputs =
  | ["executed-notebook", "analysis-log", "environment-manifest"]
  | [
      "executed-notebook",
      "summary-table",
      "analysis-log",
      "environment-manifest",
    ]
  | [
      "executed-notebook",
      "figures",
      "analysis-log",
      "environment-manifest",
    ]
  | [
      "executed-notebook",
      "summary-table",
      "figures",
      "analysis-log",
      "environment-manifest",
    ];

export type DatasetAnalysisExecutionExpectedArtifacts =
  | ["executed-notebook", "analysis-log", "environment-manifest"]
  | [
      "executed-notebook",
      "summary-table",
      "analysis-log",
      "environment-manifest",
    ]
  | [
      "executed-notebook",
      "figure",
      "analysis-log",
      "environment-manifest",
    ]
  | [
      "executed-notebook",
      "summary-table",
      "figure",
      "analysis-log",
      "environment-manifest",
    ];

export type DatasetAnalysisRuntimeArtifactType =
  | "notebook-input"
  | "notebook-executed"
  | "environment"
  | "stdout"
  | "stderr"
  | "log"
  | "figure"
  | "dataset"
  | "structured-data";

export interface DatasetInspectionStepInput {
  datasetSourceId: string;
  datasetContentHash: string;
  samplingMethod: "head-and-reservoir-v1";
  maxSampleRows: number;
}

export interface PrepareAnalysisStepInput {
  datasetSourceId: string;
  datasetContentHash: string;
  profileStepKey: "inspect-dataset";
}

export interface ExecuteAnalysisStepInput {
  datasetSourceId: string;
  datasetContentHash: string;
  preparationStepKey: "prepare-analysis";
  expectedOutputs: DatasetAnalysisExecutionExpectedOutputs;
  timeoutSeconds: number;
}

export interface CollectArtifactsStepInput {
  executionStepKey: "execute-analysis";
  expectedOutputs: DatasetAnalysisExecutionExpectedOutputs;
}

interface DatasetAnalysisStepBase {
  key: DatasetAnalysisStepKey;
  type: DatasetAnalysisTaskType;
  objective: string;
  dependencies: DatasetAnalysisStepKey[];
  acceptanceCriteria: [string, ...string[]];
  riskLevel: WorkflowRiskLevel;
}

export interface DatasetInspectionPlanStep
  extends DatasetAnalysisStepBase {
  key: "inspect-dataset";
  type: "dataset-inspection";
  dependencies: [];
  inputs: DatasetInspectionStepInput;
  expectedArtifacts: ["dataset-profile"];
  riskLevel: "low";
}

export interface PrepareAnalysisPlanStep extends DatasetAnalysisStepBase {
  key: "prepare-analysis";
  type: "prepare-analysis";
  dependencies: ["inspect-dataset"];
  inputs: PrepareAnalysisStepInput;
  expectedArtifacts: ["analysis-intent"];
  riskLevel: "medium";
}

interface ExecuteAnalysisPlanStepBase extends DatasetAnalysisStepBase {
  key: "execute-analysis";
  type: "python-data-analysis";
  dependencies: ["prepare-analysis"];
  riskLevel: "high";
}

interface CollectArtifactsPlanStepBase extends DatasetAnalysisStepBase {
  key: "collect-artifacts";
  type: "collect-artifacts";
  dependencies: ["execute-analysis"];
  riskLevel: "low";
}

type ExecuteAnalysisPlanStepFor<
  Outputs extends DatasetAnalysisExecutionExpectedOutputs,
  Artifacts extends DatasetAnalysisExecutionExpectedArtifacts,
> = ExecuteAnalysisPlanStepBase & {
  inputs: Omit<ExecuteAnalysisStepInput, "expectedOutputs"> & {
    expectedOutputs: Outputs;
  };
  expectedArtifacts: Artifacts;
};

type CollectArtifactsPlanStepFor<
  Outputs extends DatasetAnalysisExecutionExpectedOutputs,
  Artifacts extends DatasetAnalysisExecutionExpectedArtifacts,
> = CollectArtifactsPlanStepBase & {
  inputs: Omit<CollectArtifactsStepInput, "expectedOutputs"> & {
    expectedOutputs: Outputs;
  };
  expectedArtifacts: Artifacts;
};

export type ExecuteAnalysisPlanStep =
  | ExecuteAnalysisPlanStepFor<
      ["executed-notebook", "analysis-log", "environment-manifest"],
      ["executed-notebook", "analysis-log", "environment-manifest"]
    >
  | ExecuteAnalysisPlanStepFor<
      [
        "executed-notebook",
        "summary-table",
        "analysis-log",
        "environment-manifest",
      ],
      [
        "executed-notebook",
        "summary-table",
        "analysis-log",
        "environment-manifest",
      ]
    >
  | ExecuteAnalysisPlanStepFor<
      [
        "executed-notebook",
        "figures",
        "analysis-log",
        "environment-manifest",
      ],
      [
        "executed-notebook",
        "figure",
        "analysis-log",
        "environment-manifest",
      ]
    >
  | ExecuteAnalysisPlanStepFor<
      [
        "executed-notebook",
        "summary-table",
        "figures",
        "analysis-log",
        "environment-manifest",
      ],
      [
        "executed-notebook",
        "summary-table",
        "figure",
        "analysis-log",
        "environment-manifest",
      ]
    >;

export type CollectArtifactsPlanStep =
  | CollectArtifactsPlanStepFor<
      ["executed-notebook", "analysis-log", "environment-manifest"],
      ["executed-notebook", "analysis-log", "environment-manifest"]
    >
  | CollectArtifactsPlanStepFor<
      [
        "executed-notebook",
        "summary-table",
        "analysis-log",
        "environment-manifest",
      ],
      [
        "executed-notebook",
        "summary-table",
        "analysis-log",
        "environment-manifest",
      ]
    >
  | CollectArtifactsPlanStepFor<
      [
        "executed-notebook",
        "figures",
        "analysis-log",
        "environment-manifest",
      ],
      [
        "executed-notebook",
        "figure",
        "analysis-log",
        "environment-manifest",
      ]
    >
  | CollectArtifactsPlanStepFor<
      [
        "executed-notebook",
        "summary-table",
        "figures",
        "analysis-log",
        "environment-manifest",
      ],
      [
        "executed-notebook",
        "summary-table",
        "figure",
        "analysis-log",
        "environment-manifest",
      ]
    >;

export type DatasetAnalysisPlanStep =
  | DatasetInspectionPlanStep
  | PrepareAnalysisPlanStep
  | ExecuteAnalysisPlanStep
  | CollectArtifactsPlanStep;

export const DATASET_ANALYSIS_STEP_SEQUENCE = [
  {
    key: "inspect-dataset",
    type: "dataset-inspection",
    riskLevel: "low",
  },
  {
    key: "prepare-analysis",
    type: "prepare-analysis",
    riskLevel: "medium",
  },
  {
    key: "execute-analysis",
    type: "python-data-analysis",
    riskLevel: "high",
  },
  {
    key: "collect-artifacts",
    type: "collect-artifacts",
    riskLevel: "low",
  },
] as const;

/** Exhaustive bridge from plan semantics to persisted runtime artifact types. */
export const DATASET_ANALYSIS_RUNTIME_ARTIFACT_REQUIREMENTS = {
  "dataset-profile": [],
  "analysis-code": [],
  "executed-notebook": ["notebook-executed"],
  "summary-table": ["dataset"],
  figures: ["figure"],
  "analysis-log": ["stdout", "stderr", "log"],
  "environment-manifest": ["environment"],
} as const satisfies Record<
  DatasetAnalysisExpectedOutput,
  readonly DatasetAnalysisRuntimeArtifactType[]
>;

/**
 * Strict, ordered plan accepted for dataset-analysis workflows. The tuple
 * prevents a model response from omitting, reordering, or inventing handlers.
 */
interface DatasetAnalysisPlanSpecBase {
  schemaVersion: "1";
  workflowType: "dataset-analysis";
  goal: string;
  datasetSourceId: string;
  datasetContentHash: string;
  assumptions: string[];
  questionsForUser: string[];
}

type DatasetAnalysisPlanSpecFor<
  Outputs extends DatasetAnalysisExecutionExpectedOutputs,
  Artifacts extends DatasetAnalysisExecutionExpectedArtifacts,
> = DatasetAnalysisPlanSpecBase & {
  steps: [
    DatasetInspectionPlanStep,
    PrepareAnalysisPlanStep,
    ExecuteAnalysisPlanStepFor<Outputs, Artifacts>,
    CollectArtifactsPlanStepFor<Outputs, Artifacts>,
  ];
};

export type DatasetAnalysisPlanSpec =
  | DatasetAnalysisPlanSpecFor<
      ["executed-notebook", "analysis-log", "environment-manifest"],
      ["executed-notebook", "analysis-log", "environment-manifest"]
    >
  | DatasetAnalysisPlanSpecFor<
      [
        "executed-notebook",
        "summary-table",
        "analysis-log",
        "environment-manifest",
      ],
      [
        "executed-notebook",
        "summary-table",
        "analysis-log",
        "environment-manifest",
      ]
    >
  | DatasetAnalysisPlanSpecFor<
      [
        "executed-notebook",
        "figures",
        "analysis-log",
        "environment-manifest",
      ],
      [
        "executed-notebook",
        "figure",
        "analysis-log",
        "environment-manifest",
      ]
    >
  | DatasetAnalysisPlanSpecFor<
      [
        "executed-notebook",
        "summary-table",
        "figures",
        "analysis-log",
        "environment-manifest",
      ],
      [
        "executed-notebook",
        "summary-table",
        "figure",
        "analysis-log",
        "environment-manifest",
      ]
    >;

export interface ResearchWorkflowMaterializedStep {
  id: string;
  key: string;
  orderIndex: number;
  type: ResearchWorkflowTaskType;
  objective: string;
  status: ResearchWorkflowTaskStatus;
  retryCount: number;
  startedAt: string | null;
  completedAt: string | null;
  outputSummary: string | null;
}

export type DatasetAnalysisMaterializedStep<
  Key extends DatasetAnalysisStepKey,
  Type extends DatasetAnalysisTaskType,
  OrderIndex extends number,
> = Omit<ResearchWorkflowMaterializedStep, "key" | "type" | "orderIndex"> & {
  key: Key;
  type: Type;
  orderIndex: OrderIndex;
};

export interface DatasetAnalysisWorkflowPlan
  extends Omit<LiteratureResearchWorkflowPlan, "spec" | "steps"> {
  spec: DatasetAnalysisPlanSpec;
  steps: [
    DatasetAnalysisMaterializedStep<
      "inspect-dataset",
      "dataset-inspection",
      0
    >,
    DatasetAnalysisMaterializedStep<"prepare-analysis", "prepare-analysis", 1>,
    DatasetAnalysisMaterializedStep<
      "execute-analysis",
      "python-data-analysis",
      2
    >,
    DatasetAnalysisMaterializedStep<
      "collect-artifacts",
      "collect-artifacts",
      3
    >,
  ];
}

type CompletedDatasetAnalysisMaterializedStep<
  Key extends DatasetAnalysisStepKey,
  Type extends DatasetAnalysisTaskType,
  OrderIndex extends number,
> = DatasetAnalysisMaterializedStep<Key, Type, OrderIndex> & {
  status: "completed";
};

export type CompletedDatasetAnalysisWorkflowPlan = Omit<
  DatasetAnalysisWorkflowPlan,
  "status" | "steps"
> & {
  status: "approved";
  steps: [
    CompletedDatasetAnalysisMaterializedStep<
      "inspect-dataset",
      "dataset-inspection",
      0
    >,
    CompletedDatasetAnalysisMaterializedStep<
      "prepare-analysis",
      "prepare-analysis",
      1
    >,
    CompletedDatasetAnalysisMaterializedStep<
      "execute-analysis",
      "python-data-analysis",
      2
    >,
    CompletedDatasetAnalysisMaterializedStep<
      "collect-artifacts",
      "collect-artifacts",
      3
    >,
  ];
};

export interface LiteratureResearchWorkflowPlan {
  id: string;
  workflowId: string;
  version: number;
  status: ResearchWorkflowPlanStatus;
  planSha256: string;
  /** Planner identity, for example a deterministic template or model gateway. */
  generator?: string;
  /** Versioned prompt/template identifier used to create this plan. */
  promptVersion?: string | null;
  /** Exact model identifier when a remote model generated the plan. */
  model?: string | null;
  spec: ResearchWorkflowPlanSpec;
  steps: ResearchWorkflowMaterializedStep[];
  createdAt: string;
  approvedAt: string | null;
}

export type ResearchWorkflowPlan =
  | LiteratureResearchWorkflowPlan
  | DatasetAnalysisWorkflowPlan;

interface WorkflowPendingApprovalBase {
  id: string;
  workflowId: string;
  planId: string;
  status: "waiting";
  subjectType: string;
  subjectId: string;
  action: string;
  payloadSha256: string;
  riskLevel: WorkflowRiskLevel;
  reason: string;
  affectedResources: string[];
  createdAt: string;
  decidedAt: string | null;
}

export interface WorkflowPlanPendingApproval
  extends WorkflowPendingApprovalBase {
  taskId: null;
  kind: "plan";
  subjectType: "plan";
  workflowType?: "literature-synthesis";
  datasetSourceId?: never;
  datasetContentHash?: never;
}

export interface DatasetWorkflowPlanPendingApproval
  extends WorkflowPendingApprovalBase {
  taskId: null;
  kind: "plan";
  subjectType: "plan";
  action: "approve-plan";
  riskLevel: "medium";
  approvalSchemaVersion: "workflow-plan-approval-v3";
  workflowType: "dataset-analysis";
  planVersion: number;
  planSha256: string;
  expectedWorkflowRevision: number;
  datasetSourceId: string;
  datasetContentHash: string;
}

export interface WorkflowAnalysisExecutionPendingApproval
  extends WorkflowPendingApprovalBase {
  taskId: string;
  kind: "analysis-execution";
  subjectType: "analysis-intent";
  action: "execute-python-data-analysis";
  riskLevel: "high";
  approvalSchemaVersion: "analysis-intent-v2" | "analysis-intent-v3";
  expectedWorkflowRevision: number;
  analysisIntentId: string;
  planStepId: "execute-analysis";
  datasetSourceId: string;
  datasetContentHash: string;
  expectedOutputs: DatasetAnalysisExecutionExpectedOutputs;
  timeoutSeconds: number;
  code: string;
  codeDiff: string | null;
}

export type WorkflowPendingApproval =
  | WorkflowPlanPendingApproval
  | DatasetWorkflowPlanPendingApproval
  | WorkflowAnalysisExecutionPendingApproval;

/** Future policy approvals stay distinct from plan confirmation. */
export type WorkflowApprovalKind = "plan" | "remote-data" | "analysis-execution";

export type ClaimSupportStatus =
  | "supported"
  | "partially-supported"
  | "contradicted"
  | "insufficient-evidence"
  | "pending-review"
  | "not-applicable";

export interface WorkflowEvidenceRelationship {
  evidenceId: string;
  sourceId: string;
  sourceTitle: string | null;
  sourceContentHash: string | null;
  sourcePageManifestHash: string | null;
  pageIndex: number;
  pageLabel: string | null;
  text: string;
  bbox: EvidenceBoundingBox | null;
  coordinateSpace: "normalized-rotated-top-left-v1";
  quoteHash: string;
  extractionMethod: string;
  confidence: number;
  verified: boolean;
  relationship: "supporting" | "contradicting";
}

export interface WorkflowClaim {
  id: string;
  statement: string;
  supportStatus: ClaimSupportStatus;
  confidence: number;
  evidence: WorkflowEvidenceRelationship[];
}

export interface ResearchWorkflowResult {
  answerId: string;
  summary: string;
  generator: string;
  model: string | null;
  promptVersion: string | null;
  integrityStatus: "verified-frozen-v2" | "unfrozen";
  claims: WorkflowClaim[];
  unresolvedQuestions: string[];
}

export type LiteratureWorkflowReviewVerdict =
  | "passed"
  | "revision-required"
  | "blocked"
  | "failed";

export type WorkflowReviewVerdict =
  | LiteratureWorkflowReviewVerdict
  | "passed-with-warnings";

export interface WorkflowReviewCheck {
  code: string;
  status: "passed" | "failed";
  message: string;
  claimId: string | null;
  evidenceId: string | null;
}

export interface WorkflowClaimReviewResult {
  claimId: string;
  status: ClaimSupportStatus;
  evidenceIds: string[];
  relationships: Array<"supporting" | "contradicting">;
}

export interface WorkflowDeterministicReviewResult {
  schemaVersion: "1" | "2";
  verdict: LiteratureWorkflowReviewVerdict;
  checks: WorkflowReviewCheck[];
  claimResults: WorkflowClaimReviewResult[];
  requiredRevisions: string[];
  resultSnapshotSha256: string | null;
  resultSnapshot: ResearchWorkflowResult | null;
}

export type LegacyWorkflowDeterministicReviewResult = Omit<
  WorkflowDeterministicReviewResult,
  "schemaVersion" | "resultSnapshotSha256" | "resultSnapshot"
> & {
  schemaVersion: "1";
  resultSnapshotSha256: null;
  resultSnapshot: null;
};

export type FrozenWorkflowDeterministicReviewResult = Omit<
  WorkflowDeterministicReviewResult,
  "schemaVersion" | "resultSnapshotSha256" | "resultSnapshot"
> & {
  schemaVersion: "2";
  resultSnapshotSha256: string;
  resultSnapshot: ResearchWorkflowResult;
};

interface ResearchWorkflowReviewBase<
  Verdict extends LiteratureWorkflowReviewVerdict,
> {
  id: string;
  verdict: Verdict;
  inputSha256: string;
  createdAt: string;
}

type ReviewResultWithVerdict<
  Result extends WorkflowDeterministicReviewResult,
  Verdict extends LiteratureWorkflowReviewVerdict,
> = Omit<Result, "verdict"> & { verdict: Verdict };

export type ResearchWorkflowReview = {
  [Verdict in LiteratureWorkflowReviewVerdict]: ResearchWorkflowReviewBase<Verdict> &
    (
      | {
          reviewType: "deterministic-claims-v1";
          result: ReviewResultWithVerdict<
            LegacyWorkflowDeterministicReviewResult,
            Verdict
          >;
        }
      | {
          reviewType: "deterministic-claims-v2";
          result: ReviewResultWithVerdict<
            FrozenWorkflowDeterministicReviewResult,
            Verdict
          >;
        }
    );
}[LiteratureWorkflowReviewVerdict];

interface ResearchWorkflowSnapshotBase {
  result: ResearchWorkflowResult | null;
  allowedActions: ResearchWorkflowAllowedAction[];
  eventCursor: number;
}

export interface LiteratureResearchWorkflowSnapshot
  extends ResearchWorkflowSnapshotBase {
  workflow: LiteratureResearchWorkflow;
  plan: LiteratureResearchWorkflowPlan | null;
  pendingApprovals: WorkflowPlanPendingApproval[];
  latestReview: ResearchWorkflowReview | null;
  allowedActions: LiteratureResearchWorkflowAllowedAction[];
  datasetProfile?: null;
  analysisIntent?: null;
  analysisRun?: null;
  reviewWarningAcceptance?: null;
}

interface ActiveDatasetAnalysisWorkflowSnapshot
  extends Omit<ResearchWorkflowSnapshotBase, "result"> {
  workflow: Omit<DatasetAnalysisWorkflow, "status"> & {
    status: Exclude<ResearchWorkflowStatus, "completed">;
  };
  plan: DatasetAnalysisWorkflowPlan | null;
  pendingApprovals: Array<
    | DatasetWorkflowPlanPendingApproval
    | WorkflowAnalysisExecutionPendingApproval
  >;
  /** Dataset results are represented by the exact Intent, Run, and Review records. */
  result: null;
  latestReview: DatasetAnalysisReview | null;
  datasetProfile: DatasetProfile | null;
  analysisIntent: WorkflowAnalysisIntent | null;
  analysisRun: WorkflowAnalysisRun | null;
  reviewWarningAcceptance: DatasetReviewWarningAcceptance | null;
}

type CompletedDatasetAnalysisWorkflowState = Omit<
  DatasetAnalysisWorkflow,
  | "status"
  | "currentStepId"
  | "blockingReason"
  | "cancelRequestedAt"
  | "completedAt"
> & {
  status: "completed";
  currentStepId: null;
  blockingReason: null;
  cancelRequestedAt: null;
  completedAt: string;
};

interface CompletedDatasetAnalysisWorkflowSnapshotBase {
  workflow: CompletedDatasetAnalysisWorkflowState;
  plan: CompletedDatasetAnalysisWorkflowPlan;
  pendingApprovals: [];
  result: null;
  datasetProfile: DatasetProfile;
  analysisIntent: WorkflowAnalysisIntent & {
    status: "completed";
    decision: "approved";
  };
  analysisRun: CompletedWorkflowAnalysisRun;
  allowedActions: [];
  eventCursor: number;
}

type PassedDatasetAnalysisWorkflowSnapshot =
  CompletedDatasetAnalysisWorkflowSnapshotBase & {
    latestReview: Extract<DatasetAnalysisReview, { verdict: "passed" }>;
    reviewWarningAcceptance: null;
  };

type WarningAcceptedDatasetAnalysisWorkflowSnapshot =
  CompletedDatasetAnalysisWorkflowSnapshotBase & {
    latestReview: Extract<
      DatasetAnalysisReview,
      { verdict: "passed-with-warnings" }
    >;
    reviewWarningAcceptance: DatasetReviewWarningAcceptance;
  };

export type DatasetAnalysisWorkflowSnapshot =
  | ActiveDatasetAnalysisWorkflowSnapshot
  | PassedDatasetAnalysisWorkflowSnapshot
  | WarningAcceptedDatasetAnalysisWorkflowSnapshot;

export type ResearchWorkflowSnapshot =
  | LiteratureResearchWorkflowSnapshot
  | DatasetAnalysisWorkflowSnapshot;

export interface WorkflowCreatedEventData {
  workflowType: ResearchWorkflowType;
  goalSha256: string;
  /** Optional while reading event logs created before model-assisted v2. */
  generationMode?: ResearchGenerationMode;
}

interface WorkflowRemoteDataApprovalEventBase {
  provider: "openai-compatible";
  endpointHost: string;
  endpointIdentity: string;
  model: string | null;
}

export interface LiteratureRemoteDataApprovalEventData
  extends WorkflowRemoteDataApprovalEventBase {
  dataCategories: ["user-goal"];
}

export interface DatasetRemoteDataApprovalEventData
  extends WorkflowRemoteDataApprovalEventBase {
  dataCategories: ["user-goal", "dataset-profile"];
}

export type WorkflowRemoteDataApprovalEventData =
  | LiteratureRemoteDataApprovalEventData
  | DatasetRemoteDataApprovalEventData;

export interface WorkflowStatusChangedEventData {
  previousStatus: ResearchWorkflowStatus;
  status: ResearchWorkflowStatus;
  reasonCode: string | null;
}

export interface WorkflowPlanEventData {
  planId: string;
  version: number;
  planSha256: string;
}

export interface WorkflowApprovalEventData {
  approvalId: string;
  subjectType: string;
  subjectId: string;
  action: string;
  payloadSha256: string;
  /** Added by v2 approval envelopes; absent on frozen v1 events. */
  riskLevel?: string | null;
  reason?: string | null;
  affectedResources?: string[] | null;
  approvalSchemaVersion?: string | null;
}

export interface WorkflowTaskEventData {
  taskId: string;
  stepKey: string;
  orderIndex: number;
  status: ResearchWorkflowTaskStatus;
  outputCount: number | null;
  errorCode: string | null;
}

export interface WorkflowJobEventData {
  jobId: string;
  kind: string;
  attempt: number;
  errorCode: string | null;
}

export interface WorkflowReviewEventData {
  reviewId: string;
  verdict: WorkflowReviewVerdict;
  claimCount: number | null;
}

export interface WorkflowCancelEventData {
  requested: boolean;
}

export interface AnalysisIntentCreatedEventData {
  analysisIntentId: string;
  taskId: string;
  jobId: string;
  planStepId: "execute-analysis";
  datasetSourceId: string;
  datasetContentHash: string;
  payloadSha256: string;
  repairAttempt: 0 | 1 | 2;
}

export interface AnalysisApprovalEventData {
  approvalId: string;
  analysisIntentId: string;
  taskId: string;
  jobId: string | null;
  payloadSha256: string;
  approvalSchemaVersion: "analysis-intent-v2" | "analysis-intent-v3";
  expectedWorkflowRevision: number;
}

interface AnalysisRunEventDataBase {
  analysisIntentId: string;
  runId: string;
  taskId: string;
  jobId: string;
  payloadSha256: string;
}

export interface AnalysisRunStartedEventData extends AnalysisRunEventDataBase {
  environmentHash: null;
  artifactCount: null;
  errorCode: null;
}

export interface AnalysisRunCompletedEventData extends AnalysisRunEventDataBase {
  environmentHash: string;
  artifactCount: number;
  errorCode: null;
}

export interface AnalysisRunFailedEventData extends AnalysisRunEventDataBase {
  environmentHash: string | null;
  artifactCount: number | null;
  errorCode: string;
}

export type AnalysisRunEventData =
  | AnalysisRunStartedEventData
  | AnalysisRunCompletedEventData
  | AnalysisRunFailedEventData;

export interface AnalysisRunProgressEventData {
  analysisIntentId: string;
  runId: string;
  taskId: string;
  jobId: string;
  stage: "preparing-input" | "executing-runtime" | "collecting-artifacts";
  elapsedSeconds: number;
}

export interface AnalysisArtifactCreatedEventData {
  analysisIntentId: string;
  runId: string;
  taskId: string;
  jobId: string;
  artifactId: string;
  artifactType: DatasetAnalysisRuntimeArtifactType;
  contentHash: string;
  path: string;
}

export interface DatasetReviewWarningsAcceptedEventData {
  reviewId: string;
  reviewInputSha256: string;
  expectedWorkflowRevision: number;
  decision: "accepted";
}

export type WorkflowEventData =
  | WorkflowCreatedEventData
  | WorkflowRemoteDataApprovalEventData
  | WorkflowStatusChangedEventData
  | WorkflowPlanEventData
  | WorkflowApprovalEventData
  | WorkflowTaskEventData
  | WorkflowJobEventData
  | WorkflowReviewEventData
  | WorkflowCancelEventData
  | AnalysisIntentCreatedEventData
  | AnalysisApprovalEventData
  | AnalysisRunEventData
  | AnalysisRunProgressEventData
  | AnalysisArtifactCreatedEventData
  | DatasetReviewWarningsAcceptedEventData;

interface WorkflowEventEnvelope<Type extends string, Data extends WorkflowEventData> {
  id: string;
  sequence: number;
  type: Type;
  taskId: string | null;
  jobId: string | null;
  data: Data;
  createdAt: string;
}

export type WorkflowEvent =
  | WorkflowEventEnvelope<"workflow.created", WorkflowCreatedEventData>
  | WorkflowEventEnvelope<
      "remote-data.approved",
      WorkflowRemoteDataApprovalEventData
    >
  | WorkflowEventEnvelope<
      "workflow.status-changed",
      WorkflowStatusChangedEventData
    >
  | WorkflowEventEnvelope<
      "plan.generated" | "plan.approved",
      WorkflowPlanEventData
    >
  | WorkflowEventEnvelope<"approval.requested", WorkflowApprovalEventData>
  | WorkflowEventEnvelope<
      "step.queued" | "step.started" | "step.completed" | "step.failed",
      WorkflowTaskEventData
    >
  | WorkflowEventEnvelope<
      "job.failed" | "job.retried",
      WorkflowJobEventData
    >
  | WorkflowEventEnvelope<"review.completed", WorkflowReviewEventData>
  | WorkflowEventEnvelope<"workflow.cancel-requested", WorkflowCancelEventData>
  | WorkflowEventEnvelope<
      "analysis.intent-created",
      AnalysisIntentCreatedEventData
    >
  | WorkflowEventEnvelope<
      "analysis.approval-requested" | "analysis.approved" | "analysis.rejected",
      AnalysisApprovalEventData
    >
  | WorkflowEventEnvelope<
      "analysis.run-started",
      AnalysisRunStartedEventData
    >
  | WorkflowEventEnvelope<
      "analysis.run-completed",
      AnalysisRunCompletedEventData
    >
  | WorkflowEventEnvelope<"analysis.run-failed", AnalysisRunFailedEventData>
  | WorkflowEventEnvelope<
      "analysis.run-progress",
      AnalysisRunProgressEventData
    >
  | WorkflowEventEnvelope<
      "artifact.created",
      AnalysisArtifactCreatedEventData
    >
  | WorkflowEventEnvelope<
      "analysis.review-warnings-accepted",
      DatasetReviewWarningsAcceptedEventData
    >;

export interface WorkflowEventsPage {
  events: WorkflowEvent[];
  nextAfter: number;
  hasMore: boolean;
}

export type ResearchSourceKind =
  | "paper"
  | "pdf"
  | "dataset"
  | "webpage"
  | "code-repository"
  | "note";

export interface ResearchProject {
  id: string;
  title: string;
  description: string;
  projectPath: string;
  researchDomain: string | null;
  executionMode: "safe" | "trusted-local";
  createdAt: string;
  updatedAt: string;
}

export interface ResearchSource {
  id: string;
  projectId: string;
  title: string;
  sourceKind: ResearchSourceKind;
  authors: string[];
  doi: string | null;
  arxivId: string | null;
  localPath: string;
  publicationDate: string | null;
  ingestionStatus: "pending" | "processing" | "ready" | "failed";
  contentHash: string;
  pageCount: number | null;
  createdAt: string;
}

export interface EvidenceBoundingBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface EvidenceSpan {
  id: string;
  sourceId: string;
  pageIndex: number;
  pageLabel: string | null;
  text: string;
  bbox: EvidenceBoundingBox | null;
  coordinateSpace: "normalized-rotated-top-left-v1";
  quoteHash: string;
  extractionMethod: string;
  confidence: number;
  verified: boolean;
}

export interface ResearchClaim {
  id: string;
  statement: string;
  claimType: "answer" | "finding" | "limitation" | "contradiction";
  confidence: number;
  reviewStatus: "unreviewed" | "verified" | "rejected";
  evidence: EvidenceSpan[];
}

export interface ResearchAnswer {
  id: string;
  projectId: string;
  question: string;
  answer: string;
  claims: ResearchClaim[];
  unresolvedQuestions: string[];
  generator: string;
  model: string | null;
  promptVersion: string | null;
  metadata: Record<string, unknown>;
  createdAt: string;
}

export interface ScienceCoreModelDestination {
  provider: "openai-compatible";
  endpointHost: string;
  endpointIdentity: string;
  model: string;
}

export interface ScienceCoreHealth {
  status: "ok" | "degraded";
  version: string;
  database: "ok" | "error";
  paperQa: "available" | "unavailable";
  modelGateway: "configured" | "unconfigured";
  modelDestination: ScienceCoreModelDestination | null;
  runtime: "ready" | "unavailable";
}

export type DatasetColumnInferredType =
  | "boolean"
  | "integer"
  | "number"
  | "datetime"
  | "categorical"
  | "string"
  | "empty"
  | "mixed";

export interface DatasetNumericRange {
  minimum: number | null;
  maximum: number | null;
}

export interface DatasetLowCardinalitySummary {
  /** Values are bounded, sanitized display strings rather than raw records. */
  values: string[];
  truncated: boolean;
}

export interface DatasetColumnProfile {
  index: number;
  name: string;
  inferredType: DatasetColumnInferredType;
  missingCount: number;
  uniqueCount: number;
  numericRange: DatasetNumericRange | null;
  lowCardinality: DatasetLowCardinalitySummary | null;
  potentialDate: boolean;
  potentialId: boolean;
  mixedType: boolean;
}

export interface DatasetInspectionWarning {
  code:
    | "encoding-fallback"
    | "duplicate-column-name"
    | "mixed-column-type"
    | "malformed-row"
    | "sample-limited"
    | "other";
  message: string;
  columnName: string | null;
}

export interface DatasetSamplingRecord {
  method: "head-and-reservoir-v1";
  rowsRead: number;
  rowsProfiled: number;
  maxSampleRows: number;
  seed: number;
}

/** Structured, persisted inspector result; never contains complete source rows. */
export interface DatasetProfile {
  schemaVersion: "1";
  datasetSourceId: string;
  filename: string;
  contentHash: string;
  fileSizeBytes: number;
  encoding: string;
  delimiter: string;
  rowCount: number;
  columnCount: number;
  columns: DatasetColumnProfile[];
  sampling: DatasetSamplingRecord;
  warnings: DatasetInspectionWarning[];
}

export type AnalysisIntentStatus =
  | "waiting-approval"
  | "approved"
  | "rejected"
  | "executing"
  | "completed"
  | "failed";

export interface AnalysisIntent {
  id: string;
  taskId: string;
  projectId: string;
  datasetSourceId: string;
  /** Required for new intents; optional only for legacy API snapshots. */
  datasetContentHash?: string;
  objective: string;
  code: string;
  payloadSha256: string;
  riskLevel: "high";
  affectedResources: string[];
  status: AnalysisIntentStatus;
  decision: "approved" | "rejected" | null;
  workflowId?: string | null;
  planStepId?: string | null;
  previousIntentId?: string | null;
  expectedOutputs?: DatasetAnalysisExpectedOutput[] | null;
  timeoutSeconds?: number | null;
  repairAttempt?: 0 | 1 | 2 | null;
  errorSummary?: AnalysisErrorSummary | null;
  codeDiff?: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface AnalysisErrorSummary {
  schemaVersion: "1";
  category:
    | "policy"
    | "runtime"
    | "timeout"
    | "input-integrity"
    | "artifact-integrity"
    | "unknown";
  code: string;
  userMessage: string;
  stderrExcerpt: string | null;
  retryable: boolean;
}

interface WorkflowAnalysisIntentBase
  extends Omit<
    AnalysisIntent,
    | "datasetContentHash"
    | "workflowId"
    | "planStepId"
    | "previousIntentId"
    | "expectedOutputs"
    | "timeoutSeconds"
    | "repairAttempt"
    | "errorSummary"
    | "codeDiff"
    | "status"
    | "decision"
  > {
  datasetContentHash: string;
  workflowId: string;
  planStepId: "execute-analysis";
  taskId: string;
  expectedOutputs: DatasetAnalysisExecutionExpectedOutputs;
  timeoutSeconds: number;
}

type NonFailedWorkflowAnalysisIntentLifecycle =
  | { status: "waiting-approval"; decision: null }
  | { status: "rejected"; decision: "rejected" }
  | {
      status: "approved" | "executing" | "completed";
      decision: "approved";
    };

type FailedWorkflowAnalysisIntentLifecycle = {
  status: "failed";
  decision: "approved";
};

type WorkflowAnalysisIntentLifecycle =
  | NonFailedWorkflowAnalysisIntentLifecycle
  | FailedWorkflowAnalysisIntentLifecycle;

interface InitialWorkflowAnalysisIntentLineage {
  previousIntentId: null;
  repairAttempt: 0;
  codeDiff: null;
}

export type InitialWorkflowAnalysisIntent = WorkflowAnalysisIntentBase &
  InitialWorkflowAnalysisIntentLineage &
  (
    | (NonFailedWorkflowAnalysisIntentLifecycle & { errorSummary: null })
    | (FailedWorkflowAnalysisIntentLifecycle & {
        errorSummary: AnalysisErrorSummary;
      })
  );

interface RepairWorkflowAnalysisIntentLineage {
  previousIntentId: string;
  repairAttempt: 1 | 2;
  errorSummary: AnalysisErrorSummary;
  codeDiff: string;
}

export type RepairWorkflowAnalysisIntent = WorkflowAnalysisIntentBase &
  RepairWorkflowAnalysisIntentLineage &
  WorkflowAnalysisIntentLifecycle;

/** Fully bound AnalysisIntent created by a dataset workflow step. */
export type WorkflowAnalysisIntent =
  | InitialWorkflowAnalysisIntent
  | RepairWorkflowAnalysisIntent;

export interface AnalysisArtifact {
  id: string;
  artifactType: string;
  path: string;
  mimeType: string;
  contentHash: string;
  sizeBytes: number;
  createdAt: string;
}

export type AnalysisRunStatus = "pending" | "running" | "completed" | "failed";

export interface AnalysisRun {
  id: string;
  intentId: string;
  taskId: string;
  projectId: string;
  datasetSourceId: string;
  objective: string;
  code: string;
  payloadSha256: string;
  status: AnalysisRunStatus;
  environmentHash: string | null;
  inputArtifacts: string[];
  outputArtifacts: string[];
  stdout: string;
  stderr: string;
  log: string;
  logs: string;
  artifacts: AnalysisArtifact[];
  createdAt: string;
  finishedAt: string | null;
  error: string | null;
}

export interface WorkflowAnalysisArtifact
  extends Omit<AnalysisArtifact, "artifactType"> {
  artifactType: DatasetAnalysisRuntimeArtifactType;
}

interface WorkflowAnalysisRunBase
  extends Omit<
    AnalysisRun,
    | "status"
    | "environmentHash"
    | "finishedAt"
    | "error"
    | "artifacts"
    | "inputArtifacts"
  > {
  artifacts: WorkflowAnalysisArtifact[];
  inputArtifacts: [string];
}

export interface ActiveWorkflowAnalysisRun extends WorkflowAnalysisRunBase {
  status: "pending" | "running";
  environmentHash: string | null;
  finishedAt: null;
  error: string | null;
}

export interface CompletedWorkflowAnalysisRun
  extends Omit<WorkflowAnalysisRunBase, "artifacts" | "outputArtifacts"> {
  status: "completed";
  environmentHash: string;
  finishedAt: string;
  error: null;
  outputArtifacts: [string, string, string, string, string, ...string[]];
  artifacts: [
    WorkflowAnalysisArtifact,
    WorkflowAnalysisArtifact,
    WorkflowAnalysisArtifact,
    WorkflowAnalysisArtifact,
    WorkflowAnalysisArtifact,
    ...WorkflowAnalysisArtifact[],
  ];
}

export interface FailedWorkflowAnalysisRun extends WorkflowAnalysisRunBase {
  status: "failed";
  environmentHash: string | null;
  finishedAt: string;
  error: string;
}

/** Run whose intentId is resolved through RunRecord.analysis_intent_id. */
export type WorkflowAnalysisRun =
  | ActiveWorkflowAnalysisRun
  | CompletedWorkflowAnalysisRun
  | FailedWorkflowAnalysisRun;

export type DatasetAnalysisReviewVerdict =
  | "passed"
  | "passed-with-warnings"
  | "revision-required"
  | "blocked"
  | "failed";

export interface DatasetAnalysisReviewCheck {
  code: string;
  status: "passed" | "warning" | "failed";
  message: string;
  artifactId: string | null;
}

export interface DatasetAnalysisReviewIssue {
  code: string;
  message: string;
  artifactId: string | null;
}

export interface DatasetAnalysisReviewResult {
  schemaVersion: "1";
  verdict: DatasetAnalysisReviewVerdict;
  checks: DatasetAnalysisReviewCheck[];
  artifactIssues: DatasetAnalysisReviewIssue[];
  numericIssues: DatasetAnalysisReviewIssue[];
  methodWarnings: DatasetAnalysisReviewIssue[];
  requiredRevisions: string[];
  runId: string;
  analysisIntentId: string;
  inputDatasetContentHash: string;
}

interface DatasetAnalysisReviewBase<
  Verdict extends DatasetAnalysisReviewVerdict,
> {
  id: string;
  reviewType: "deterministic-analysis-v1";
  verdict: Verdict;
  inputSha256: string;
  createdAt: string;
}

export type DatasetAnalysisReview = {
  [Verdict in DatasetAnalysisReviewVerdict]: DatasetAnalysisReviewBase<Verdict> & {
    result: Omit<DatasetAnalysisReviewResult, "verdict"> & {
      verdict: Verdict;
    };
  };
}[DatasetAnalysisReviewVerdict];

/** Durable acceptance required before a warning-bearing review may complete. */
export interface DatasetReviewWarningAcceptance {
  eventId: string;
  reviewId: string;
  reviewInputSha256: string;
  expectedWorkflowRevision: number;
  decision: "accepted";
  acceptedAt: string;
}
