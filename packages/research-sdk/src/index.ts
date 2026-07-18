import type {
  AcceptWorkflowReviewWarningsInput,
  AgentResearchWorkflowSnapshot,
  AnalysisIntent,
  AnalysisRun,
  CreateAgentRunInput,
  CreateResearchWorkflowInput as DomainCreateResearchWorkflowInput,
  DecideWorkflowAnalysisIntentInput,
  InteractionRequest,
  RespondToInteractionInput,
  ResearchAnswer,
  ResearchProject,
  ResearchSource,
  ResearchWorkflowSnapshot,
  ScienceCoreHealth,
  WorkflowAnalysisIntentDecision,
  WorkflowApiErrorDetail,
  WorkflowEventsPage,
} from "@spark/research-domain";

export type {
  AcceptWorkflowReviewWarningsInput,
  AgentResearchWorkflowSnapshot,
  CreateAgentRunInput,
  DecideWorkflowAnalysisIntentInput,
  InteractionRequest,
  InteractionResponseValue,
  IntentDecision,
  RespondToInteractionInput,
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

  constructor(status: number, detail: WorkflowApiErrorDetail) {
    super(detail.userMessage);
    this.name = "ScienceCoreApiError";
    this.status = status;
    this.code = detail.code;
    this.retryable = detail.retryable;
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

  async listProjects(): Promise<ResearchProject[]> {
    return this.request<ResearchProject[]>("/v1/projects");
  }

  async createProject(input: CreateResearchProjectInput): Promise<ResearchProject> {
    return this.request<ResearchProject>("/v1/projects", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  async listSources(projectId: string): Promise<ResearchSource[]> {
    return this.request<ResearchSource[]>(
      `/v1/projects/${encodeURIComponent(projectId)}/sources`,
    );
  }

  async importPdf(projectId: string, file: File): Promise<ResearchSource> {
    const body = new FormData();
    body.append("file", file, file.name);
    return this.request<ResearchSource>(
      `/v1/projects/${encodeURIComponent(projectId)}/sources`,
      { method: "POST", body },
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
