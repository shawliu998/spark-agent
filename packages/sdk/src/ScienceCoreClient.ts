import type {
  AnalysisIntent,
  AnalysisRun,
  ResearchAnswer,
  ResearchProject,
  ResearchSource,
  ScienceCoreHealth,
} from "@ai4s/shared";

export const DEFAULT_SCIENCE_CORE_URL = "http://127.0.0.1:8765";

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

export interface PrepareAnalysisIntentInput {
  datasetSourceId: string;
  objective: string;
  code: string;
}

export type AnalysisIntentDecision = "approved" | "rejected";

/**
 * Typed boundary for the local Python science-core service. The desktop never
 * reaches PaperQA, SQLite, or Jupyter directly; those implementations remain
 * replaceable behind this client.
 */
export class ScienceCoreClient {
  private readonly baseUrl: string;
  private readonly token: string | null;
  private readonly fetchImpl: typeof fetch;
  private readonly requestTimeoutMs: number;

  constructor(opts: ScienceCoreClientOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? DEFAULT_SCIENCE_CORE_URL).replace(/\/$/, "");
    this.token = opts.token ?? null;
    this.fetchImpl = (opts.fetchImpl ?? globalThis.fetch).bind(globalThis);
    this.requestTimeoutMs = opts.requestTimeoutMs ?? 120_000;
  }

  sourceFileUrl(sourceId: string, pageIndex?: number): string {
    const page = pageIndex == null ? "" : `#page=${pageIndex + 1}`;
    return `${this.baseUrl}/v1/sources/${encodeURIComponent(sourceId)}/file${page}`;
  }

  artifactFileUrl(artifactId: string): string {
    return `${this.baseUrl}/v1/artifacts/${encodeURIComponent(artifactId)}/file`;
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

  async ask(
    projectId: string,
    input: AskResearchQuestionInput,
  ): Promise<ResearchAnswer> {
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

  private async request<T>(path: string, init: RequestInit = {}, json = true): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.requestTimeoutMs);
    const headers = new Headers(init.headers);
    if (json && init.body != null) headers.set("Content-Type", "application/json");
    if (this.token) headers.set("Authorization", `Bearer ${this.token}`);
    try {
      const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        ...init,
        headers,
        signal: controller.signal,
      });
      if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`;
        try {
          const payload = (await response.json()) as { detail?: string };
          if (payload.detail) detail = payload.detail;
        } catch {
          // Keep the HTTP status when the service did not return JSON.
        }
        throw new Error(detail);
      }
      return (await response.json()) as T;
    } catch (error) {
      if (controller.signal.aborted) throw new Error("Science core request timed out");
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }
}
