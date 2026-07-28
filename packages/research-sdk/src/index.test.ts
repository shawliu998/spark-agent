import { describe, expect, it, vi } from "vitest";
import { ScienceCoreClient } from "./index";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ScienceCoreClient project mutations", () => {
  it("encodes archived listing, rename body, and idempotency headers", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({
      id: "project-1",
      title: "Renamed",
      rowVersion: 2,
      archivedAt: null,
    }));
    const client = new ScienceCoreClient({
      baseUrl: "http://science.test",
      token: "token",
      fetchImpl,
    });

    await client.listProjects({ includeArchived: true });
    await client.renameProject("project-1", "Renamed", 1, {
      idempotencyKey: "project-rename-0001",
    });

    expect(fetchImpl.mock.calls[0]?.[0]).toBe("http://science.test/v1/projects?includeArchived=true");
    expect(fetchImpl.mock.calls[1]?.[0]).toBe("http://science.test/v1/projects/project-1");
    expect(fetchImpl.mock.calls[1]?.[1]).toMatchObject({
      method: "PATCH",
      body: JSON.stringify({ title: "Renamed", expectedRowVersion: 1 }),
    });
    expect(new Headers(fetchImpl.mock.calls[1]?.[1]?.headers).get("Idempotency-Key")).toBe(
      "project-rename-0001",
    );
  });

  it("passes caller abort through to a project archive request", async () => {
    let requestSignal: AbortSignal | undefined;
    const fetchImpl = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      requestSignal = init?.signal;
      return new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener(
          "abort",
          () => reject(new DOMException("The request was aborted", "AbortError")),
          { once: true },
        );
      });
    });
    const client = new ScienceCoreClient({
      baseUrl: "http://science.test",
      token: "token",
      fetchImpl,
      requestTimeoutMs: 10_000,
    });
    const controller = new AbortController();
    const request = client.archiveProject("project-1", 1, {
      idempotencyKey: "project-archive-0001",
      signal: controller.signal,
    });
    await vi.waitFor(() => expect(requestSignal).toBeInstanceOf(AbortSignal));
    controller.abort();
    await expect(request).rejects.toMatchObject({ name: "AbortError" });
    expect(requestSignal?.aborted).toBe(true);
  });

  it("preserves structured project mutation conflicts", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({
      detail: {
        code: "project-idempotency-stale",
        userMessage: "The project changed after this mutation. Reload it before retrying.",
        retryable: false,
      },
    }, 409));
    const client = new ScienceCoreClient({
      baseUrl: "http://science.test",
      token: "token",
      fetchImpl,
    });

    await expect(
      client.restoreProject("project-1", 2, {
        idempotencyKey: "project-restore-0001",
      }),
    ).rejects.toMatchObject({
      name: "ScienceCoreApiError",
      status: 409,
      code: "project-idempotency-stale",
      retryable: false,
    });
  });
});

describe("ScienceCoreClient report drafts", () => {
  it("keeps project/workflow ownership, CAS bodies, and idempotency headers exact", async () => {
    const draft = {
      id: "draft/1",
      projectId: "project/1",
      workflowId: "workflow/1",
      schemaVersion: "1",
      revision: 1,
      contentMarkdown: "# Report\n",
      contentSha256: "a".repeat(64),
      baseWorkflowSha256: "b".repeat(64),
      baseResultSha256: "c".repeat(64),
      baseEvidenceSha256: "d".repeat(64),
      status: "draft",
      createdAt: "2026-07-24T00:00:00Z",
      updatedAt: "2026-07-24T00:00:00Z",
    } as const;
    const fetchImpl = vi.fn(async () => jsonResponse(draft));
    const client = new ScienceCoreClient({
      baseUrl: "http://science.test",
      token: "token",
      fetchImpl,
    });

    await client.getReportDraft("project/1", "workflow/1");
    await client.createReportDraft(
      "project/1",
      "workflow/1",
      { schemaVersion: "1" },
      { idempotencyKey: "report-create-0001" },
    );
    await client.saveReportDraft(
      "project/1",
      "workflow/1",
      "draft/1",
      {
        expectedRevision: 1,
        expectedContentSha256: "a".repeat(64),
        contentMarkdown: "# Edited report\n",
      },
      { idempotencyKey: "report-save-0001" },
    );
    await client.reviewReportDraft(
      "project/1",
      "workflow/1",
      "draft/1",
      {
        expectedRevision: 2,
        expectedContentSha256: "e".repeat(64),
        citationRebases: [{
          previousEvidenceId: "evidence-old",
          previousQuoteHash: "1".repeat(64),
          currentEvidenceId: "evidence-current",
          currentQuoteHash: "2".repeat(64),
        }],
      },
      { idempotencyKey: "report-review-0001" },
    );
    await client.exportReportDraft(
      "project/1",
      "workflow/1",
      "draft/1",
      {
        expectedRevision: 3,
        expectedContentSha256: "f".repeat(64),
      },
    );

    const base =
      "http://science.test/v1/projects/project%2F1/workflows/workflow%2F1";
    expect(fetchImpl.mock.calls.map((call) => call[0])).toEqual([
      `${base}/report-draft`,
      `${base}/report-draft`,
      `${base}/report-drafts/draft%2F1`,
      `${base}/report-drafts/draft%2F1/review`,
      `${base}/report-drafts/draft%2F1/export`,
    ]);
    expect(fetchImpl.mock.calls[1]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ schemaVersion: "1" }),
    });
    expect(
      new Headers(fetchImpl.mock.calls[1]?.[1]?.headers).get("Idempotency-Key"),
    ).toBe("report-create-0001");
    expect(fetchImpl.mock.calls[2]?.[1]).toMatchObject({
      method: "PUT",
      body: JSON.stringify({
        expectedRevision: 1,
        expectedContentSha256: "a".repeat(64),
        contentMarkdown: "# Edited report\n",
      }),
    });
    expect(
      new Headers(fetchImpl.mock.calls[2]?.[1]?.headers).get("Idempotency-Key"),
    ).toBe("report-save-0001");
    expect(
      new Headers(fetchImpl.mock.calls[3]?.[1]?.headers).get("Idempotency-Key"),
    ).toBe("report-review-0001");
    expect(fetchImpl.mock.calls[3]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        expectedRevision: 2,
        expectedContentSha256: "e".repeat(64),
        citationRebases: [{
          previousEvidenceId: "evidence-old",
          previousQuoteHash: "1".repeat(64),
          currentEvidenceId: "evidence-current",
          currentQuoteHash: "2".repeat(64),
        }],
      }),
    });
    expect(fetchImpl.mock.calls[4]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({
        expectedRevision: 3,
        expectedContentSha256: "f".repeat(64),
      }),
    });
  });
});

describe("ScienceCoreClient evidence directions", () => {
  it("encodes answer/source identity and optimistic version bodies", async () => {
    const judgment = {
      id: "judgment-1",
      projectId: "project/1",
      answerId: "answer/1",
      sourceId: "source/1",
      direction: "supporting",
      rowVersion: 1,
      createdAt: "2026-07-26T00:00:00Z",
      updatedAt: "2026-07-26T00:00:00Z",
    } as const;
    const fetchImpl = vi.fn(async () => jsonResponse(judgment));
    const client = new ScienceCoreClient({
      baseUrl: "http://science.test",
      token: "token",
      fetchImpl,
    });

    await client.listEvidenceDirectionJudgments("project/1", "answer/1");
    await client.upsertEvidenceDirectionJudgment(
      "project/1",
      "answer/1",
      "source/1",
      { direction: "mixed", expectedVersion: 1 },
    );

    const base =
      "http://science.test/v1/projects/project%2F1/answers/answer%2F1/evidence-directions";
    expect(fetchImpl.mock.calls[0]?.[0]).toBe(base);
    expect(fetchImpl.mock.calls[1]?.[0]).toBe(`${base}/source%2F1`);
    expect(fetchImpl.mock.calls[1]?.[1]).toMatchObject({
      method: "PUT",
      body: JSON.stringify({ direction: "mixed", expectedVersion: 1 }),
    });
  });
});
