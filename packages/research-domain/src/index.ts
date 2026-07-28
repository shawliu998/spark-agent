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
  | "routing"
  | "waiting-clarification"
  | "planning"
  | "waiting-plan-approval"
  | "running"
  | "reviewing"
  | "completed"
  | "unsupported"
  | "blocked"
  | "failed"
  | "cancelled";

/** Actions science-core currently permits for a workflow snapshot. */
export type ResearchWorkflowAllowedAction =
  | "approve-plan"
  | "approve-analysis"
  | "reject-analysis"
  | "approve-agent-decision"
  | "reject-agent-decision"
  | "respond-interaction"
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

/** Strict autonomous-router decision vocabulary. */
export type ResearchIntent =
  | "literature-synthesis"
  | "dataset-analysis"
  | "mixed-research"
  | "clarification-required"
  | "unsupported";

export type ProposedResearchWorkflowType =
  | "literature-synthesis"
  | "dataset-analysis"
  | "mixed-research";

export interface IntentDecision {
  id: string;
  workflowId: string;
  intent: ResearchIntent;
  confidence: number;
  reasoningSummary: string;
  selectedSourceIds: string[];
  missingInputs: string[];
  proposedWorkflowType: ProposedResearchWorkflowType | null;
  promptVersion: string;
  inputSha256: string;
  outputSha256: string;
  createdAt: string;
}

export interface CreateAgentRunInput {
  goal: string;
  sourceIds: string[];
  mode: "autonomous";
  remoteDataApproved?: boolean;
}

export type InteractionRequestType =
  | "single-choice"
  | "multi-choice"
  | "text"
  | "number"
  | "boolean"
  | "column-selection"
  | "method-confirmation"
  | "assumption-confirmation";

export type InteractionRequestStatus =
  | "pending"
  | "answered"
  | "superseded"
  | "cancelled";

export type InteractionResponseValue = string | number | boolean | string[];

export interface InteractionOption {
  value: string;
  label: string;
  description?: string | null;
}

export interface InteractionUserResponse {
  id: string;
  interactionId: string;
  revision: number;
  response: InteractionResponseValue;
  responseSha256: string;
  createdAt: string;
}

/** Durable science-core request; it is never a skippable runtime prompt. */
export interface InteractionRequest {
  id: string;
  workflowId: string;
  stepId: string | null;
  requestType: InteractionRequestType;
  question: string;
  options: InteractionOption[];
  required: boolean;
  status: InteractionRequestStatus;
  responseSchema: Record<string, unknown>;
  workflowRevision: number;
  latestResponse: InteractionUserResponse | null;
  createdAt: string;
  answeredAt: string | null;
}

export interface RespondToInteractionInput {
  response: InteractionResponseValue;
  expectedWorkflowRevision: number;
}

export interface ResolveAgentDecisionInput {
  decision: "approved" | "rejected";
  decisionOutputSha256: string;
  expectedWorkflowRevision: number;
}

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
  details?: Record<string, unknown>;
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
  /** Present for workflows created through the autonomous agent entry point. */
  mode?: "autonomous" | "advanced";
  sourceIds?: string[];
}

export interface AgentResearchWorkflow
  extends Omit<ResearchWorkflowBase, "blockingReason"> {
  mode: "autonomous";
  sourceIds: string[];
  workflowType: ProposedResearchWorkflowType | null;
  generationMode: ResearchGenerationMode;
  status: ResearchWorkflowStatus;
  statusReason?: {
    code: string;
    userMessage: string;
  } | null;
  /** Legacy workflow snapshots use blockingReason; autonomous snapshots use statusReason. */
  blockingReason?: null;
  datasetSourceId?: null;
  datasetContentHash?: null;
}

export type PendingAgentResearchWorkflow = Omit<
  AgentResearchWorkflow,
  "workflowType" | "status"
> & {
  workflowType: null;
  status: "routing" | "waiting-clarification" | "unsupported";
};

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
  | DatasetAnalysisWorkflow
  | AgentResearchWorkflow;

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
  | DatasetAnalysisTaskType
  | "paper-discovery";

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
  /** Keeps paper-discovery recognition explicit and exhaustive at the UI boundary. */
  planType?: never;
  goal: string;
  steps: ResearchWorkflowStepSpec[];
}

/** Strict public-search plan. It is not a literature-synthesis plan or source set. */
export interface PaperDiscoveryStepInput {
  schemaVersion: "1";
  discoverySpecId: string;
  discoverySpecRevision: number;
  discoverySpecSha256: string;
  queryId: string;
  query: string;
  provider: DiscoveryProvider;
  yearFrom: number | null;
  yearTo: number | null;
  sort: DiscoverySort;
  maxResultsPerProvider: number;
  derivedMaximumResults: number;
  stopPolicy: DiscoveryStopPolicy;
  downloadOpenAccessPdfs: false;
  maxPdfDownloads: 0;
}

export interface PaperDiscoveryPlanStep {
  key: string;
  orderIndex: number;
  objective: string;
  taskType: "paper-discovery";
  inputs: PaperDiscoveryStepInput;
  expectedOutputs: ["discovery-observation"];
  acceptanceCriteria: ["persist-structured-discovery-observation"];
  permissions: ["remote-paper-search"];
  riskLevel: "medium";
  timeoutSeconds: 120;
}

export interface PaperDiscoveryPlanSpec {
  schemaVersion: "1";
  planType: "paper-discovery";
  goal: string;
  discoverySpecId: string;
  discoverySpecRevision: number;
  discoverySpecSha256: string;
  steps: PaperDiscoveryPlanStep[];
}

export type WorkflowPlanSpec = ResearchWorkflowPlanSpec | PaperDiscoveryPlanSpec;

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
  planType?: never;
  workflowType: "dataset-analysis";
  goal: string;
  datasetSourceId: string;
  datasetContentHash: string;
  analysisSpecId: string | null;
  analysisSpecSha256: string | null;
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
  spec: WorkflowPlanSpec;
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

interface WorkflowAnalysisExecutionPendingApprovalBase
  extends WorkflowPendingApprovalBase {
  taskId: string;
  kind: "analysis-execution";
  subjectType: "analysis-intent";
  action: "execute-python-data-analysis";
  riskLevel: "high";
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

export type WorkflowAnalysisExecutionPendingApproval =
  | (WorkflowAnalysisExecutionPendingApprovalBase & {
      approvalSchemaVersion: "analysis-intent-v2" | "analysis-intent-v3";
      analysisSpecId: null;
      specSha256: null;
      datasetProfileSha256: null;
      compilerVersion: null;
      codeSha256: null;
      runtimePolicyId: null;
    })
  | (WorkflowAnalysisExecutionPendingApprovalBase & {
      approvalSchemaVersion: "analysis-intent-v4";
      analysisSpecId: string;
      specSha256: string;
      datasetProfileSha256: string;
      compilerVersion: string;
      codeSha256: string;
      runtimePolicyId: string;
    });

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

export type ReportDraftStatus = "draft" | "needs-review" | "reviewed";

export interface ReportDraftRecord {
  id: string;
  projectId: string;
  workflowId: string;
  schemaVersion: "1";
  revision: number;
  contentMarkdown: string;
  contentSha256: string;
  baseWorkflowSha256: string;
  baseResultSha256: string;
  baseEvidenceSha256: string;
  status: ReportDraftStatus;
  createdAt: string;
  updatedAt: string;
}

export interface CreateReportDraftInput {
  schemaVersion: "1";
}

export interface SaveReportDraftInput {
  expectedRevision: number;
  expectedContentSha256: string;
  contentMarkdown: string;
}

export interface ReportCitationRebaseInput {
  previousEvidenceId: string;
  previousQuoteHash: string;
  currentEvidenceId: string;
  currentQuoteHash: string;
}

export interface ReviewReportDraftInput {
  expectedRevision: number;
  expectedContentSha256: string;
  citationRebases: ReportCitationRebaseInput[];
}

export interface ExportReportDraftInput {
  expectedRevision: number;
  expectedContentSha256: string;
}

export interface ReportDraftExport {
  draftId: string;
  projectId: string;
  workflowId: string;
  revision: number;
  contentMarkdown: string;
  contentSha256: string;
  baseWorkflowSha256: string;
  baseResultSha256: string;
  baseEvidenceSha256: string;
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
  /** Optional only while reading snapshots emitted before AnalysisSpec v1. */
  analysisSpec?: WorkflowAnalysisSpec | null;
  analysisIntent: WorkflowAnalysisIntent | null;
  analysisRun: WorkflowAnalysisRun | null;
  /** Optional only while reading snapshots emitted before structured results v1. */
  structuredResult?: WorkflowStructuredAnalysisResult | null;
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
  analysisSpec?: WorkflowAnalysisSpec | null;
  analysisIntent: WorkflowAnalysisIntent & {
    status: "completed";
    decision: "approved";
  };
  analysisRun: CompletedWorkflowAnalysisRun;
  structuredResult?: WorkflowStructuredAnalysisResult | null;
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

export type AgentAction =
  | "continue"
  | "request-clarification"
  | "revise-analysis-spec"
  | "retry-step"
  | "complete"
  | "stop";

export type AgentLoopJsonScalar = string | number | boolean;
export type AgentLoopJsonValue =
  | AgentLoopJsonScalar
  | null
  | AgentLoopJsonValue[]
  | { [key: string]: AgentLoopJsonValue };

export type ObservationSourceType =
  | "dataset-profile"
  | "analysis-spec"
  | "preflight"
  | "run"
  | "structured-result"
  | "artifact"
  | "review"
  | "workflow";

export interface ObservationFact {
  code: string;
  statement: string;
  value: AgentLoopJsonValue;
  sourceType: ObservationSourceType;
  sourceId: string;
}

export interface ObservationWarning {
  code: string;
  message: string;
  severity: "info" | "warning" | "error";
  sourceId: string | null;
}

export interface UnresolvedQuestion {
  code: string;
  question: string;
  answerType:
    | "single-choice"
    | "multi-choice"
    | "column-selection"
    | "method-confirmation"
    | "assumption-confirmation"
    | "boolean"
    | "text";
}

export type StepObservationStatus =
  | "succeeded"
  | "failed"
  | "blocked"
  | "needs-review";

export type ObservationType =
  | "pre-plan"
  | "step-output"
  | "analysis-execution"
  | "review";

export type ObservationFailureCategory =
  | "none"
  | "input"
  | "method"
  | "runtime"
  | "artifact"
  | "review"
  | "unsupported"
  | "unknown";

/** Immutable structured facts for one durable job attempt. */
export interface StepObservation {
  schemaVersion: "1";
  workflowId: string;
  planId: string | null;
  /** Null only for a pre-plan or reviewer observation with no materialized task. */
  taskId: string | null;
  /** Durable source job; never inferred from the current in-memory worker. */
  sourceJobId: string;
  runId: string | null;
  reviewId: string | null;
  observationType: ObservationType;
  stepKey: string;
  attempt: number;
  status: StepObservationStatus;
  facts: [ObservationFact, ...ObservationFact[]];
  warnings: ObservationWarning[];
  unresolvedQuestions: UnresolvedQuestion[];
  artifactIds: string[];
  failureCategory: ObservationFailureCategory;
  recommendedActions: [AgentAction, ...AgentAction[]];
}

export interface StepObservationOut extends StepObservation {
  id: string;
  inputSha256: string;
  outputSha256: string;
  generator: string;
  promptVersion: string | null;
  model: string | null;
  modelInvocationId: string | null;
  createdAt: string;
}

export interface AnalysisSpecDiff {
  changedFields: string[];
  previousValues: Record<string, AgentLoopJsonValue>;
  proposedValues: Record<string, AgentLoopJsonValue>;
  reason: string;
}

interface AgentDecisionCore {
  schemaVersion: "1";
  action: AgentAction;
  reasonCode: string;
  reason: string;
  targetStepKey: string | null;
  clarificationRequests: ScientificClarification[];
  proposedAnalysisSpec: AnalysisSpec | null;
  analysisSpecDiff: AnalysisSpecDiff | null;
  requiresUserConfirmation: boolean;
}

export type AgentDecision =
  | (AgentDecisionCore & {
      action: "continue";
      targetStepKey: string;
      clarificationRequests: [];
      proposedAnalysisSpec: null;
      analysisSpecDiff: null;
      requiresUserConfirmation: false;
    })
  | (AgentDecisionCore & {
      action: "request-clarification";
      targetStepKey: null;
      clarificationRequests: [
        ScientificClarification,
        ...ScientificClarification[],
      ];
      proposedAnalysisSpec: null;
      analysisSpecDiff: null;
      requiresUserConfirmation: false;
    })
  | (AgentDecisionCore & {
      action: "revise-analysis-spec";
      targetStepKey: null;
      clarificationRequests: [];
      proposedAnalysisSpec: AnalysisSpec;
      analysisSpecDiff: AnalysisSpecDiff;
      requiresUserConfirmation: true;
    })
  | (AgentDecisionCore & {
      action: "retry-step";
      targetStepKey: string;
      clarificationRequests: [];
      proposedAnalysisSpec: null;
      analysisSpecDiff: null;
      requiresUserConfirmation: false;
    })
  | (AgentDecisionCore & {
      action: "complete" | "stop";
      targetStepKey: null;
      clarificationRequests: [];
      proposedAnalysisSpec: null;
      analysisSpecDiff: null;
      requiresUserConfirmation: false;
    });

export type AgentDecisionStatus =
  | "proposed"
  | "waiting-user-confirmation"
  | "applied"
  | "superseded"
  | "rejected"
  | "failed";

export type AgentDecisionOut = AgentDecision & {
  id: string;
  workflowId: string;
  observationId: string;
  decisionRevision: number;
  status: AgentDecisionStatus;
  expectedWorkflowRevision: number;
  generator: string;
  promptVersion: string | null;
  model: string | null;
  modelInvocationId: string | null;
  inputSha256: string;
  outputSha256: string;
  researchContextSnapshotId: string | null;
  researchContextSnapshotSha256: string | null;
  appliedAt: string | null;
  createdAt: string;
};

export interface AgentDecisionSummary {
  id: string;
  observationId: string;
  action: AgentAction;
  reason: string;
  status: AgentDecisionStatus;
  requiresUserConfirmation: boolean;
  researchContextSnapshotId: string | null;
  researchContextSnapshotSha256: string | null;
  createdAt: string;
  appliedAt: string | null;
}

export type AgentLoopLimitName =
  | "agent-steps"
  | "plan-revisions"
  | "analysis-spec-revisions"
  | "step-retries"
  | "clarification-rounds"
  | "model-decisions"
  | "invalid-model-decisions";

export interface AgentLoopLimitUsage {
  count: number;
  limit: number;
  reached: boolean;
}

/** Seven persisted counters; none depend on process-local memory. */
export interface AgentLoopLimitState {
  agentSteps: AgentLoopLimitUsage;
  planRevisions: AgentLoopLimitUsage;
  analysisSpecRevisions: AgentLoopLimitUsage;
  stepRetries: AgentLoopLimitUsage;
  clarificationRounds: AgentLoopLimitUsage;
  modelDecisions: AgentLoopLimitUsage;
  invalidModelDecisions: AgentLoopLimitUsage;
}

interface CurrentAgentLoopSnapshotFields {
  latestObservation: StepObservationOut | null;
  pendingDecision: AgentDecisionOut | null;
  decisionHistory: AgentDecisionSummary[];
  agentLoopLimits: AgentLoopLimitState;
}

/** Frozen pre-agent-loop snapshots remain readable, but fields cannot be partial. */
interface LegacyAgentLoopSnapshotFields {
  latestObservation?: never;
  pendingDecision?: never;
  decisionHistory?: never;
  agentLoopLimits?: never;
}

export type AgentResearchWorkflowSnapshot = ResearchWorkflowSnapshotBase & {
  workflow: AgentResearchWorkflow;
  intentDecision: IntentDecision | null;
  interactions: InteractionRequest[];
  plan: ResearchWorkflowPlan | null;
  pendingApprovals: WorkflowPendingApproval[];
  latestReview: ResearchWorkflowReview | DatasetAnalysisReview | null;
  datasetProfile: DatasetProfile | null;
  analysisIntent: WorkflowAnalysisIntent | null;
  analysisRun: WorkflowAnalysisRun | null;
  analysisSpec: WorkflowAnalysisSpec | null;
  structuredResult: WorkflowStructuredAnalysisResult | null;
  reviewWarningAcceptance: DatasetReviewWarningAcceptance | null;
} & (CurrentAgentLoopSnapshotFields | LegacyAgentLoopSnapshotFields);

export type PendingAgentResearchWorkflowSnapshot = Omit<
  AgentResearchWorkflowSnapshot,
  | "workflow"
  | "plan"
  | "pendingApprovals"
  | "result"
  | "latestReview"
  | "datasetProfile"
  | "analysisIntent"
  | "analysisRun"
  | "analysisSpec"
  | "structuredResult"
  | "reviewWarningAcceptance"
  | "latestObservation"
  | "pendingDecision"
  | "decisionHistory"
  | "agentLoopLimits"
> & {
  workflow: PendingAgentResearchWorkflow;
  plan: null;
  pendingApprovals: [];
  result: null;
  latestReview: null;
  datasetProfile: null;
  analysisIntent: null;
  analysisRun: null;
  analysisSpec: null;
  structuredResult: null;
  reviewWarningAcceptance: null;
} & (CurrentAgentLoopSnapshotFields | LegacyAgentLoopSnapshotFields);

export type ResolvedResearchWorkflowSnapshot =
  | LiteratureResearchWorkflowSnapshot
  | DatasetAnalysisWorkflowSnapshot;

export type ResearchWorkflowSnapshot =
  | ResolvedResearchWorkflowSnapshot
  | AgentResearchWorkflowSnapshot;

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

export interface AgentRemoteDataApprovalEventData
  extends WorkflowRemoteDataApprovalEventBase {
  dataCategories: ["user-goal", "source-metadata", "user-answer"];
}

export type WorkflowRemoteDataApprovalEventData =
  | LiteratureRemoteDataApprovalEventData
  | DatasetRemoteDataApprovalEventData
  | AgentRemoteDataApprovalEventData;

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
  approvalSchemaVersion:
    | "analysis-intent-v2"
    | "analysis-intent-v3"
    | "analysis-intent-v4";
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

export interface AgentRunCreatedEventData {
  goalSha256: string;
  sourceIds: string[];
  mode: "autonomous";
  generationMode: ResearchGenerationMode;
}

export interface IntentDecisionEventData {
  intentDecisionId: string;
  intent: ResearchIntent;
  confidence: number;
  outputSha256: string;
}

export interface AnalysisMethodSelectionStartedEventData {
  datasetSourceId: string;
  datasetContentHash: string;
  datasetProfileSha256: string;
}

export interface AnalysisClarificationRequestedEventData {
  interactionId: string;
  clarificationType: string;
  selectorInputSha256: string;
  selectorOutputSha256: string;
}

export interface AnalysisSpecEventData {
  analysisSpecId: string;
  revision: number;
  specSha256: string;
  datasetProfileSha256: string;
  selectorKind: "local-deterministic" | "remote-model-assisted";
  promptVersion: string | null;
}

export interface AnalysisCompiledEventData {
  analysisIntentId: string;
  analysisSpecId: string;
  specSha256: string;
  datasetProfileSha256: string;
  compilerVersion: string;
  approvedCodeSha256: string;
  runtimePolicyId: string;
}

export interface AnalysisStructuredResultEventData {
  structuredResultId: string;
  analysisSpecId: string;
  analysisIntentId: string;
  runId: string;
  resultSha256: string;
}

export interface AnalysisUnsupportedEventData {
  capability: string;
  explanation: string;
  supportedAlternatives: Array<
    "descriptive" | "two-group-comparison" | "correlation"
  >;
  selectorInputSha256: string;
  selectorOutputSha256: string;
}

interface InteractionEventDataBase {
  interactionId: string;
  requestType: InteractionRequestType;
  required: boolean;
  expectedWorkflowRevision: number;
}

export interface InteractionRequestedEventData
  extends InteractionEventDataBase {
  responseId: null;
  responseRevision: null;
}

export interface InteractionAnsweredEventData
  extends InteractionEventDataBase {
  responseId: string;
  responseRevision: number;
}

export type InteractionEventData =
  | InteractionRequestedEventData
  | InteractionAnsweredEventData;

interface AgentLoopEventDataBase {
  observationId: string | null;
  decisionId: string | null;
  action: AgentAction | null;
  taskId: string | null;
  targetStepKey: string | null;
  previousAnalysisSpecId: string | null;
  proposedAnalysisSpecId: string | null;
  expectedWorkflowRevision: number;
  reasonCode: string;
}

export interface AgentObservationCreatedEventData
  extends AgentLoopEventDataBase {
  observationId: string;
  decisionId: null;
  action: null;
  targetStepKey: null;
  previousAnalysisSpecId: null;
  proposedAnalysisSpecId: null;
}

export interface AgentDiscoverySelectionOperationSignal {
  operationKey: string;
  stepKey: string;
  queryId: string;
  provider: DiscoveryProvider;
  queryAttemptCount: number;
  providerAttemptCount: number;
  queryNoNoveltyCount: number;
  queryNovelCandidateCount: number;
  queryDuplicateCount: number;
  tieBreakSha256: string;
  rank: number;
}

export interface AgentDiscoverySelectionProjection {
  schemaVersion: "1";
  policyVersion: "discovery-next-operation-v1";
  workflowId: string;
  planId: string;
  planSha256: string;
  discoverySpecId: string;
  discoverySpecRevision: number;
  discoverySpecSha256: string;
  eligibleOperations: AgentDiscoverySelectionOperationSignal[];
  selectedOperationKey: string;
  selectedStepKey: string;
  selectionSnapshotSha256: string;
  reasonCode:
    | "only-eligible-operation"
    | "query-coverage-gap"
    | "provider-coverage-gap"
    | "lower-query-no-novelty"
    | "higher-observed-novelty"
    | "lower-duplicate-burden"
    | "stable-tie-break";
  postcondition: "queue-selected-pending-approved-operation-only";
}

export interface AgentDecisionEventData extends AgentLoopEventDataBase {
  observationId: string;
  decisionId: string;
  action: AgentAction;
  researchContextSnapshotId: string | null;
  researchContextSnapshotSha256: string | null;
  discoverySelection: AgentDiscoverySelectionProjection | null;
  discoverySelectionSha256: string | null;
}

export interface AgentStepRetryRequestedEventData
  extends AgentLoopEventDataBase {
  observationId: string;
  decisionId: string;
  action: "retry-step";
  taskId: string;
  targetStepKey: string;
  previousAnalysisSpecId: null;
  proposedAnalysisSpecId: null;
}

export interface AgentAnalysisSpecRevisionEventData
  extends AgentLoopEventDataBase {
  observationId: string;
  decisionId: string;
  action: "revise-analysis-spec";
  previousAnalysisSpecId: string;
  proposedAnalysisSpecId: string;
}

export interface AgentLoopLimitReachedEventData
  extends AgentLoopEventDataBase {
  action: "stop";
  targetStepKey: null;
  limitName: AgentLoopLimitName;
}

export interface AgentStoppedEventData extends AgentLoopEventDataBase {
  observationId: string;
  decisionId: string;
  action: "stop";
  targetStepKey: null;
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
  | DatasetReviewWarningsAcceptedEventData
  | AgentRunCreatedEventData
  | IntentDecisionEventData
  | AnalysisMethodSelectionStartedEventData
  | AnalysisClarificationRequestedEventData
  | AnalysisSpecEventData
  | AnalysisCompiledEventData
  | AnalysisStructuredResultEventData
  | AnalysisUnsupportedEventData
  | InteractionEventData
  | AgentObservationCreatedEventData
  | AgentDecisionEventData
  | AgentStepRetryRequestedEventData
  | AgentAnalysisSpecRevisionEventData
  | AgentLoopLimitReachedEventData
  | AgentStoppedEventData;

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
  | WorkflowEventEnvelope<
      "agent.observation-created",
      AgentObservationCreatedEventData
    >
  | WorkflowEventEnvelope<
      | "agent.decision-proposed"
      | "agent.decision-approved"
      | "agent.decision-rejected"
      | "agent.decision-applied",
      AgentDecisionEventData
    >
  | WorkflowEventEnvelope<
      "agent.step-retry-requested",
      AgentStepRetryRequestedEventData
    >
  | WorkflowEventEnvelope<
      | "agent.analysis-spec-revision-proposed"
      | "agent.analysis-spec-revision-approved",
      AgentAnalysisSpecRevisionEventData
    >
  | WorkflowEventEnvelope<
      "agent.loop-limit-reached",
      AgentLoopLimitReachedEventData
    >
  | WorkflowEventEnvelope<"agent.stopped", AgentStoppedEventData>
  | WorkflowEventEnvelope<"agent-run.created", AgentRunCreatedEventData>
  | WorkflowEventEnvelope<
      "intent.decision-recorded",
      IntentDecisionEventData
    >
  | WorkflowEventEnvelope<
      "analysis.method-selection-started",
      AnalysisMethodSelectionStartedEventData
    >
  | WorkflowEventEnvelope<
      "analysis.clarification-requested",
      AnalysisClarificationRequestedEventData
    >
  | WorkflowEventEnvelope<
      "analysis.spec-created" | "analysis.spec-superseded" | "analysis.spec-approved",
      AnalysisSpecEventData
    >
  | WorkflowEventEnvelope<"analysis.compiled", AnalysisCompiledEventData>
  | WorkflowEventEnvelope<
      "analysis.execution-approval-requested",
      AnalysisApprovalEventData
    >
  | WorkflowEventEnvelope<
      "analysis.execution-started",
      AnalysisRunStartedEventData
    >
  | WorkflowEventEnvelope<
      "analysis.structured-result-created",
      AnalysisStructuredResultEventData
    >
  | WorkflowEventEnvelope<
      "analysis.review-completed",
      WorkflowReviewEventData
    >
  | WorkflowEventEnvelope<"analysis.unsupported", AnalysisUnsupportedEventData>
  | WorkflowEventEnvelope<
      "interaction.requested",
      InteractionRequestedEventData
    >
  | WorkflowEventEnvelope<
      "interaction.answered",
      InteractionAnsweredEventData
    >
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
  rowVersion: number;
  archivedAt: string | null;
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
  pageManifestHash?: string | null;
  discoveryLineage?: DiscoverySourceLineage | null;
  createdAt: string;
}

export interface DiscoverySourceLineage {
  schemaVersion: "1";
  workflowId: string;
  candidateId: string;
  candidateSha256: string;
  occurrenceInvocationId: string;
  queryId: string;
  provider: DiscoveryProvider;
  rawItemSha256: string;
  sourceContentHash: string;
}

export interface AttachDiscoveryCandidatePdfInput {
  workflowId: string;
  candidateId: string;
  candidateSha256: string;
  occurrenceInvocationId: string;
  confirmIdentityMismatch?: boolean;
}

/**
 * Public paper-discovery output is deliberately separate from ResearchSource.
 * It is provider metadata only: it has not been imported, screened, or
 * verified against a local full text.
 */
export type RemoteDiscoveryProvider = "arxiv" | "crossref" | "openalex" | "pubmed";
export type DiscoveryProvider = RemoteDiscoveryProvider;
export type DiscoveryCandidateProvider = DiscoveryProvider | "csl-json-file";
export type DiscoverySort = "relevance" | "newest";
export type DiscoveryOperationStatus =
  | "not-started"
  | "prepared"
  | "pending"
  | "succeeded"
  | "failed"
  | "outcome-unknown"
  | "cancelled";
export type DiscoveryRetryClassification =
  | "safe-to-retry"
  | "never-retry"
  | "manual-review";

export interface DiscoveryQueryScope {
  id: string;
  query: string;
  providers: DiscoveryProvider[];
  yearFrom: number | null;
  yearTo: number | null;
  sort: DiscoverySort;
  maxResultsPerProvider: number;
}

export interface DiscoveryStopPolicy {
  minUniqueCandidates: number;
  maxAttempts: number;
  maxConsecutiveNoNovelty: number;
}

export interface DiscoveryExactScope {
  schemaVersion: "1";
  question: string;
  queries: DiscoveryQueryScope[];
  stopPolicy: DiscoveryStopPolicy;
  downloadOpenAccessPdfs: false;
  maxPdfDownloads: 0;
}

/** Public Crossref metadata query. */
export interface CreateCrossrefDiscoveryQueryInput {
  id: string;
  query: string;
  providers: ["crossref"];
  yearFrom: number | null;
  yearTo: number | null;
  sort: DiscoverySort;
  maxResultsPerProvider: number;
}

/** Narrow OpenAlex metadata query accepted by the discovery backend. */
export interface CreateOpenAlexDiscoveryQueryInput {
  id: string;
  query: string;
  providers: ["openalex"];
  yearFrom: null;
  yearTo: null;
  sort: "relevance";
  maxResultsPerProvider: number;
}

export interface CreateCrossrefOpenAlexDiscoveryQueryInput {
  id: string;
  query: string;
  providers: ["crossref", "openalex"];
  yearFrom: null;
  yearTo: null;
  sort: "relevance";
  maxResultsPerProvider: number;
}

export type CreateDiscoveryQueryInput =
  | CreateCrossrefDiscoveryQueryInput
  | CreateOpenAlexDiscoveryQueryInput
  | CreateCrossrefOpenAlexDiscoveryQueryInput;

export interface CreateDiscoveryRunInput {
  goal: string;
  discoverySpec: {
    schemaVersion: "1";
    question: string;
    queries: [CreateDiscoveryQueryInput, ...CreateDiscoveryQueryInput[]];
    stopPolicy: DiscoveryStopPolicy;
    downloadOpenAccessPdfs: false;
    maxPdfDownloads: 0;
  };
}

export interface DiscoveryOperationProgress {
  operationKey: string;
  queryId: string;
  provider: DiscoveryProvider;
  status: DiscoveryOperationStatus;
  attempt: number | null;
  invocationId: string | null;
  returnedCount: number;
  novelCandidateCount: number;
  duplicateCount: number;
  candidateSetSha256: string | null;
  errorCode: string | null;
  retryClassification: DiscoveryRetryClassification | null;
  createdAt: string | null;
  finishedAt: string | null;
}

export interface DiscoverySummary {
  totalOperations: number;
  notStartedOperations: number;
  inProgressOperations: number;
  succeededOperations: number;
  failedOperations: number;
  outcomeUnknownOperations: number;
  cancelledOperations: number;
  returnedCount: number;
  novelCandidateCount: number;
  duplicateCount: number;
  uniqueCandidateCount: number;
  occurrenceCount: number;
}

export interface DiscoveryCandidateOccurrence {
  invocationId: string;
  queryId: string;
  provider: DiscoveryCandidateProvider;
  attempt: number;
  rank: number;
  rawItemSha256: string;
}

export interface DiscoveryCandidate {
  id: string;
  provider: DiscoveryCandidateProvider;
  providerId: string;
  title: string;
  authors: string[];
  abstract: string | null;
  publicationDate: string | null;
  doi: string | null;
  arxivId: string | null;
  pmid: string | null;
  candidateSha256: string;
  trustClassification: "untrusted-metadata";
  fullTextVerification: "not-verified";
  importAvailability: "manual-pdf-required";
  landingPageAvailability: "reported" | "not-reported";
  openAccessPdfAvailability: "reported" | "not-reported";
  attachmentStatus?: "manual-pdf-required" | "verified-local-source";
  attachedSourceId?: string | null;
  occurrences: DiscoveryCandidateOccurrence[];
}

export type CandidateTriageDecisionValue = "keep" | "reject" | "uncertain";

/**
 * A human project-scoped judgment over untrusted discovery metadata.
 * It is not a source, screening decision, evidence record, or report input.
 */
export interface CandidateTriageDecision {
  id: string;
  projectId: string;
  candidateId: string;
  decision: CandidateTriageDecisionValue;
  reason: string | null;
  criteriaVersion: string;
  evidenceStatus: "not-evidence";
  rowVersion: number;
  createdAt: string;
  updatedAt: string;
}

export interface UpsertCandidateTriageDecisionInput {
  decision: CandidateTriageDecisionValue;
  reason?: string | null;
  criteriaVersion?: string;
  /** Zero creates the first judgment; updates require the current row version. */
  expectedVersion: number;
}

export interface DiscoveryCandidatePage {
  offset: number;
  limit: number;
  total: number;
  hasMore: boolean;
  items: DiscoveryCandidate[];
}

export interface CslJsonCandidateImport {
  schemaVersion: "1";
  projectId: string;
  workflowId: string;
  invocationId: string;
  fileSha256: string;
  parserVersion: "csl-json-file-v1";
  importedCount: number;
  unchangedCount: number;
  candidateIds: string[];
  replayed: boolean;
}

export interface DiscoveryAgentSelection {
  decisionId: string;
  selectedOperationKey: string;
  selectedStepKey: string;
  queryId: string;
  provider: DiscoveryCandidateProvider;
  reasonCode:
    | "only-eligible-operation"
    | "query-coverage-gap"
    | "provider-coverage-gap"
    | "lower-query-no-novelty"
    | "higher-observed-novelty"
    | "lower-duplicate-burden"
    | "stable-tie-break";
  eligibleOperationCount: number;
  queryAttemptCount: number;
  providerAttemptCount: number;
  queryNoNoveltyCount: number;
  queryNovelCandidateCount: number;
  queryDuplicateCount: number;
  selectionSnapshotSha256: string;
}

/** Bounded, workflow-scoped read model; it contains no URL, local path, source, or evidence. */
export interface WorkflowDiscoverySnapshot {
  workflowId: string;
  projectId: string;
  workflowStatus:
    | "waiting-plan-approval"
    | "running"
    | "blocked"
    | "failed"
    | "cancelled"
    | "completed";
  /** Durable approved-policy stop; failures remain on the affected operation. */
  stopReason:
    | "discovery-candidate-target-reached"
    | "discovery-no-novelty-limit"
    | "discovery-attempt-budget-reached"
    | null;
  discoverySpecId: string;
  discoverySpecRevision: number;
  discoverySpecSha256: string;
  discoverySpecStatus: "pending-approval" | "approved" | "rejected" | "superseded";
  exactScope: DiscoveryExactScope;
  operations: DiscoveryOperationProgress[];
  summary: DiscoverySummary;
  candidates: DiscoveryCandidatePage;
  /** Latest applied Agent choice; null until a completed operation triggers the loop. */
  latestAgentSelection: DiscoveryAgentSelection | null;
}

/** Structural coverage of confirmed local extraction evidence; never a scientific score. */
export interface EvidenceCoverageSourceBreadth {
  frozenSourceCount: number;
  sourcesWithCoveredEvidenceCount: number;
  sourcesWithoutCoveredEvidenceCount: number;
  verifiedReferencedSpanCount: number;
}

export interface EvidenceCoverageFacet {
  columnId: string;
  name: string;
  state: "complete" | "partial" | "unverified" | "missing";
  sourceCount: number;
  coveredSourceCount: number;
  awaitingConfirmationSourceCount: number;
  unverifiedSourceCount: number;
  missingSourceCount: number;
}

export interface EvidenceCoverageClaimCoverage {
  state: "not-generated" | "not-verified" | "verified-frozen";
  totalClaimCount: number;
  /** Structural relationship count; never a scientific support score. */
  evidenceLinkedClaimCount: number;
  /** Schema-v1 compatibility only. New UI must not present this as coverage. */
  supportedClaimCount: number;
  unresolvedQuestionCount: number;
}

export interface WorkflowEvidenceCoverage {
  schemaVersion: "1";
  workflowId: string;
  projectId: string;
  planId: string | null;
  planVersion: number | null;
  planSha256: string | null;
  state: "not-ready" | "available" | "reviewed";
  sourceSetSha256: string | null;
  sourceBreadth: EvidenceCoverageSourceBreadth;
  facets: EvidenceCoverageFacet[];
  claimCoverage: EvidenceCoverageClaimCoverage;
  contradictionAssessment: "not-assessed";
}

export type ResearchMemoryType =
  | "user-decision"
  | "assumption"
  | "open-question"
  | "failure-lesson"
  | "operational-fact";
export type ResearchMemoryStatus =
  | "candidate"
  | "committed"
  | "rejected"
  | "superseded"
  | "invalidated";
export type ResearchMemoryAction =
  | "accept"
  | "reject"
  | "invalidate";

export type ResearchMemoryReferenceType =
  | "step-observation"
  | "user-response"
  | "source"
  | "evidence"
  | "artifact";

export interface ResearchMemoryReference {
  id: string;
  sha256: string;
  type: ResearchMemoryReferenceType;
}

export type ResearchMemoryContext =
  | {
      state: "selected";
      reasonCode: "selected-in-latest-snapshot";
      snapshotId: string;
      snapshotSha256: string;
    }
  | {
      state: "eligible";
      reasonCode: "eligible-for-future-snapshot";
      snapshotId: string | null;
      snapshotSha256: string | null;
    }
  | {
      state: "excluded";
      reasonCode:
        | "bounded-context-excluded"
        | "candidate-excluded"
        | "rejected-excluded"
        | "superseded-excluded"
        | "invalidated-excluded"
        | "source-missing"
        | "source-not-ready"
        | "source-stale"
        | "evidence-missing"
        | "evidence-invalid";
      snapshotId: string | null;
      snapshotSha256: string | null;
    };

export interface ResearchMemoryRecord {
  id: string;
  projectId: string;
  scopeWorkflowId: string | null;
  subjectKey: string;
  revision: number;
  previousId: string | null;
  schemaVersion: "1";
  type: ResearchMemoryType;
  contentJson: Record<string, unknown>;
  sourceRefs: ResearchMemoryReference[];
  artifactRefs: ResearchMemoryReference[];
  invalidationRule: string | null;
  createdBy: string;
  memorySha256: string;
  createdAt: string;
  updatedAt: string;
  status: ResearchMemoryStatus;
}

interface ResearchMemoryWorkspaceItemBase extends ResearchMemoryRecord {
  subjectHeadId: string;
  subjectHeadRevision: number;
  context: ResearchMemoryContext;
}

export type ResearchMemory =
  | (ResearchMemoryWorkspaceItemBase & {
      status: "candidate";
      availableActions: ["accept", "reject"];
    })
  | (ResearchMemoryWorkspaceItemBase & {
      status: "committed";
      availableActions: ["invalidate"];
    })
  | (ResearchMemoryWorkspaceItemBase & {
      status: "rejected" | "superseded" | "invalidated";
      availableActions: [];
    });

export interface ResearchMemoryWorkspaceCounts {
  candidate: number;
  committed: number;
  rejected: number;
  superseded: number;
  invalidated: number;
}

export interface ResearchMemoryWorkspace {
  schemaVersion: "1";
  projectId: string;
  workflowId: string;
  latestContextSnapshotId: string | null;
  latestContextSnapshotSha256: string | null;
  counts: ResearchMemoryWorkspaceCounts;
  items: ResearchMemory[];
  workspaceSha256: string;
}

export interface ResolveResearchMemoryCandidateInput {
  decision: "accept" | "reject";
  expectedContentHash: string;
  expectedStatus: "candidate";
  expectedRevision: number;
  expectedSubjectHeadId: string;
  expectedSubjectHeadRevision: number;
}

export interface InvalidateResearchMemoryInput {
  expectedContentHash: string;
  expectedStatus: "committed";
  expectedRevision: number;
  expectedSubjectHeadId: string;
  expectedSubjectHeadRevision: number;
}

export interface CreateEvidenceMemoryCandidateInput {
  evidenceId: string;
  expectedSourceContentHash: string;
  expectedQuoteHash: string;
}

export interface VerifiedEpisode {
  episodeId: string;
  episodeSha256: string;
  action: "remembered-evidence-action-v1";
  schemaVersion: "1";
}

export type CreateEvidenceMemoryCandidateResult = {
  outcome:
    | "candidate-created"
    | "candidate-reopened"
    | "already-remembered";
  memory: ResearchMemoryRecord;
  verifiedEpisode: VerifiedEpisode;
};

export type SkillReplayName =
  | "happy"
  | "malformed"
  | "tool-failure"
  | "permission-denial"
  | "prompt-injection"
  | "restart-recovery";

export interface SkillReplayResult {
  name: SkillReplayName;
  fixtureSha256: string;
  outcome: string;
  passed: boolean;
  postconditionSha256: string;
  resultSha256: string;
}

export interface SkillCandidate {
  id: string;
  projectId: string;
  workflowId: string;
  schemaVersion: "1";
  name: "remember-verified-evidence";
  description: string;
  scope: "project";
  triggerJson: Record<string, unknown>;
  inputsJson: Record<string, unknown>;
  preconditionsJson: Array<Record<string, unknown>>;
  allowedToolsJson: ["spark.research_memory.remember_verified_evidence@1"];
  requiredPermissionsJson: ["project-memory:candidate-write"];
  procedureJson: Array<Record<string, unknown>>;
  postconditionsJson: Array<Record<string, unknown>>;
  failurePolicyJson: Record<string, unknown>;
  provenanceRequirementsJson: string[];
  originTraceIds: [string];
  sanitizedSourceHash: string;
  parentSkillId: null;
  version: number;
  contentHash: string;
  status: "failed-validation" | "awaiting-approval";
  generatedSkillMd: string;
  evaluationJson: {
    schemaVersion: "1";
    runner: "isolated-sqlite-capability-replay-v1";
    results: SkillReplayResult[];
    passed: boolean;
  };
  createdAt: string;
}

export interface CreateSkillCandidateInput {
  memoryId: string;
  expectedMemoryContentHash: string;
  episodeId?: string;
  expectedEpisodeSha256?: string;
}

export interface CreateSkillCandidateResult {
  outcome: "candidate-created" | "already-exists";
  candidate: SkillCandidate;
}

export type SkillActivationStatus =
  | "installing"
  | "active"
  | "rollback-pending"
  | "rolled-back"
  | "blocked";

export interface SkillActivation {
  id: string;
  projectId: string;
  workflowId: string;
  candidateId: string;
  schemaVersion: "1";
  targetRelativePath: ".opencode/skills/remember-verified-evidence/SKILL.md";
  candidateContentHash: string;
  templateSha256: string;
  evaluationSha256: string;
  approvalSha256: string;
  priorPresent: boolean;
  priorSha256: string | null;
  installedSha256: string;
  createdDirectory: boolean;
  status: SkillActivationStatus;
  createdAt: string;
  updatedAt: string;
  activatedAt: string | null;
  rolledBackAt: string | null;
}

export interface SkillActivationPreview {
  schemaVersion: "1";
  projectId: string;
  workflowId: string;
  candidateId: string;
  expectedStatus: "awaiting-approval";
  targetRelativePath: ".opencode/skills/remember-verified-evidence/SKILL.md";
  candidateContentHash: string;
  templateSha256: string;
  evaluationSha256: string;
  approvalSha256: string;
  priorPresent: boolean;
  priorSha256: string | null;
  targetDirectoryPresent: boolean;
  latestActivation: SkillActivation | null;
}

export interface ApproveSkillActivationInput {
  expectedStatus: "awaiting-approval";
  expectedCandidateContentHash: string;
  expectedTemplateSha256: string;
  expectedEvaluationSha256: string;
  expectedApprovalSha256: string;
  expectedPriorPresent: boolean;
  expectedPriorSha256: string | null;
  expectedTargetDirectoryPresent: boolean;
}

export interface RollbackSkillActivationInput {
  expectedStatus: "active";
  expectedActivationId: string;
  expectedApprovalSha256: string;
  expectedInstalledSha256: string;
  expectedCurrentTargetSha256: string;
}

export interface InvokeActiveRememberEvidenceResult {
  memoryCandidateId: string;
  memoryContentHash: string;
  revision: number;
  episodeId: string;
  episodeHash: string;
  outcome:
    | "candidate-created"
    | "candidate-reopened"
    | "already-remembered";
}

export type ScreeningDecisionValue = "include" | "exclude";

export interface ScreeningDecision {
  id: string;
  projectId: string;
  sourceId: string;
  decision: ScreeningDecisionValue;
  reason: string | null;
  criteriaVersion: string;
  rowVersion: number;
  createdAt: string;
  updatedAt: string;
}

export interface UpsertScreeningDecisionInput {
  decision: ScreeningDecisionValue;
  reason?: string | null;
  criteriaVersion?: string;
  /** Zero creates the first decision; updates require the current row version. */
  expectedVersion: number;
}

export type EvidenceDirectionValue = "supporting" | "mixed" | "insufficient";

/**
 * A human-confirmed direction judgment for one source in one persisted answer.
 * It is deliberately separate from model-generated claim relationships.
 */
export interface EvidenceDirectionJudgment {
  id: string;
  projectId: string;
  answerId: string;
  sourceId: string;
  direction: EvidenceDirectionValue;
  rowVersion: number;
  createdAt: string;
  updatedAt: string;
}

export interface UpsertEvidenceDirectionJudgmentInput {
  direction: EvidenceDirectionValue;
  /** Zero creates the first judgment; updates require the current row version. */
  expectedVersion: number;
}

export type ExtractionCellReviewStatus = "unreviewed" | "confirmed";

export interface ExtractionColumn {
  id: string;
  projectId: string;
  name: string;
  instructions: string | null;
  orderIndex: number;
  rowVersion: number;
  createdAt: string;
  updatedAt: string;
}

export interface ExtractionCell {
  id: string;
  projectId: string;
  sourceId: string;
  columnId: string;
  value: string;
  /** Human review only; this is deliberately independent from EvidenceSpan.verified. */
  reviewStatus: ExtractionCellReviewStatus;
  evidenceIds: string[];
  rowVersion: number;
  createdAt: string;
  updatedAt: string;
}

export interface ExtractionMatrix {
  columns: ExtractionColumn[];
  cells: ExtractionCell[];
}

export interface CreateExtractionColumnInput {
  name: string;
  instructions?: string | null;
}

export interface UpsertExtractionCellInput {
  value: string;
  reviewStatus?: ExtractionCellReviewStatus;
  evidenceIds?: string[];
  /** Zero creates the first cell; updates require the current row version. */
  expectedVersion: number;
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

export interface CreateExactEvidenceSpanInput {
  pageIndex: number;
  quoteText: string;
  expectedSourceContentHash: string;
  expectedPageManifestHash: string;
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

/** JSON contract mirrored from science-core analysis_spec/schemas.py. */
export type DescriptiveStatistic =
  | "count"
  | "missing"
  | "mean"
  | "std"
  | "median"
  | "min"
  | "max"
  | "q1"
  | "q3"
  | "iqr"
  | "unique"
  | "frequency";

export interface DescriptiveOperation {
  type: "descriptive";
  columns: string[];
  statistics: DescriptiveStatistic[];
  plot: "none" | "histogram" | "bar";
}

interface TwoGroupComparisonOperationBase {
  type: "two-group-comparison";
  outcomeColumn: string;
  groupColumn: string;
  groups: [string, string];
  checkAssumptions: boolean;
  plot: "boxplot" | "violin" | "none";
}

/** Method/effect-size pairs rejected by science-core are also unrepresentable here. */
export type TwoGroupComparisonOperation =
  | (TwoGroupComparisonOperationBase & {
      method: "auto";
      effectSize: "hedges-g" | "rank-biserial";
    })
  | (TwoGroupComparisonOperationBase & {
      method: "welch-t-test";
      effectSize: "hedges-g";
    })
  | (TwoGroupComparisonOperationBase & {
      method: "mann-whitney-u";
      effectSize: "rank-biserial";
    });

export interface CorrelationOperation {
  type: "correlation";
  xColumn: string;
  yColumn: string;
  method: "auto" | "pearson" | "spearman";
  confidenceInterval: boolean;
  plot: "scatter" | "none";
}

export type AnalysisOperation =
  | DescriptiveOperation
  | TwoGroupComparisonOperation
  | CorrelationOperation;

interface AnalysisSpecBase {
  schemaVersion: "1";
  objective: string;
  datasetSourceId: string;
  datasetContentHash: string;
  datasetProfileHash: string;
  missingValuePolicy: "drop-per-operation" | "report-only";
  confidenceLevel: number;
  randomSeed: number;
  assumptions: string[];
  limitations: string[];
}

export type AnalysisSpec =
  | (AnalysisSpecBase & { operation: DescriptiveOperation })
  | (AnalysisSpecBase & { operation: TwoGroupComparisonOperation })
  | (AnalysisSpecBase & { operation: CorrelationOperation });

/** Durable, revisioned method selection bound to the approved workflow plan. */
export interface WorkflowAnalysisSpec {
  id: string;
  revision: number;
  status: "pending-approval" | "approved" | "superseded" | "rejected";
  selectorKind: "local-deterministic" | "remote-model-assisted";
  selectorReason: string;
  promptVersion: string | null;
  specSha256: string;
  datasetProfileSha256: string;
  spec: AnalysisSpec;
  createdAt: string;
}

export type ScientificClarificationType =
  | "outcome-column"
  | "group-column"
  | "group-values"
  | "x-column"
  | "y-column"
  | "analysis-objective"
  | "method-confirmation"
  | "independence-assumption"
  | "missing-value-policy";

export interface ScientificClarificationOption {
  value: string;
  label: string;
  description: string | null;
}

export interface ScientificClarification {
  type: ScientificClarificationType;
  question: string;
  options: ScientificClarificationOption[];
}

export interface ScientificClarificationProposal {
  reason: string;
  requests: ScientificClarification[];
}

export interface UnsupportedAnalysis {
  capability: string;
  explanation: string;
  supportedAlternatives: Array<
    "descriptive" | "two-group-comparison" | "correlation"
  >;
}

export interface CompiledAnalysis {
  compilerVersion: string;
  specSha256: string;
  code: string;
  codeSha256: string;
  expectedOutputs: string[];
  runtimePolicyId: string;
}

export interface AnalysisSampleSummary {
  totalRows: number;
  analyzedRows: number;
  missingRows: number;
}

export interface DescriptiveColumnResult {
  column: string;
  sampleSize: number;
  missingCount: number;
  statistics: Record<string, number | string | null>;
}

export interface DescriptiveAnalysisResult {
  type: "descriptive";
  columns: DescriptiveColumnResult[];
}

export interface TwoGroupComparisonResult {
  type: "two-group-comparison";
  groupColumn: string;
  outcomeColumn: string;
  groups: [string, string];
  sampleSizes: Record<string, number>;
  missingCounts: Record<string, number>;
  descriptiveStatistics: Record<string, Record<string, number | null>>;
  testStatistic: number;
  pValue: number;
  effectSizeName: "hedges-g" | "rank-biserial";
  effectSize: number;
  confidenceInterval: [number, number];
}

export interface CorrelationAnalysisResult {
  type: "correlation";
  xColumn: string;
  yColumn: string;
  sampleSize: number;
  missingPairs: number;
  correlation: number;
  pValue: number;
  confidenceInterval: [number, number] | null;
}

export type OperationResult =
  | DescriptiveAnalysisResult
  | TwoGroupComparisonResult
  | CorrelationAnalysisResult;

export type RequestedMethod =
  | "descriptive"
  | "auto"
  | "welch-t-test"
  | "mann-whitney-u"
  | "pearson"
  | "spearman";

export type ResolvedMethod =
  | "descriptive"
  | "welch-t-test"
  | "mann-whitney-u"
  | "pearson"
  | "spearman";

interface StructuredAnalysisResultBase {
  schemaVersion: "1";
  objective: string;
  datasetSourceId: string;
  datasetContentHash: string;
  datasetProfileHash: string;
  methodSelectionReason: string;
  sampleSummary: AnalysisSampleSummary;
  warnings: string[];
  limitations: string[];
}

type DescriptiveStructuredAnalysisResult = StructuredAnalysisResultBase & {
  operationType: "descriptive";
  requestedMethod: "descriptive";
  resolvedMethod: "descriptive";
  result: DescriptiveAnalysisResult;
};

type WelchStructuredAnalysisResult = StructuredAnalysisResultBase & {
  operationType: "two-group-comparison";
  requestedMethod: "auto" | "welch-t-test";
  resolvedMethod: "welch-t-test";
  result: TwoGroupComparisonResult & { effectSizeName: "hedges-g" };
};

type MannWhitneyStructuredAnalysisResult = StructuredAnalysisResultBase & {
  operationType: "two-group-comparison";
  requestedMethod: "auto" | "mann-whitney-u";
  resolvedMethod: "mann-whitney-u";
  result: TwoGroupComparisonResult & { effectSizeName: "rank-biserial" };
};

type PearsonStructuredAnalysisResult = StructuredAnalysisResultBase & {
  operationType: "correlation";
  requestedMethod: "auto" | "pearson";
  resolvedMethod: "pearson";
  result: CorrelationAnalysisResult;
};

type SpearmanStructuredAnalysisResult = StructuredAnalysisResultBase & {
  operationType: "correlation";
  requestedMethod: "auto" | "spearman";
  resolvedMethod: "spearman";
  result: CorrelationAnalysisResult;
};

/**
 * JSON result union mirrored from analysis_spec/results.py, including its
 * method/result compatibility validation.
 */
export type StructuredAnalysisResult =
  | DescriptiveStructuredAnalysisResult
  | WelchStructuredAnalysisResult
  | MannWhitneyStructuredAnalysisResult
  | PearsonStructuredAnalysisResult
  | SpearmanStructuredAnalysisResult;

/** Persisted structured result and the exact Spec/Intent/Run lineage it attests. */
export interface WorkflowStructuredAnalysisResult {
  id: string;
  analysisSpecId: string;
  analysisIntentId: string;
  runId: string;
  resultSha256: string;
  result: StructuredAnalysisResult;
  createdAt: string;
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
  /** All six fields are present together for compiler-produced AnalysisSpec intents. */
  analysisSpecId?: string | null;
  specSha256?: string | null;
  datasetProfileSha256?: string | null;
  compilerVersion?: string | null;
  codeSha256?: string | null;
  runtimePolicyId?: string | null;
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

interface LegacyWorkflowAnalysisIntentProvenance {
  analysisSpecId?: null;
  specSha256?: null;
  datasetProfileSha256?: null;
  compilerVersion?: null;
  codeSha256?: null;
  runtimePolicyId?: null;
}

interface CompiledWorkflowAnalysisIntentProvenance {
  analysisSpecId: string;
  specSha256: string;
  datasetProfileSha256: string;
  compilerVersion: string;
  codeSha256: string;
  runtimePolicyId: string;
}

type WorkflowAnalysisIntentProvenance =
  | LegacyWorkflowAnalysisIntentProvenance
  | CompiledWorkflowAnalysisIntentProvenance;

export type InitialWorkflowAnalysisIntent = WorkflowAnalysisIntentBase &
  InitialWorkflowAnalysisIntentLineage &
  WorkflowAnalysisIntentProvenance &
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
  LegacyWorkflowAnalysisIntentProvenance &
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
  /** Compiled reviewer conclusion and lineage; all null for legacy fixed analyses. */
  conclusion?: string | null;
  analysisSpecId?: string | null;
  structuredResultSha256?: string | null;
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
