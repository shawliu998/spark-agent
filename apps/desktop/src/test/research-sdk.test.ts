import { describe, expect, it, vi } from "vitest";
import { ScienceCoreApiError, ScienceCoreClient } from "@spark/research-sdk";

function requestHeaders(fetchMock: ReturnType<typeof vi.fn>): Headers {
  const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
  return new Headers(init?.headers);
}

describe("ScienceCoreClient authentication and workflow transport", () => {
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

  it("surfaces structured workflow errors without exposing credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: "workflow-revision-conflict",
            userMessage: "The workflow changed. Refresh and try again.",
            retryable: true,
          },
        }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ),
    );
    const client = new ScienceCoreClient({
      baseUrl: "http://127.0.0.1:42311",
      token: "never-log-this",
      fetchImpl: fetchMock,
    });

    const failure = await client.getWorkflow("workflow-1").catch((error) => error);

    expect(failure).toBeInstanceOf(ScienceCoreApiError);
    expect(failure).toMatchObject({
      status: 409,
      code: "workflow-revision-conflict",
      retryable: true,
      message: "The workflow changed. Refresh and try again.",
    });
    expect(String(failure)).not.toContain("never-log-this");
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
