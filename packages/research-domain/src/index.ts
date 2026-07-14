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
  createdAt: string;
}

export interface ScienceCoreHealth {
  status: "ok" | "degraded";
  version: string;
  database: "ok" | "error";
  paperQa: "available" | "unavailable";
  modelGateway: "configured" | "unconfigured";
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
