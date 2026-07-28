import { describe, expect, it, vi } from "vitest";
import {
  ScienceCoreApiError,
  ScienceCoreClient,
  type CreateDiscoveryRunInput,
  type CreateResearchWorkflowInput,
} from "@spark/research-sdk";
import type { WorkflowEvent } from "@spark/research-domain";

function requestHeaders(
  fetchMock: ReturnType<typeof vi.fn>,
  callIndex = 0,
): Headers {
  const init = fetchMock.mock.calls[callIndex]?.[1] as RequestInit | undefined;
  return new Headers(init?.headers);
}

function abortableNeverFetch() {
  return vi.fn(
    (_input: RequestInfo | URL, init?: RequestInit): Promise<Response> =>
      new Promise((_resolve, reject) => {
        const signal = init?.signal;
        const rejectForAbort = () => {
          reject(
            signal?.reason ??
              new DOMException("The operation was aborted", "AbortError"),
          );
        };
        if (signal?.aborted) rejectForAbort();
        else signal?.addEventListener("abort", rejectForAbort, { once: true });
      }),
  );
}

describe("ScienceCoreClient authentication and workflow transport", () => {
  it("types a rejected workflow analysis decision as a durable event", () => {
    const event = {
      id: "event-one",
      sequence: 9,
      type: "analysis.rejected",
      taskId: "task-one",
      jobId: null,
      data: {
        approvalId: "approval-one",
        analysisIntentId: "intent-one",
        taskId: "task-one",
        jobId: null,
        payloadSha256: "d".repeat(64),
        approvalSchemaVersion: "analysis-intent-v3",
        expectedWorkflowRevision: 8,
      },
      createdAt: "2026-07-15T00:00:00Z",
    } satisfies WorkflowEvent;

    expect(event.type).toBe("analysis.rejected");
  });

  it("types requested and answered interactions as distinct event envelopes", () => {
    const requested = {
      id: "event-interaction-requested",
      sequence: 1,
      type: "interaction.requested",
      taskId: null,
      jobId: null,
      data: {
        interactionId: "interaction-1",
        requestType: "text",
        required: true,
        responseId: null,
        responseRevision: null,
        expectedWorkflowRevision: 2,
      },
      createdAt: "2026-07-16T00:00:00Z",
    } satisfies WorkflowEvent;
    const answered = {
      ...requested,
      id: "event-interaction-answered",
      sequence: 2,
      type: "interaction.answered",
      data: {
        ...requested.data,
        responseId: "response-1",
        responseRevision: 1,
      },
    } satisfies WorkflowEvent;

    expect(requested.data.responseId).toBeNull();
    expect(answered.data.responseRevision).toBe(1);
  });

  it("uses the configured dynamic URL, Bearer token, and idempotency key", async () => {
    const fetchMock = vi.fn().mockImplementation(
      async () => new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129/",
      token: "test-token",
      fetchImpl: fetchMock,
    });

    await client.createWorkflow(
      "project one",
      {
        goal: "Compare the imported studies",
        workflowType: "literature-synthesis",
        generationMode: "remote-model-assisted",
        remoteDataApproved: true,
      },
      { idempotencyKey: "workflow-create-1" },
    );

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:43129/v1/projects/project%20one/workflows",
    );
    expect(requestHeaders(fetchMock).get("Authorization")).toBe("Bearer test-token");
    expect(requestHeaders(fetchMock).get("Idempotency-Key")).toBe("workflow-create-1");
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(JSON.parse(String(request?.body))).toEqual({
      goal: "Compare the imported studies",
      workflowType: "literature-synthesis",
      generationMode: "remote-model-assisted",
      remoteDataApproved: true,
    });
    expect(String(fetchMock.mock.calls[0]?.[0])).not.toContain("test-token");
  });

  it("posts candidate lineage with the existing local PDF import", async () => {
    const fetchMock = vi.fn().mockImplementation(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129",
      token: "test-token",
      fetchImpl: fetchMock,
    });
    const file = new File(["%PDF-1.7"], "paper.pdf", {
      type: "application/pdf",
    });

    await client.importPdf("project-1", file, {
      workflowId: "workflow-1",
      candidateId: "candidate-1",
      candidateSha256: "c".repeat(64),
      occurrenceInvocationId: "invocation-1",
    });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    const body = request.body as FormData;
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:43129/v1/projects/project-1/sources",
    );
    expect(body.get("file")).toBeInstanceOf(File);
    expect((body.get("file") as File).name).toBe("paper.pdf");
    expect(body.get("workflowId")).toBe("workflow-1");
    expect(body.get("candidateId")).toBe("candidate-1");
    expect(body.get("candidateSha256")).toBe("c".repeat(64));
    expect(body.get("occurrenceInvocationId")).toBe("invocation-1");
    expect(body.get("confirmIdentityMismatch")).toBe("false");
  });

  it("imports CSL-JSON through the project workflow with auth, idempotency, and abort", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ importedCount: 1, unchangedCount: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129",
      token: "csl-token",
      fetchImpl: fetchMock,
    });
    const controller = new AbortController();
    const file = new File(['[{"title":"Paper"}]'], "zotero.json", {
      type: "application/json",
    });
    await client.importCslJsonCandidates("project/1", "workflow/1", file, {
      idempotencyKey: "csl-import-1",
      signal: controller.signal,
    });
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:43129/v1/projects/project%2F1/workflows/workflow%2F1/discovery/csl-json",
    );
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(request.signal).toBeInstanceOf(AbortSignal);
    expect(requestHeaders(fetchMock).get("Authorization")).toBe("Bearer csl-token");
    expect(requestHeaders(fetchMock).get("Idempotency-Key")).toBe("csl-import-1");
    expect((request.body as FormData).get("file")).toBeInstanceOf(File);
  });

  it("creates a local dataset workflow with its source identity", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({}), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129",
      token: "dataset-token",
      fetchImpl: fetchMock,
    });
    const input = {
      goal: "Summarize distributions and missing values",
      workflowType: "dataset-analysis",
      datasetSourceId: "dataset/one",
      generationMode: "local-deterministic",
      remoteDataApproved: false,
    } satisfies CreateResearchWorkflowInput;

    await client.createWorkflow("project/one", input, {
      idempotencyKey: "dataset-workflow-create-1",
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:43129/v1/projects/project%2Fone/workflows",
    );
    expect(requestHeaders(fetchMock).get("Authorization")).toBe(
      "Bearer dataset-token",
    );
    expect(requestHeaders(fetchMock).get("Idempotency-Key")).toBe(
      "dataset-workflow-create-1",
    );
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(JSON.parse(String(request?.body))).toEqual(input);
  });

  it("creates an abortable idempotent Crossref-only discovery proposal", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({}), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129",
      token: "discovery-token",
      fetchImpl: fetchMock,
    });
    const input = {
      goal: "Which methods evaluate hallucinations?",
      discoverySpec: {
        schemaVersion: "1",
        question: "Which methods evaluate hallucinations?",
        queries: [{
          id: "query-primary",
          query: "Which methods evaluate hallucinations?",
          providers: ["crossref"],
          yearFrom: null,
          yearTo: null,
          sort: "relevance",
          maxResultsPerProvider: 20,
        }],
        stopPolicy: {
          minUniqueCandidates: 20,
          maxAttempts: 1,
          maxConsecutiveNoNovelty: 1,
        },
        downloadOpenAccessPdfs: false,
        maxPdfDownloads: 0,
      },
    } satisfies CreateDiscoveryRunInput;

    await client.createDiscoveryRun("project/one", input, {
      idempotencyKey: "discovery-create-1",
    });

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:43129/v1/projects/project%2Fone/discovery-runs",
    );
    expect(requestHeaders(fetchMock).get("Authorization")).toBe(
      "Bearer discovery-token",
    );
    expect(requestHeaders(fetchMock).get("Idempotency-Key")).toBe(
      "discovery-create-1",
    );
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(request.method).toBe("POST");
    expect(JSON.parse(String(request.body))).toEqual(input);

    const abortFetch = abortableNeverFetch();
    const abortClient = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129",
      token: "discovery-token",
      fetchImpl: abortFetch,
    });
    const controller = new AbortController();
    const pending = abortClient.createDiscoveryRun("project-one", input, {
      idempotencyKey: "discovery-create-2",
      signal: controller.signal,
    });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("serializes a constrained OpenAlex-only discovery proposal", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({}), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129",
      token: "discovery-token",
      fetchImpl: fetchMock,
    });
    const input = {
      goal: "Which methods evaluate hallucinations?",
      discoverySpec: {
        schemaVersion: "1",
        question: "Which methods evaluate hallucinations?",
        queries: [{
          id: "query-openalex",
          query: "Which methods evaluate hallucinations?",
          providers: ["openalex"],
          yearFrom: null,
          yearTo: null,
          sort: "relevance",
          maxResultsPerProvider: 20,
        }],
        stopPolicy: {
          minUniqueCandidates: 20,
          maxAttempts: 1,
          maxConsecutiveNoNovelty: 1,
        },
        downloadOpenAccessPdfs: false,
        maxPdfDownloads: 0,
      },
    } satisfies CreateDiscoveryRunInput;

    await client.createDiscoveryRun("project/one", input, {
      idempotencyKey: "openalex-discovery-create-1",
    });

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(request.method).toBe("POST");
    expect(requestHeaders(fetchMock).get("Idempotency-Key")).toBe(
      "openalex-discovery-create-1",
    );
    expect(JSON.parse(String(request.body))).toEqual(input);
  });

  it("transports autonomous agent runs and revision-bound clarification answers", async () => {
    const fetchMock = vi.fn().mockImplementation(
      async () =>
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129",
      token: "agent-token",
      fetchImpl: fetchMock,
    });

    await client.createAgentRun(
      "project/one",
      {
        goal: "Compare the selected paper and dataset",
        sourceIds: ["paper/one", "dataset/one"],
        mode: "autonomous",
      },
      { idempotencyKey: "agent-create-1" },
    );
    await client.listAgentRuns("project/one", {
      activeOnly: true,
      limit: 100,
    });
    await client.getAgentRun("workflow/one");
    await client.listWorkflowInteractions("workflow/one");
    await client.respondToInteraction(
      "interaction/one",
      { response: ["outcome-a"], expectedWorkflowRevision: 7 },
      { idempotencyKey: "interaction-response-1" },
    );
    await client.resolveAgentDecision(
      "workflow/one",
      "decision/one",
      {
        decision: "approved",
        decisionOutputSha256: "d".repeat(64),
        expectedWorkflowRevision: 8,
      },
    );

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "http://127.0.0.1:43129/v1/projects/project%2Fone/agent-runs",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/agent-runs?activeOnly=true&limit=100",
      "http://127.0.0.1:43129/v1/agent-runs/workflow%2Fone",
      "http://127.0.0.1:43129/v1/workflows/workflow%2Fone/interactions",
      "http://127.0.0.1:43129/v1/interactions/interaction%2Fone/respond",
      "http://127.0.0.1:43129/v1/agent-runs/workflow%2Fone/decisions/decision%2Fone/resolve",
    ]);
    expect(requestHeaders(fetchMock, 0).get("Idempotency-Key")).toBe(
      "agent-create-1",
    );
    expect(requestHeaders(fetchMock, 4).get("Idempotency-Key")).toBe(
      "interaction-response-1",
    );
    const createRequest = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(createRequest.body))).toEqual({
      goal: "Compare the selected paper and dataset",
      sourceIds: ["paper/one", "dataset/one"],
      mode: "autonomous",
    });
    const responseRequest = fetchMock.mock.calls[4]?.[1] as RequestInit;
    expect(JSON.parse(String(responseRequest.body))).toEqual({
      response: ["outcome-a"],
      expectedWorkflowRevision: 7,
    });
    const decisionRequest = fetchMock.mock.calls[5]?.[1] as RequestInit;
    expect(JSON.parse(String(decisionRequest.body))).toEqual({
      decision: "approved",
      decisionOutputSha256: "d".repeat(64),
      expectedWorkflowRevision: 8,
    });
  });

  it("binds workflow analysis decisions to the exact approval and intent", async () => {
    const snapshot = { workflow: { id: "workflow/one", revision: 8 } };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(snapshot), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129",
      token: "decision-token",
      fetchImpl: fetchMock,
    });

    const result = await client.decideWorkflowAnalysisIntent(
      "workflow/one",
      {
        intentId: "intent/one",
        approvalId: "approval-one",
        decision: "approved",
        payloadSha256: "a".repeat(64),
        expectedWorkflowRevision: 7,
      },
      { idempotencyKey: "analysis-decision-1" },
    );

    expect(result).toEqual(snapshot);
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:43129/v1/workflows/workflow%2Fone/analysis-intents/intent%2Fone/decision",
    );
    expect(requestHeaders(fetchMock).get("Authorization")).toBe(
      "Bearer decision-token",
    );
    expect(requestHeaders(fetchMock).get("Idempotency-Key")).toBe(
      "analysis-decision-1",
    );
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(request?.method).toBe("POST");
    expect(JSON.parse(String(request?.body))).toEqual({
      approvalId: "approval-one",
      decision: "approved",
      payloadSha256: "a".repeat(64),
      expectedWorkflowRevision: 7,
    });
  });

  it("binds warning acceptance to the exact review snapshot", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129",
      token: "review-token",
      fetchImpl: fetchMock,
    });
    const input = {
      reviewId: "review/one",
      reviewInputSha256: "b".repeat(64),
      expectedWorkflowRevision: 12,
      decision: "accepted" as const,
    };

    await client.acceptWorkflowReviewWarnings("workflow/one", input);

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:43129/v1/workflows/workflow%2Fone/accept-review-warnings",
    );
    expect(requestHeaders(fetchMock).get("Authorization")).toBe(
      "Bearer review-token",
    );
    expect(requestHeaders(fetchMock).get("Idempotency-Key")).toBeNull();
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    expect(request?.method).toBe("POST");
    expect(JSON.parse(String(request?.body))).toEqual(input);
  });

  it("sends the same exact binding for a repeated workflow intent decision", async () => {
    const fetchMock = vi.fn().mockImplementation(
      async () =>
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129",
      token: "decision-token",
      fetchImpl: fetchMock,
    });
    const input = {
      intentId: "intent-one",
      approvalId: "approval-one",
      decision: "rejected" as const,
      payloadSha256: "c".repeat(64),
      expectedWorkflowRevision: 4,
    };

    await client.decideWorkflowAnalysisIntent("workflow-one", input);
    await client.decideWorkflowAnalysisIntent("workflow-one", input);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1]?.[0]).toBe(fetchMock.mock.calls[0]?.[0]);
    const first = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    const second = fetchMock.mock.calls[1]?.[1] as RequestInit | undefined;
    expect(second?.body).toBe(first?.body);
    expect(JSON.parse(String(second?.body))).toEqual({
      approvalId: "approval-one",
      decision: "rejected",
      payloadSha256: "c".repeat(64),
      expectedWorkflowRevision: 4,
    });
  });

  it("uses Bearer authentication when fetching source blobs", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("pdf", {
        status: 200,
        headers: { "Content-Type": "application/pdf" },
      }),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:52991",
      token: "blob-token",
      fetchImpl: fetchMock,
    });

    const blob = await client.fetchSourceBlob("source/1");

    expect(blob.type).toBe("application/pdf");
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:52991/v1/sources/source%2F1/file",
    );
    expect(requestHeaders(fetchMock).get("Authorization")).toBe("Bearer blob-token");
  });

  it("uses Bearer authentication when fetching artifact blobs", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response("artifact", {
        status: 200,
        headers: { "Content-Type": "application/octet-stream" },
      }),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:52991",
      token: "blob-token",
      fetchImpl: fetchMock,
    });

    await client.fetchArtifactBlob("artifact/1");

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:52991/v1/artifacts/artifact%2F1/file",
    );
    expect(requestHeaders(fetchMock).get("Authorization")).toBe("Bearer blob-token");
  });

  it.each([
    {
      name: "401 string detail",
      response: () =>
        new Response(JSON.stringify({ detail: "Authentication failed" }), {
          status: 401,
          statusText: "Unauthorized",
          headers: { "Content-Type": "application/json" },
        }),
      expected: {
        status: 401,
        code: "http-401",
        retryable: false,
        message: "Authentication failed",
      },
    },
    {
      name: "409 structured detail",
      response: () =>
        new Response(
          JSON.stringify({
            detail: {
              code: "workflow-revision-conflict",
              userMessage: "The workflow changed. Refresh and try again.",
              retryable: true,
            },
          }),
          {
            status: 409,
            statusText: "Conflict",
            headers: { "Content-Type": "application/json" },
          },
        ),
      expected: {
        status: 409,
        code: "workflow-revision-conflict",
        retryable: true,
        message: "The workflow changed. Refresh and try again.",
      },
    },
    {
      name: "422 FastAPI validation detail",
      response: () =>
        new Response(
          JSON.stringify({
            detail: [
              {
                type: "missing",
                loc: ["body", "datasetSourceId"],
                msg: "Field required",
                input: { privateValue: "must-not-be-rendered" },
              },
            ],
          }),
          {
            status: 422,
            statusText: "Unprocessable Entity",
            headers: { "Content-Type": "application/json" },
          },
        ),
      expected: {
        status: 422,
        code: "validation-error",
        retryable: false,
        message: "datasetSourceId: Field required",
      },
    },
    {
      name: "503 non-JSON detail",
      response: () =>
        new Response("Service unavailable", {
          status: 503,
          statusText: "Service Unavailable",
        }),
      expected: {
        status: 503,
        code: "http-503",
        retryable: true,
        message: "503 Service Unavailable",
      },
    },
  ])("maps $name without exposing credentials", async ({ response, expected }) => {
    const fetchMock = vi.fn().mockResolvedValue(response());
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:42311",
      token: "never-log-this",
      fetchImpl: fetchMock,
    });

    const failure = await client.getWorkflow("workflow-1").catch((error) => error);

    expect(failure).toBeInstanceOf(ScienceCoreApiError);
    expect(failure).toMatchObject(expected);
    expect(String(failure)).not.toContain("never-log-this");
    expect(String(failure)).not.toContain("must-not-be-rendered");
  });

  it("turns its own deadline abort into a stable timeout error", async () => {
    vi.useFakeTimers();
    try {
      const fetchMock = abortableNeverFetch();
      const client = new ScienceCoreClient({
        baseUrl: "http://127.0.0.1:42311",
        token: "timeout-token",
        fetchImpl: fetchMock,
        requestTimeoutMs: 25,
      });
      const failure = client
        .decideWorkflowAnalysisIntent("workflow-1", {
          intentId: "intent-one",
          approvalId: "approval-one",
          decision: "approved",
          payloadSha256: "e".repeat(64),
          expectedWorkflowRevision: 3,
        })
        .catch((error) => error);

      await vi.advanceTimersByTimeAsync(26);

      expect(await failure).toMatchObject({
        message: "Science core request timed out",
      });
    } finally {
      vi.useRealTimers();
    }
  });

  it("preserves a caller abort instead of reporting a timeout", async () => {
    const fetchMock = abortableNeverFetch();
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:42311",
      token: "abort-token",
      fetchImpl: fetchMock,
      requestTimeoutMs: 60_000,
    });
    const controller = new AbortController();
    const request = client.acceptWorkflowReviewWarnings(
      "workflow-1",
      {
        reviewId: "review-one",
        reviewInputSha256: "f".repeat(64),
        expectedWorkflowRevision: 5,
        decision: "accepted",
      },
      { signal: controller.signal },
    );
    const rejection = expect(request).rejects.toMatchObject({
      name: "AbortError",
      message: "Caller cancelled",
    });

    controller.abort(new DOMException("Caller cancelled", "AbortError"));

    await rejection;
  });

  it("fails locally when URL or token configuration is missing", async () => {
    const fetchMock = vi.fn();
    const missingUrl = new ScienceCoreClient({ token: "token", fetchImpl: fetchMock });
    const missingToken = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:40000",
      fetchImpl: fetchMock,
    });

    await expect(missingUrl.health()).rejects.toThrow("Science core URL is not configured");
    await expect(missingToken.health()).rejects.toThrow("Science core token is not configured");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("lists and upserts project-scoped screening decisions with auth and abort support", async () => {
    const fetchMock = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129/",
      token: "screening-token",
      fetchImpl: fetchMock,
    });
    const controller = new AbortController();

    await client.listScreeningDecisions("project/one", { signal: controller.signal });
    await client.upsertScreeningDecision(
      "project/one",
      "source one",
      {
        decision: "exclude",
        reason: "Wrong population",
        criteriaVersion: "screening-v1",
        expectedVersion: 2,
      },
      { signal: controller.signal },
    );

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:43129/v1/projects/project%2Fone/screening-decisions",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "http://127.0.0.1:43129/v1/projects/project%2Fone/screening-decisions/source%20one",
    );
    expect(requestHeaders(fetchMock, 0).get("Authorization")).toBe("Bearer screening-token");
    expect(requestHeaders(fetchMock, 1).get("Authorization")).toBe("Bearer screening-token");
    const upsert = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(upsert.method).toBe("PUT");
    expect(upsert.signal).toBeInstanceOf(AbortSignal);
    expect(JSON.parse(String(upsert.body))).toEqual({
      decision: "exclude",
      reason: "Wrong population",
      criteriaVersion: "screening-v1",
      expectedVersion: 2,
    });
  });

  it("lists and upserts non-evidentiary candidate triage decisions", async () => {
    const fetchMock = vi.fn().mockImplementation(async () =>
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129/",
      token: "candidate-token",
      fetchImpl: fetchMock,
    });
    const controller = new AbortController();

    await client.listCandidateTriageDecisions("project/one", {
      signal: controller.signal,
    });
    await client.upsertCandidateTriageDecision(
      "project/one",
      "candidate one",
      {
        decision: "uncertain",
        reason: null,
        criteriaVersion: "candidate-triage-v1",
        expectedVersion: 1,
      },
      { signal: controller.signal },
    );

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:43129/v1/projects/project%2Fone/candidate-triage-decisions",
    );
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "http://127.0.0.1:43129/v1/projects/project%2Fone/candidate-triage-decisions/candidate%20one",
    );
    expect(requestHeaders(fetchMock, 0).get("Authorization")).toBe(
      "Bearer candidate-token",
    );
    const upsert = fetchMock.mock.calls[1]?.[1] as RequestInit;
    expect(upsert.method).toBe("PUT");
    expect(upsert.signal).toBeInstanceOf(AbortSignal);
    expect(JSON.parse(String(upsert.body))).toEqual({
      decision: "uncertain",
      reason: null,
      criteriaVersion: "candidate-triage-v1",
      expectedVersion: 1,
    });
  });

  it("reads a bounded workflow discovery snapshot with encoded identity, query, and caller abort", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({}), {
      status: 200, headers: { "Content-Type": "application/json" },
    }));
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129/", token: "discovery-token", fetchImpl: fetchMock,
    });
    const controller = new AbortController();
    await client.getWorkflowDiscovery("workflow/one", { offset: 0, limit: 50, signal: controller.signal });
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "http://127.0.0.1:43129/v1/workflows/workflow%2Fone/discovery?offset=0&limit=50",
    );
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(request.signal).toBeInstanceOf(AbortSignal);
    expect(requestHeaders(fetchMock).get("Authorization")).toBe("Bearer discovery-token");

    const abortFetch = abortableNeverFetch();
    const abortClient = new ScienceCoreClient({ baseUrl: "http://127.0.0.1:43129", token: "abort-token", fetchImpl: abortFetch });
    const pending = abortClient.getWorkflowDiscovery("workflow-2", { signal: controller.signal });
    controller.abort(new DOMException("Discovery cancelled", "AbortError"));
    await expect(pending).rejects.toMatchObject({ name: "AbortError", message: "Discovery cancelled" });
  });

  it("reads workflow evidence coverage with encoded identity, signal, and typed errors", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({}), { status: 200, headers: { "Content-Type": "application/json" } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: { code: "coverage-invalid", userMessage: "Coverage unavailable", retryable: false } }), { status: 409, headers: { "Content-Type": "application/json" } }));
    const client = new ScienceCoreClient({ baseUrl: "http://127.0.0.1:43129", token: "coverage-token", fetchImpl: fetchMock });
    const controller = new AbortController();
    await client.getWorkflowEvidenceCoverage("workflow/one", { signal: controller.signal });
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://127.0.0.1:43129/v1/workflows/workflow%2Fone/evidence-coverage");
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).signal).toBeInstanceOf(AbortSignal);
    await expect(client.getWorkflowEvidenceCoverage("workflow-two")).rejects.toMatchObject({ status: 409, code: "coverage-invalid", message: "Coverage unavailable" });
  });

  it("transports project-scoped research memory reads and immutable mutations", async () => {
    const fetchMock = vi.fn().mockImplementation(
      async () => new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129/",
      token: "memory-token",
      fetchImpl: fetchMock,
    });
    const controller = new AbortController();

    await client.getResearchMemoryWorkspace("project/one", "workflow/one", {
      signal: controller.signal,
    });
    await client.createEvidenceMemoryCandidate(
      "project/one",
      "workflow/one",
      {
        evidenceId: "evidence/one",
        expectedSourceContentHash: "c".repeat(64),
        expectedQuoteHash: "d".repeat(64),
      },
      { idempotencyKey: "memory-evidence-1", signal: controller.signal },
    );
    await client.createSkillCandidate(
      "project/one",
      "workflow/one",
      {
        memoryId: "memory/one",
        expectedMemoryContentHash: "e".repeat(64),
      },
      { idempotencyKey: "skill-candidate-1", signal: controller.signal },
    );
    await client.listSkillCandidates("project/one", "workflow/one", {
      signal: controller.signal,
    });
    await client.getSkillCandidate(
      "project/one",
      "workflow/one",
      "skill/one",
      { signal: controller.signal },
    );
    await client.resolveResearchMemoryCandidate(
      "project/one",
      "workflow/one",
      "memory/one",
      {
        decision: "accept",
        expectedContentHash: "a".repeat(64),
        expectedStatus: "candidate",
        expectedRevision: 2,
        expectedSubjectHeadId: "memory/one",
        expectedSubjectHeadRevision: 2,
      },
      { idempotencyKey: "memory-accept-1", signal: controller.signal },
    );
    await client.invalidateResearchMemory(
      "project/one",
      "workflow/one",
      "memory/two",
      {
        expectedContentHash: "b".repeat(64),
        expectedStatus: "committed",
        expectedRevision: 1,
        expectedSubjectHeadId: "memory/two",
        expectedSubjectHeadRevision: 1,
      },
      { idempotencyKey: "memory-invalidate-1", signal: controller.signal },
    );

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "http://127.0.0.1:43129/v1/projects/project%2Fone/workflows/workflow%2Fone/research-memory-workspace",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/workflows/workflow%2Fone/research-memory-candidates/from-evidence",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/workflows/workflow%2Fone/skill-candidates",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/workflows/workflow%2Fone/skill-candidates",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/workflows/workflow%2Fone/skill-candidates/skill%2Fone",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/workflows/workflow%2Fone/research-memories/memory%2Fone/resolve",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/workflows/workflow%2Fone/research-memories/memory%2Ftwo/invalidate",
    ]);
    expect(requestHeaders(fetchMock, 0).get("Authorization")).toBe(
      "Bearer memory-token",
    );
    expect(
      (fetchMock.mock.calls[0]?.[1] as RequestInit).signal,
    ).toBeInstanceOf(AbortSignal);
    expect(
      [1, 2, 5, 6].map((index) =>
        requestHeaders(fetchMock, index).get("Idempotency-Key"),
      ),
    ).toEqual([
      "memory-evidence-1",
      "skill-candidate-1",
      "memory-accept-1",
      "memory-invalidate-1",
    ]);
    expect(
      JSON.parse(String((fetchMock.mock.calls[5]?.[1] as RequestInit).body)),
    ).toEqual({
      decision: "accept",
      expectedContentHash: "a".repeat(64),
      expectedStatus: "candidate",
      expectedRevision: 2,
      expectedSubjectHeadId: "memory/one",
      expectedSubjectHeadRevision: 2,
    });

    const abortFetch = abortableNeverFetch();
    const abortClient = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129",
      token: "memory-token",
      fetchImpl: abortFetch,
    });
    const abortController = new AbortController();
    const pending = abortClient.getResearchMemoryWorkspace(
      "project-one",
      "workflow-one",
      { signal: abortController.signal },
    );
    abortController.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("transports exact project Skill activation, rollback, and active invoke contracts", async () => {
    const fetchMock = vi.fn().mockImplementation(
      async () => new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129/",
      token: "activation-token",
      fetchImpl: fetchMock,
    });
    const controller = new AbortController();
    const approval = {
      expectedStatus: "awaiting-approval" as const,
      expectedCandidateContentHash: "a".repeat(64),
      expectedTemplateSha256: "b".repeat(64),
      expectedEvaluationSha256: "c".repeat(64),
      expectedApprovalSha256: "d".repeat(64),
      expectedPriorPresent: false,
      expectedPriorSha256: null,
      expectedTargetDirectoryPresent: false,
    };
    const rollback = {
      expectedStatus: "active" as const,
      expectedActivationId: "activation/one",
      expectedApprovalSha256: "d".repeat(64),
      expectedInstalledSha256: "b".repeat(64),
      expectedCurrentTargetSha256: "b".repeat(64),
    };

    await client.getSkillActivationPreview(
      "project/one",
      "workflow/one",
      "candidate/one",
      { signal: controller.signal },
    );
    await client.approveSkillActivation(
      "project/one",
      "workflow/one",
      "candidate/one",
      approval,
      { idempotencyKey: "activate-1", signal: controller.signal },
    );
    await client.listSkillActivations("project/one", {
      workflowId: "workflow/one",
      signal: controller.signal,
    });
    await client.getSkillActivation(
      "project/one",
      "activation/one",
      { signal: controller.signal },
    );
    await client.rollbackSkillActivation(
      "project/one",
      "activation/one",
      rollback,
      { idempotencyKey: "rollback-1", signal: controller.signal },
    );
    await client.invokeActiveRememberVerifiedEvidence(
      "project/one",
      {
        evidenceId: "evidence/one",
        expectedSourceContentHash: "e".repeat(64),
        expectedQuoteHash: "f".repeat(64),
      },
      { idempotencyKey: "invoke-1", signal: controller.signal },
    );

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "http://127.0.0.1:43129/v1/projects/project%2Fone/workflows/workflow%2Fone/skill-candidates/candidate%2Fone/activation-preview",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/workflows/workflow%2Fone/skill-candidates/candidate%2Fone/approve-and-activate",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/skill-activations?workflow_id=workflow%2Fone",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/skill-activations/activation%2Fone",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/skill-activations/activation%2Fone/rollback",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/active-skill-capabilities/remember-verified-evidence/invoke",
    ]);
    expect(
      [1, 4, 5].map((index) =>
        requestHeaders(fetchMock, index).get("Idempotency-Key"),
      ),
    ).toEqual(["activate-1", "rollback-1", "invoke-1"]);
    expect(
      JSON.parse(String((fetchMock.mock.calls[1]?.[1] as RequestInit).body)),
    ).toEqual(approval);
    expect(
      JSON.parse(String((fetchMock.mock.calls[4]?.[1] as RequestInit).body)),
    ).toEqual(rollback);
    expect(
      (fetchMock.mock.calls[0]?.[1] as RequestInit).signal,
    ).toBeInstanceOf(AbortSignal);
    expect(requestHeaders(fetchMock, 5).get("Authorization")).toBe(
      "Bearer activation-token",
    );
  });

  it("transports project-scoped extraction matrix mutations with encoded identities", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response(
      JSON.stringify({ columns: [], cells: [] }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ));
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:43129/", token: "extraction-token", fetchImpl: fetchMock,
    });
    const controller = new AbortController();
    await client.getExtractionMatrix("project/one", { signal: controller.signal });
    await client.createExtractionColumn("project/one", { name: "Design" }, { signal: controller.signal });
    await client.upsertExtractionCell("project/one", "source one", "column/two", {
      value: "Adults", reviewStatus: "confirmed", evidenceIds: ["evidence-1"], expectedVersion: 3,
    }, { signal: controller.signal });
    await client.createExactEvidenceSpan("project/one", "source one", {
      pageIndex: 2,
      quoteText: "An exact local quote that is long enough.",
      expectedSourceContentHash: "a".repeat(64),
      expectedPageManifestHash: "b".repeat(64),
    }, { signal: controller.signal, idempotencyKey: "evidence-request-1" });
    await client.createConfirmedExtractionCitedBrief("project/one", {
      signal: controller.signal,
      idempotencyKey: "brief-request-1",
    });
    await client.deleteExtractionCell("project/one", "source one", "column/two", 4, { signal: controller.signal });
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "http://127.0.0.1:43129/v1/projects/project%2Fone/extraction",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/extraction/columns",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/extraction/cells/source%20one/column%2Ftwo",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/sources/source%20one/evidence-spans",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/extraction/cited-brief",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/extraction/cells/source%20one/column%2Ftwo",
    ]);
    const create = fetchMock.mock.calls[1]?.[1] as RequestInit;
    const upsert = fetchMock.mock.calls[2]?.[1] as RequestInit;
    const evidence = fetchMock.mock.calls[3]?.[1] as RequestInit;
    const brief = fetchMock.mock.calls[4]?.[1] as RequestInit;
    const remove = fetchMock.mock.calls[5]?.[1] as RequestInit;
    expect(create.method).toBe("POST");
    expect(upsert.method).toBe("PUT");
    expect(evidence.method).toBe("POST");
    expect(new Headers(evidence.headers).get("Idempotency-Key")).toBe("evidence-request-1");
    expect(brief.method).toBe("POST");
    expect(new Headers(brief.headers).get("Idempotency-Key")).toBe("brief-request-1");
    expect(remove.method).toBe("DELETE");
    expect(JSON.parse(String(remove.body))).toEqual({ expectedVersion: 4 });
    expect(requestHeaders(fetchMock, 5).get("Authorization")).toBe("Bearer extraction-token");
  });
});
