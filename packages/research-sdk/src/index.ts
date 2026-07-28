import type {
  AcceptWorkflowReviewWarningsInput,
  AgentResearchWorkflowSnapshot,
  AnalysisIntent,
  AnalysisRun,
  AttachDiscoveryCandidatePdfInput,
  CandidateTriageDecision,
  CslJsonCandidateImport,
  CreateAgentRunInput,
  CreateDiscoveryRunInput,
  CreateEvidenceMemoryCandidateInput,
  CreateEvidenceMemoryCandidateResult,
  CreateSkillCandidateInput,
  CreateSkillCandidateResult,
  ApproveSkillActivationInput,
  CreateResearchWorkflowInput as DomainCreateResearchWorkflowInput,
  CreateReportDraftInput,
  DecideWorkflowAnalysisIntentInput,
  EvidenceDirectionJudgment,
  ExportReportDraftInput,
  InteractionRequest,
  InvalidateResearchMemoryInput,
  ResearchMemoryRecord,
  ResearchMemoryWorkspace,
  SkillCandidate,
  SkillActivation,
  SkillActivationPreview,
  RollbackSkillActivationInput,
  InvokeActiveRememberEvidenceResult,
  ResolveResearchMemoryCandidateInput,
  ResolveAgentDecisionInput,
  RespondToInteractionInput,
  ResearchAnswer,
  EvidenceSpan,
  CreateExactEvidenceSpanInput,
  ResearchProject,
  ReportDraftExport,
  ReportDraftRecord,
  ReviewReportDraftInput,
  ResearchSource,
  ExtractionCell,
  ExtractionColumn,
  ExtractionMatrix,
  CreateExtractionColumnInput,
  UpsertExtractionCellInput,
  UpsertEvidenceDirectionJudgmentInput,
  UpsertCandidateTriageDecisionInput,
  ScreeningDecision,
  UpsertScreeningDecisionInput,
  ResearchWorkflowSnapshot,
  ResearchWorkflowResult,
  SaveReportDraftInput,
  ScienceCoreHealth,
  WorkflowAnalysisIntentDecision,
  WorkflowApiErrorDetail,
  WorkflowDiscoverySnapshot,
  WorkflowEvidenceCoverage,
  WorkflowEventsPage,
} from "@spark/research-domain";

export type {
  AcceptWorkflowReviewWarningsInput,
  AgentResearchWorkflowSnapshot,
  CandidateTriageDecision,
  CreateAgentRunInput,
  CreateEvidenceMemoryCandidateInput,
  CreateEvidenceMemoryCandidateResult,
  CreateSkillCandidateInput,
  CreateSkillCandidateResult,
  ApproveSkillActivationInput,
  CreateDiscoveryRunInput,
  CreateReportDraftInput,
  DecideWorkflowAnalysisIntentInput,
  EvidenceDirectionJudgment,
  ExportReportDraftInput,
  InteractionRequest,
  InteractionResponseValue,
  IntentDecision,
  ResolveAgentDecisionInput,
  InvalidateResearchMemoryInput,
  ResearchMemory,
  ResearchMemoryRecord,
  ResearchMemoryWorkspace,
  ReportDraftExport,
  ReportDraftRecord,
  ReviewReportDraftInput,
  SkillCandidate,
  SkillActivation,
  SkillActivationPreview,
  RollbackSkillActivationInput,
  InvokeActiveRememberEvidenceResult,
  ResolveResearchMemoryCandidateInput,
  VerifiedEpisode,
  RespondToInteractionInput,
  SaveReportDraftInput,
  UpsertCandidateTriageDecisionInput,
  UpsertEvidenceDirectionJudgmentInput,
} from "@spark/research-domain";

export interface ScienceCoreClientOptions {
  baseUrl?: string;
  token?: string;
  fetchImpl?: typeof fetch;
  requestTimeoutMs?: number;
}

export interface CreateResearchProjectInput {
  title: string;
  description?: string;
  researchDomain?: string;
}

export interface CreateResearchProjectOptions {
  signal?: AbortSignal;
}

export interface ListResearchProjectsOptions {
  includeArchived?: boolean;
  signal?: AbortSignal;
}

export interface ProjectMutationOptions {
  signal?: AbortSignal;
  idempotencyKey: string;
}

export interface AskResearchQuestionInput {
  question: string;
  model?: string;
  remoteDataApproved: boolean;
}

export type CreateResearchWorkflowInput = DomainCreateResearchWorkflowInput;

export interface ApproveResearchWorkflowPlanInput {
  approvalId: string;
  planId: string;
  planVersion: number;
  planSha256: string;
  expectedWorkflowRevision: number;
}

export interface ResearchWorkflowMutationInput {
  expectedWorkflowRevision?: number;
}

export interface RetryResearchWorkflowInput extends ResearchWorkflowMutationInput {
  taskId?: string;
}

export interface ResearchWorkflowListOptions {
  activeOnly?: boolean;
  limit?: number;
  signal?: AbortSignal;
}

export interface ResearchWorkflowEventsOptions {
  after?: number;
  limit?: number;
  signal?: AbortSignal;
}

export interface WorkflowDiscoveryOptions {
  offset?: number;
  limit?: number;
  signal?: AbortSignal;
}

export interface ScienceCoreRequestOptions {
  signal?: AbortSignal;
  idempotencyKey?: string;
}

export interface PrepareAnalysisIntentInput {
  datasetSourceId: string;
  objective: string;
  code: string;
}

export type AnalysisIntentDecision = WorkflowAnalysisIntentDecision;

export class ScienceCoreApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;
  readonly details: Record<string, unknown> | undefined;

  constructor(status: number, detail: WorkflowApiErrorDetail) {
    super(detail.userMessage);
    this.name = "ScienceCoreApiError";
    this.status = status;
    this.code = detail.code;
    this.retryable = detail.retryable;
    this.details = detail.details;
  }
}

/** Typed boundary around the replaceable local science-core service. */
export class ScienceCoreClient {
  private readonly baseUrl: string | null;
  private readonly token: string | null;
  private readonly fetchImpl: typeof fetch;
  private readonly requestTimeoutMs: number;

  constructor(opts: ScienceCoreClientOptions = {}) {
    const baseUrl = opts.baseUrl?.trim().replace(/\/$/, "");
    this.baseUrl = baseUrl || null;
    this.token = opts.token?.trim() || null;
    this.fetchImpl = (opts.fetchImpl ?? globalThis.fetch).bind(globalThis);
    this.requestTimeoutMs = opts.requestTimeoutMs ?? 120_000;
  }

  async health(): Promise<ScienceCoreHealth> {
    return this.request<ScienceCoreHealth>("/health");
  }

  async listProjects(options: ListResearchProjectsOptions = {}): Promise<ResearchProject[]> {
    const query = options.includeArchived ? "?includeArchived=true" : "";
    return this.request<ResearchProject[]>(`/v1/projects${query}`, {
      signal: options.signal,
    });
  }

  async createProject(
    input: CreateResearchProjectInput,
    options: CreateResearchProjectOptions = {},
  ): Promise<ResearchProject> {
    return this.request<ResearchProject>("/v1/projects", {
      method: "POST",
      body: JSON.stringify(input),
      signal: options.signal,
    });
  }

  async renameProject(
    projectId: string,
    title: string,
    expectedRowVersion: number,
    options: ProjectMutationOptions,
  ): Promise<ResearchProject> {
    return this.request<ResearchProject>(
      `/v1/projects/${encodeURIComponent(projectId)}`,
      {
        method: "PATCH",
        body: JSON.stringify({ title, expectedRowVersion }),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async archiveProject(
    projectId: string,
    expectedRowVersion: number,
    options: ProjectMutationOptions,
  ): Promise<ResearchProject> {
    return this.projectStateMutation(projectId, "archive", expectedRowVersion, options);
  }

  async restoreProject(
    projectId: string,
    expectedRowVersion: number,
    options: ProjectMutationOptions,
  ): Promise<ResearchProject> {
    return this.projectStateMutation(projectId, "restore", expectedRowVersion, options);
  }

  private async projectStateMutation(
    projectId: string,
    action: "archive" | "restore",
    expectedRowVersion: number,
    options: ProjectMutationOptions,
  ): Promise<ResearchProject> {
    return this.request<ResearchProject>(
      `/v1/projects/${encodeURIComponent(projectId)}/${action}`,
      {
        method: "POST",
        body: JSON.stringify({ expectedRowVersion }),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async listSources(projectId: string): Promise<ResearchSource[]> {
    return this.request<ResearchSource[]>(
      `/v1/projects/${encodeURIComponent(projectId)}/sources`,
    );
  }

  async listScreeningDecisions(
    projectId: string,
    options: ScienceCoreRequestOptions = {},
  ): Promise<ScreeningDecision[]> {
    return this.request<ScreeningDecision[]>(
      `/v1/projects/${encodeURIComponent(projectId)}/screening-decisions`,
      { signal: options.signal },
    );
  }

  async listCandidateTriageDecisions(
    projectId: string,
    options: ScienceCoreRequestOptions = {},
  ): Promise<CandidateTriageDecision[]> {
    return this.request<CandidateTriageDecision[]>(
      `/v1/projects/${encodeURIComponent(projectId)}/candidate-triage-decisions`,
      { signal: options.signal },
    );
  }

  async upsertCandidateTriageDecision(
    projectId: string,
    candidateId: string,
    input: UpsertCandidateTriageDecisionInput,
    options: ScienceCoreRequestOptions = {},
  ): Promise<CandidateTriageDecision> {
    return this.request<CandidateTriageDecision>(
      `/v1/projects/${encodeURIComponent(projectId)}/candidate-triage-decisions/${encodeURIComponent(candidateId)}`,
      {
        method: "PUT",
        body: JSON.stringify(input),
        signal: options.signal,
      },
    );
  }

  async upsertScreeningDecision(
    projectId: string,
    sourceId: string,
    input: UpsertScreeningDecisionInput,
    options: ScienceCoreRequestOptions = {},
  ): Promise<ScreeningDecision> {
    return this.request<ScreeningDecision>(
      `/v1/projects/${encodeURIComponent(projectId)}/screening-decisions/${encodeURIComponent(sourceId)}`,
      {
        method: "PUT",
        body: JSON.stringify(input),
        signal: options.signal,
      },
    );
  }

  async listEvidenceDirectionJudgments(
    projectId: string,
    answerId: string,
    options: ScienceCoreRequestOptions = {},
  ): Promise<EvidenceDirectionJudgment[]> {
    return this.request<EvidenceDirectionJudgment[]>(
      `/v1/projects/${encodeURIComponent(projectId)}/answers/${encodeURIComponent(answerId)}/evidence-directions`,
      { signal: options.signal },
    );
  }

  async upsertEvidenceDirectionJudgment(
    projectId: string,
    answerId: string,
    sourceId: string,
    input: UpsertEvidenceDirectionJudgmentInput,
    options: ScienceCoreRequestOptions = {},
  ): Promise<EvidenceDirectionJudgment> {
    return this.request<EvidenceDirectionJudgment>(
      `/v1/projects/${encodeURIComponent(projectId)}/answers/${encodeURIComponent(answerId)}/evidence-directions/${encodeURIComponent(sourceId)}`,
      {
        method: "PUT",
        body: JSON.stringify(input),
        signal: options.signal,
      },
    );
  }

  async getExtractionMatrix(
    projectId: string,
    options: ScienceCoreRequestOptions = {},
  ): Promise<ExtractionMatrix> {
    return this.request<ExtractionMatrix>(
      `/v1/projects/${encodeURIComponent(projectId)}/extraction`,
      { signal: options.signal },
    );
  }

  async createExtractionColumn(
    projectId: string,
    input: CreateExtractionColumnInput,
    options: ScienceCoreRequestOptions = {},
  ): Promise<ExtractionColumn> {
    return this.request<ExtractionColumn>(
      `/v1/projects/${encodeURIComponent(projectId)}/extraction/columns`,
      { method: "POST", body: JSON.stringify(input), signal: options.signal },
    );
  }

  async upsertExtractionCell(
    projectId: string,
    sourceId: string,
    columnId: string,
    input: UpsertExtractionCellInput,
    options: ScienceCoreRequestOptions = {},
  ): Promise<ExtractionCell> {
    return this.request<ExtractionCell>(
      `/v1/projects/${encodeURIComponent(projectId)}/extraction/cells/${encodeURIComponent(sourceId)}/${encodeURIComponent(columnId)}`,
      { method: "PUT", body: JSON.stringify(input), signal: options.signal },
    );
  }

  async createExactEvidenceSpan(
    projectId: string,
    sourceId: string,
    input: CreateExactEvidenceSpanInput,
    options: ScienceCoreRequestOptions,
  ): Promise<EvidenceSpan> {
    return this.request<EvidenceSpan>(
      `/v1/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(sourceId)}/evidence-spans`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async createConfirmedExtractionCitedBrief(
    projectId: string,
    options: ScienceCoreRequestOptions,
  ): Promise<ResearchWorkflowResult> {
    return this.request<ResearchWorkflowResult>(
      `/v1/projects/${encodeURIComponent(projectId)}/extraction/cited-brief`,
      {
        method: "POST",
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async deleteExtractionCell(
    projectId: string,
    sourceId: string,
    columnId: string,
    expectedVersion: number,
    options: ScienceCoreRequestOptions = {},
  ): Promise<void> {
    await this.fetchResponse(
      `/v1/projects/${encodeURIComponent(projectId)}/extraction/cells/${encodeURIComponent(sourceId)}/${encodeURIComponent(columnId)}`,
      { method: "DELETE", body: JSON.stringify({ expectedVersion }), signal: options.signal },
      true,
    );
  }

  async importPdf(
    projectId: string,
    file: File,
    attachment?: AttachDiscoveryCandidatePdfInput,
  ): Promise<ResearchSource> {
    const body = new FormData();
    body.append("file", file, file.name);
    if (attachment) {
      body.append("workflowId", attachment.workflowId);
      body.append("candidateId", attachment.candidateId);
      body.append("candidateSha256", attachment.candidateSha256);
      body.append("occurrenceInvocationId", attachment.occurrenceInvocationId);
      body.append(
        "confirmIdentityMismatch",
        attachment.confirmIdentityMismatch ? "true" : "false",
      );
    }
    return this.request<ResearchSource>(
      `/v1/projects/${encodeURIComponent(projectId)}/sources`,
      { method: "POST", body },
      false,
    );
  }

  async importCslJsonCandidates(
    projectId: string,
    workflowId: string,
    file: File,
    options: ScienceCoreRequestOptions,
  ): Promise<CslJsonCandidateImport> {
    const body = new FormData();
    body.append("file", file, file.name);
    return this.request<CslJsonCandidateImport>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows/${encodeURIComponent(workflowId)}/discovery/csl-json`,
      {
        method: "POST",
        body,
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
      false,
    );
  }

  async importDataset(projectId: string, file: File): Promise<ResearchSource> {
    const body = new FormData();
    body.append("file", file, file.name);
    return this.request<ResearchSource>(
      `/v1/projects/${encodeURIComponent(projectId)}/datasets`,
      { method: "POST", body },
      false,
    );
  }

  async ask(projectId: string, input: AskResearchQuestionInput): Promise<ResearchAnswer> {
    return this.request<ResearchAnswer>(
      `/v1/projects/${encodeURIComponent(projectId)}/questions`,
      { method: "POST", body: JSON.stringify(input) },
    );
  }

  async prepareAnalysisIntent(
    projectId: string,
    input: PrepareAnalysisIntentInput,
  ): Promise<AnalysisIntent> {
    return this.request<AnalysisIntent>(
      `/v1/projects/${encodeURIComponent(projectId)}/analysis-intents`,
      { method: "POST", body: JSON.stringify(input) },
    );
  }

  async decideAnalysisIntent(
    intentId: string,
    decision: AnalysisIntentDecision,
  ): Promise<AnalysisIntent> {
    return this.request<AnalysisIntent>(
      `/v1/analysis-intents/${encodeURIComponent(intentId)}/decision`,
      { method: "POST", body: JSON.stringify({ decision }) },
    );
  }

  async executeAnalysisIntent(intentId: string): Promise<AnalysisRun> {
    return this.request<AnalysisRun>(
      `/v1/analysis-intents/${encodeURIComponent(intentId)}/execute`,
      { method: "POST" },
    );
  }

  async listAnalysisRuns(projectId: string): Promise<AnalysisRun[]> {
    return this.request<AnalysisRun[]>(
      `/v1/projects/${encodeURIComponent(projectId)}/analysis-runs`,
    );
  }

  async listWorkflows(
    projectId: string,
    options: ResearchWorkflowListOptions = {},
  ): Promise<ResearchWorkflowSnapshot[]> {
    const query = new URLSearchParams();
    if (options.activeOnly != null) query.set("activeOnly", String(options.activeOnly));
    if (options.limit != null) query.set("limit", String(options.limit));
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    return this.request<ResearchWorkflowSnapshot[]>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows${suffix}`,
      { signal: options.signal },
    );
  }

  async listAgentRuns(
    projectId: string,
    options: ResearchWorkflowListOptions = {},
  ): Promise<AgentResearchWorkflowSnapshot[]> {
    const query = new URLSearchParams();
    if (options.activeOnly != null) query.set("activeOnly", String(options.activeOnly));
    if (options.limit != null) query.set("limit", String(options.limit));
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    return this.request<AgentResearchWorkflowSnapshot[]>(
      `/v1/projects/${encodeURIComponent(projectId)}/agent-runs${suffix}`,
      { signal: options.signal },
    );
  }

  async createAgentRun(
    projectId: string,
    input: CreateAgentRunInput,
    options: ScienceCoreRequestOptions,
  ): Promise<AgentResearchWorkflowSnapshot> {
    return this.request<AgentResearchWorkflowSnapshot>(
      `/v1/projects/${encodeURIComponent(projectId)}/agent-runs`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async createDiscoveryRun(
    projectId: string,
    input: CreateDiscoveryRunInput,
    options: ScienceCoreRequestOptions,
  ): Promise<AgentResearchWorkflowSnapshot> {
    return this.request<AgentResearchWorkflowSnapshot>(
      `/v1/projects/${encodeURIComponent(projectId)}/discovery-runs`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async getAgentRun(
    workflowId: string,
    options: ScienceCoreRequestOptions = {},
  ): Promise<AgentResearchWorkflowSnapshot> {
    return this.request<AgentResearchWorkflowSnapshot>(
      `/v1/agent-runs/${encodeURIComponent(workflowId)}`,
      { signal: options.signal },
    );
  }

  async listWorkflowInteractions(
    workflowId: string,
    options: ScienceCoreRequestOptions = {},
  ): Promise<InteractionRequest[]> {
    return this.request<InteractionRequest[]>(
      `/v1/workflows/${encodeURIComponent(workflowId)}/interactions`,
      { signal: options.signal },
    );
  }

  async respondToInteraction(
    interactionId: string,
    input: RespondToInteractionInput,
    options: ScienceCoreRequestOptions,
  ): Promise<AgentResearchWorkflowSnapshot> {
    return this.request<AgentResearchWorkflowSnapshot>(
      `/v1/interactions/${encodeURIComponent(interactionId)}/respond`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async resolveAgentDecision(
    workflowId: string,
    decisionId: string,
    input: ResolveAgentDecisionInput,
    options: ScienceCoreRequestOptions = {},
  ): Promise<AgentResearchWorkflowSnapshot> {
    return this.request<AgentResearchWorkflowSnapshot>(
      `/v1/agent-runs/${encodeURIComponent(workflowId)}/decisions/${encodeURIComponent(decisionId)}/resolve`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
      },
    );
  }

  async createWorkflow(
    projectId: string,
    input: CreateResearchWorkflowInput,
    options: ScienceCoreRequestOptions,
  ): Promise<ResearchWorkflowSnapshot> {
    return this.request<ResearchWorkflowSnapshot>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async getWorkflow(
    workflowId: string,
    options: ScienceCoreRequestOptions = {},
  ): Promise<ResearchWorkflowSnapshot> {
    return this.request<ResearchWorkflowSnapshot>(
      `/v1/workflows/${encodeURIComponent(workflowId)}`,
      { signal: options.signal },
    );
  }

  async getReportDraft(
    projectId: string,
    workflowId: string,
    options: ScienceCoreRequestOptions = {},
  ): Promise<ReportDraftRecord> {
    return this.request<ReportDraftRecord>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows/${encodeURIComponent(workflowId)}/report-draft`,
      { signal: options.signal },
    );
  }

  async createReportDraft(
    projectId: string,
    workflowId: string,
    input: CreateReportDraftInput,
    options: ScienceCoreRequestOptions,
  ): Promise<ReportDraftRecord> {
    return this.request<ReportDraftRecord>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows/${encodeURIComponent(workflowId)}/report-draft`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async saveReportDraft(
    projectId: string,
    workflowId: string,
    draftId: string,
    input: SaveReportDraftInput,
    options: ScienceCoreRequestOptions,
  ): Promise<ReportDraftRecord> {
    return this.request<ReportDraftRecord>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows/${encodeURIComponent(workflowId)}/report-drafts/${encodeURIComponent(draftId)}`,
      {
        method: "PUT",
        body: JSON.stringify(input),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async reviewReportDraft(
    projectId: string,
    workflowId: string,
    draftId: string,
    input: ReviewReportDraftInput,
    options: ScienceCoreRequestOptions,
  ): Promise<ReportDraftRecord> {
    return this.request<ReportDraftRecord>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows/${encodeURIComponent(workflowId)}/report-drafts/${encodeURIComponent(draftId)}/review`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async exportReportDraft(
    projectId: string,
    workflowId: string,
    draftId: string,
    input: ExportReportDraftInput,
    options: ScienceCoreRequestOptions = {},
  ): Promise<ReportDraftExport> {
    return this.request<ReportDraftExport>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows/${encodeURIComponent(workflowId)}/report-drafts/${encodeURIComponent(draftId)}/export`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
      },
    );
  }

  async getWorkflowDiscovery(
    workflowId: string,
    options: WorkflowDiscoveryOptions = {},
  ): Promise<WorkflowDiscoverySnapshot> {
    const query = new URLSearchParams();
    if (options.offset != null) query.set("offset", String(options.offset));
    if (options.limit != null) query.set("limit", String(options.limit));
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    return this.request<WorkflowDiscoverySnapshot>(
      `/v1/workflows/${encodeURIComponent(workflowId)}/discovery${suffix}`,
      { signal: options.signal },
    );
  }

  async getWorkflowEvidenceCoverage(
    workflowId: string,
    options: ScienceCoreRequestOptions = {},
  ): Promise<WorkflowEvidenceCoverage> {
    return this.request<WorkflowEvidenceCoverage>(
      `/v1/workflows/${encodeURIComponent(workflowId)}/evidence-coverage`,
      { signal: options.signal },
    );
  }

  async getResearchMemoryWorkspace(
    projectId: string,
    workflowId: string,
    options: ScienceCoreRequestOptions = {},
  ): Promise<ResearchMemoryWorkspace> {
    return this.request<ResearchMemoryWorkspace>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows/${encodeURIComponent(workflowId)}/research-memory-workspace`,
      { signal: options.signal },
    );
  }

  async createEvidenceMemoryCandidate(
    projectId: string,
    workflowId: string,
    input: CreateEvidenceMemoryCandidateInput,
    options: ScienceCoreRequestOptions,
  ): Promise<CreateEvidenceMemoryCandidateResult> {
    return this.request<CreateEvidenceMemoryCandidateResult>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows/${encodeURIComponent(workflowId)}/research-memory-candidates/from-evidence`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async createSkillCandidate(
    projectId: string,
    workflowId: string,
    input: CreateSkillCandidateInput,
    options: ScienceCoreRequestOptions,
  ): Promise<CreateSkillCandidateResult> {
    return this.request<CreateSkillCandidateResult>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows/${encodeURIComponent(workflowId)}/skill-candidates`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async listSkillCandidates(
    projectId: string,
    workflowId: string,
    options: ScienceCoreRequestOptions = {},
  ): Promise<SkillCandidate[]> {
    return this.request<SkillCandidate[]>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows/${encodeURIComponent(workflowId)}/skill-candidates`,
      { signal: options.signal },
    );
  }

  async getSkillCandidate(
    projectId: string,
    workflowId: string,
    candidateId: string,
    options: ScienceCoreRequestOptions = {},
  ): Promise<SkillCandidate> {
    return this.request<SkillCandidate>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows/${encodeURIComponent(workflowId)}/skill-candidates/${encodeURIComponent(candidateId)}`,
      { signal: options.signal },
    );
  }

  async getSkillActivationPreview(
    projectId: string,
    workflowId: string,
    candidateId: string,
    options: ScienceCoreRequestOptions = {},
  ): Promise<SkillActivationPreview> {
    return this.request<SkillActivationPreview>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows/${encodeURIComponent(workflowId)}/skill-candidates/${encodeURIComponent(candidateId)}/activation-preview`,
      { signal: options.signal },
    );
  }

  async approveSkillActivation(
    projectId: string,
    workflowId: string,
    candidateId: string,
    input: ApproveSkillActivationInput,
    options: ScienceCoreRequestOptions,
  ): Promise<SkillActivation> {
    return this.request<SkillActivation>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows/${encodeURIComponent(workflowId)}/skill-candidates/${encodeURIComponent(candidateId)}/approve-and-activate`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async listSkillActivations(
    projectId: string,
    options: ScienceCoreRequestOptions & { workflowId?: string } = {},
  ): Promise<SkillActivation[]> {
    const query = options.workflowId
      ? `?workflow_id=${encodeURIComponent(options.workflowId)}`
      : "";
    return this.request<SkillActivation[]>(
      `/v1/projects/${encodeURIComponent(projectId)}/skill-activations${query}`,
      { signal: options.signal },
    );
  }

  async getSkillActivation(
    projectId: string,
    activationId: string,
    options: ScienceCoreRequestOptions = {},
  ): Promise<SkillActivation> {
    return this.request<SkillActivation>(
      `/v1/projects/${encodeURIComponent(projectId)}/skill-activations/${encodeURIComponent(activationId)}`,
      { signal: options.signal },
    );
  }

  async rollbackSkillActivation(
    projectId: string,
    activationId: string,
    input: RollbackSkillActivationInput,
    options: ScienceCoreRequestOptions,
  ): Promise<SkillActivation> {
    return this.request<SkillActivation>(
      `/v1/projects/${encodeURIComponent(projectId)}/skill-activations/${encodeURIComponent(activationId)}/rollback`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async invokeActiveRememberVerifiedEvidence(
    projectId: string,
    input: CreateEvidenceMemoryCandidateInput,
    options: ScienceCoreRequestOptions,
  ): Promise<InvokeActiveRememberEvidenceResult> {
    return this.request<InvokeActiveRememberEvidenceResult>(
      `/v1/projects/${encodeURIComponent(projectId)}/active-skill-capabilities/remember-verified-evidence/invoke`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async resolveResearchMemoryCandidate(
    projectId: string,
    workflowId: string,
    memoryId: string,
    input: ResolveResearchMemoryCandidateInput,
    options: ScienceCoreRequestOptions,
  ): Promise<ResearchMemoryRecord> {
    return this.request<ResearchMemoryRecord>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows/${encodeURIComponent(workflowId)}/research-memories/${encodeURIComponent(memoryId)}/resolve`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async invalidateResearchMemory(
    projectId: string,
    workflowId: string,
    memoryId: string,
    input: InvalidateResearchMemoryInput,
    options: ScienceCoreRequestOptions,
  ): Promise<ResearchMemoryRecord> {
    return this.request<ResearchMemoryRecord>(
      `/v1/projects/${encodeURIComponent(projectId)}/workflows/${encodeURIComponent(workflowId)}/research-memories/${encodeURIComponent(memoryId)}/invalidate`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async approveWorkflowPlan(
    workflowId: string,
    input: ApproveResearchWorkflowPlanInput,
    options: ScienceCoreRequestOptions,
  ): Promise<ResearchWorkflowSnapshot> {
    return this.workflowMutation(
      workflowId,
      "approve-plan",
      input,
      options,
    );
  }

  async decideWorkflowAnalysisIntent(
    workflowId: string,
    input: DecideWorkflowAnalysisIntentInput,
    options: ScienceCoreRequestOptions = {},
  ): Promise<ResearchWorkflowSnapshot> {
    const encodedWorkflowId = encodeURIComponent(workflowId);
    const encodedIntentId = encodeURIComponent(input.intentId);
    return this.request<ResearchWorkflowSnapshot>(
      `/v1/workflows/${encodedWorkflowId}/analysis-intents/${encodedIntentId}/decision`,
      {
        method: "POST",
        body: JSON.stringify({
          approvalId: input.approvalId,
          decision: input.decision,
          payloadSha256: input.payloadSha256,
          expectedWorkflowRevision: input.expectedWorkflowRevision,
        }),
        signal: options.signal,
        headers: this.optionalIdempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async acceptWorkflowReviewWarnings(
    workflowId: string,
    input: AcceptWorkflowReviewWarningsInput,
    options: ScienceCoreRequestOptions = {},
  ): Promise<ResearchWorkflowSnapshot> {
    return this.request<ResearchWorkflowSnapshot>(
      `/v1/workflows/${encodeURIComponent(workflowId)}/accept-review-warnings`,
      {
        method: "POST",
        body: JSON.stringify({
          reviewId: input.reviewId,
          reviewInputSha256: input.reviewInputSha256,
          expectedWorkflowRevision: input.expectedWorkflowRevision,
          decision: input.decision,
        }),
        signal: options.signal,
        headers: this.optionalIdempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  async cancelWorkflow(
    workflowId: string,
    input: ResearchWorkflowMutationInput,
    options: ScienceCoreRequestOptions,
  ): Promise<ResearchWorkflowSnapshot> {
    return this.workflowMutation(workflowId, "cancel", input, options);
  }

  async retryWorkflow(
    workflowId: string,
    input: RetryResearchWorkflowInput,
    options: ScienceCoreRequestOptions,
  ): Promise<ResearchWorkflowSnapshot> {
    return this.workflowMutation(workflowId, "retry", input, options);
  }

  async resumeWorkflow(
    workflowId: string,
    input: ResearchWorkflowMutationInput,
    options: ScienceCoreRequestOptions,
  ): Promise<ResearchWorkflowSnapshot> {
    return this.workflowMutation(workflowId, "resume", input, options);
  }

  async listWorkflowEvents(
    workflowId: string,
    options: ResearchWorkflowEventsOptions = {},
  ): Promise<WorkflowEventsPage> {
    const query = new URLSearchParams();
    if (options.after != null) query.set("after", String(options.after));
    if (options.limit != null) query.set("limit", String(options.limit));
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    return this.request<WorkflowEventsPage>(
      `/v1/workflows/${encodeURIComponent(workflowId)}/events${suffix}`,
      { signal: options.signal },
    );
  }

  /** Fetches a source through the authenticated API for use in a blob URL. */
  async fetchSourceBlob(
    sourceId: string,
    options: ScienceCoreRequestOptions = {},
  ): Promise<Blob> {
    return this.requestBlob(
      `/v1/sources/${encodeURIComponent(sourceId)}/file`,
      { signal: options.signal },
    );
  }

  /** Fetches an artifact through the authenticated API for preview/download. */
  async fetchArtifactBlob(
    artifactId: string,
    options: ScienceCoreRequestOptions = {},
  ): Promise<Blob> {
    return this.requestBlob(
      `/v1/artifacts/${encodeURIComponent(artifactId)}/file`,
      { signal: options.signal },
    );
  }

  private workflowMutation<T extends object>(
    workflowId: string,
    action: "approve-plan" | "cancel" | "retry" | "resume",
    input: T,
    options: ScienceCoreRequestOptions,
  ): Promise<ResearchWorkflowSnapshot> {
    return this.request<ResearchWorkflowSnapshot>(
      `/v1/workflows/${encodeURIComponent(workflowId)}/${action}`,
      {
        method: "POST",
        body: JSON.stringify(input),
        signal: options.signal,
        headers: this.idempotencyHeaders(options.idempotencyKey),
      },
    );
  }

  private async request<T>(path: string, init: RequestInit = {}, json = true): Promise<T> {
    const response = await this.fetchResponse(path, init, json);
    return (await response.json()) as T;
  }

  private async requestBlob(path: string, init: RequestInit = {}): Promise<Blob> {
    const response = await this.fetchResponse(path, init, false);
    return response.blob();
  }

  private async fetchResponse(
    path: string,
    init: RequestInit,
    json: boolean,
  ): Promise<Response> {
    const baseUrl = this.requireBaseUrl();
    const token = this.requireToken();
    const controller = new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, this.requestTimeoutMs);
    const abortFromCaller = () => controller.abort(init.signal?.reason);
    if (init.signal?.aborted) abortFromCaller();
    else init.signal?.addEventListener("abort", abortFromCaller, { once: true });
    const headers = new Headers(init.headers);
    if (json && init.body != null) headers.set("Content-Type", "application/json");
    headers.set("Authorization", `Bearer ${token}`);
    try {
      const response = await this.fetchImpl(`${baseUrl}${path}`, {
        ...init,
        headers,
        signal: controller.signal,
      });
      if (!response.ok) throw await this.responseError(response);
      return response;
    } catch (error) {
      if (timedOut) throw new Error("Science core request timed out");
      throw error;
    } finally {
      clearTimeout(timer);
      init.signal?.removeEventListener("abort", abortFromCaller);
    }
  }

  private async responseError(response: Response): Promise<ScienceCoreApiError> {
    let detail: WorkflowApiErrorDetail = {
      code: `http-${response.status}`,
      userMessage: `${response.status} ${response.statusText}`,
      retryable: response.status >= 500,
    };
    try {
      const payload = (await response.json()) as {
        detail?:
          | string
          | Partial<WorkflowApiErrorDetail>
          | Array<{ loc?: unknown; msg?: unknown }>;
      };
      if (typeof payload.detail === "string") {
        detail = { ...detail, userMessage: payload.detail };
      } else if (Array.isArray(payload.detail)) {
        const validationError = payload.detail.find(
          (item) => item && typeof item.msg === "string",
        );
        if (validationError && typeof validationError.msg === "string") {
          const location = Array.isArray(validationError.loc)
            ? validationError.loc
                .filter(
                  (part): part is string | number =>
                    typeof part === "string" || typeof part === "number",
                )
                .filter((part) => part !== "body")
                .join(".")
            : "";
          detail = {
            ...detail,
            code: "validation-error",
            userMessage: location
              ? `${location}: ${validationError.msg}`
              : validationError.msg,
            retryable: false,
          };
        }
      } else if (payload.detail && typeof payload.detail === "object") {
        detail = {
          code:
            typeof payload.detail.code === "string"
              ? payload.detail.code
              : detail.code,
          userMessage:
            typeof payload.detail.userMessage === "string"
              ? payload.detail.userMessage
              : detail.userMessage,
          retryable:
            typeof payload.detail.retryable === "boolean"
              ? payload.detail.retryable
              : detail.retryable,
          details:
            payload.detail.details && typeof payload.detail.details === "object"
              ? payload.detail.details as Record<string, unknown>
              : detail.details,
        };
      }
    } catch {
      // Preserve the status-derived error when the response body is not JSON.
    }
    return new ScienceCoreApiError(response.status, detail);
  }

  private idempotencyHeaders(key: string | undefined): HeadersInit {
    const value = key?.trim();
    if (!value) throw new Error("An idempotency key is required for this request");
    return { "Idempotency-Key": value };
  }

  private optionalIdempotencyHeaders(key: string | undefined): HeadersInit | undefined {
    const value = key?.trim();
    return value ? { "Idempotency-Key": value } : undefined;
  }

  private requireBaseUrl(): string {
    if (!this.baseUrl) {
      throw new Error("Science core URL is not configured");
    }
    return this.baseUrl;
  }

  private requireToken(): string {
    if (!this.token) {
      throw new Error("Science core token is not configured");
    }
    return this.token;
  }
}
