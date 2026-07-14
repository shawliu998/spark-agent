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
  | "cancel"
  | "retry"
  | "resume";

export type ResearchWorkflowType = "literature-synthesis";

/**
 * Describes how a workflow generates its plan and narrative output.
 * Evidence verification remains deterministic in both modes.
 */
export type ResearchGenerationMode =
  | "local-deterministic"
  | "remote-model-assisted";

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

export interface ResearchWorkflow {
  id: string;
  projectId: string;
  goal: string;
  workflowType: ResearchWorkflowType;
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

export type ResearchWorkflowPlanStatus =
  | "pending-approval"
  | "approved"
  | "rejected"
  | "superseded";

export type ResearchWorkflowTaskStatus =
  | "pending"
  | "queued"
  | "running"
  | "completed"
  | "blocked"
  | "failed"
  | "cancelled";

export type ResearchWorkflowTaskType =
  | "inspect-sources"
  | "extract-local-evidence"
  | "synthesize-extractive-claims";

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
  type: ResearchWorkflowTaskType;
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

export interface ResearchWorkflowPlan {
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

export interface WorkflowPendingApproval {
  id: string;
  workflowId: string;
  planId: string;
  taskId: string | null;
  kind: "plan";
  status: "waiting";
  subjectType: string;
  subjectId: string;
  action: string;
  payloadSha256: string;
  riskLevel: string;
  reason: string;
  affectedResources: string[];
  createdAt: string;
  decidedAt: string | null;
}

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

export type WorkflowReviewVerdict =
  | "passed"
  | "revision-required"
  | "blocked"
  | "failed";

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
  verdict: WorkflowReviewVerdict;
  checks: WorkflowReviewCheck[];
  claimResults: WorkflowClaimReviewResult[];
  requiredRevisions: string[];
  resultSnapshotSha256: string | null;
  resultSnapshot: ResearchWorkflowResult | null;
}

export interface ResearchWorkflowReview {
  id: string;
  reviewType: string;
  verdict: WorkflowReviewVerdict;
  inputSha256: string;
  result: WorkflowDeterministicReviewResult;
  createdAt: string;
}

export interface ResearchWorkflowSnapshot {
  workflow: ResearchWorkflow;
  plan: ResearchWorkflowPlan | null;
  pendingApprovals: WorkflowPendingApproval[];
  result: ResearchWorkflowResult | null;
  latestReview: ResearchWorkflowReview | null;
  allowedActions: ResearchWorkflowAllowedAction[];
  eventCursor: number;
}

export interface WorkflowCreatedEventData {
  workflowType: ResearchWorkflowType;
  goalSha256: string;
  /** Optional while reading event logs created before model-assisted v2. */
  generationMode?: ResearchGenerationMode;
}

export interface WorkflowRemoteDataApprovalEventData {
  provider: string;
  endpointHost: string;
  endpointIdentity: string;
  model: string | null;
  dataCategories: Array<"user-goal">;
}

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
  claimCount: number;
}

export interface WorkflowCancelEventData {
  requested: boolean;
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
  | WorkflowCancelEventData;

export interface WorkflowEvent {
  id: string;
  sequence: number;
  type: string;
  taskId: string | null;
  jobId: string | null;
  data: WorkflowEventData;
  createdAt: string;
}

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

export type AnalysisIntentStatus =
  | "waiting-approval"
  | "approved"
  | "rejected"
  | "executing"
  | "completed"
  | "failed";

export interface AnalysisIntent {
  id: string;
  projectId: string;
  datasetSourceId: string;
  objective: string;
  code: string;
  payloadSha256: string;
  riskLevel: "high";
  affectedResources: string[];
  status: AnalysisIntentStatus;
  createdAt: string;
}

export interface AnalysisArtifact {
  id: string;
  artifactType: string;
  path: string;
  mimeType: string;
  contentHash: string;
  createdAt: string;
}

export type AnalysisRunStatus = "pending" | "running" | "completed" | "failed";

export interface AnalysisRun {
  id: string;
  intentId: string;
  projectId: string;
  status: AnalysisRunStatus;
  logs: string;
  artifacts: AnalysisArtifact[];
  createdAt: string;
  finishedAt: string | null;
  error: string | null;
}
