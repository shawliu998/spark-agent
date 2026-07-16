import { describe, expect, it, vi } from "vitest";
import {
  ScienceCoreApiError,
  ScienceCoreClient,
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
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({}), {
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

    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "http://127.0.0.1:43129/v1/projects/project%2Fone/agent-runs",
      "http://127.0.0.1:43129/v1/projects/project%2Fone/agent-runs?activeOnly=true&limit=100",
      "http://127.0.0.1:43129/v1/agent-runs/workflow%2Fone",
      "http://127.0.0.1:43129/v1/workflows/workflow%2Fone/interactions",
      "http://127.0.0.1:43129/v1/interactions/interaction%2Fone/respond",
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
});
