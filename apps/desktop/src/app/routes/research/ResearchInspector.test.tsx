import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WorkflowEvent } from "@spark/research-domain";
import { ResearchInspector } from "./ResearchInspector";

vi.mock("./useSourcePdfBlob", () => ({
  useSourcePdfBlob: () => ({ url: null, loading: false, error: null }),
}));

describe("ResearchInspector", () => {
  it("acts as a labelled focus-contained dialog in compact layouts", async () => {
    render(
      <ResearchInspector
        modal
        activeTab="evidence"
        onTabChange={vi.fn()}
        onClose={vi.fn()}
        selectedSource={null}
        selection={null}
        review={null}
        events={[]}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "Research inspector" });
    const activeTab = screen.getByRole("tab", { name: "Evidence" });
    const closeButton = screen.getByRole("button", { name: "Close inspector" });
    await waitFor(() => expect(activeTab).toHaveFocus());

    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(closeButton).toHaveFocus();
  });

  it("explains how evidence enters the inspector before a source is selected", () => {
    render(
      <ResearchInspector
        activeTab="evidence"
        onTabChange={vi.fn()}
        selectedSource={null}
        selection={null}
        review={null}
        events={[]}
      />,
    );

    expect(screen.getByText("No evidence selected")).toBeInTheDocument();
    expect(
      screen.getByText("Citation selection jumps to the exact evidence page."),
    ).toBeInTheDocument();
  });

  it("supports roving keyboard focus across inspector tabs", () => {
    const onTabChange = vi.fn();
    render(
      <ResearchInspector
        activeTab="evidence"
        onTabChange={onTabChange}
        selectedSource={null}
        selection={null}
        review={null}
        events={[]}
      />,
    );

    const evidenceTab = screen.getByRole("tab", { name: "Evidence" });
    const reviewTab = screen.getByRole("tab", { name: "Review" });
    expect(evidenceTab).toHaveAttribute(
      "aria-controls",
      "research-inspector-panel-evidence",
    );
    expect(screen.getByRole("tabpanel")).toHaveAttribute(
      "aria-labelledby",
      "research-inspector-tab-evidence",
    );

    evidenceTab.focus();
    fireEvent.keyDown(evidenceTab, { key: "ArrowRight" });
    expect(onTabChange).toHaveBeenCalledWith("review");
    expect(reviewTab).toHaveFocus();
  });

  it("keeps the selected passage and its source-integrity boundary above the PDF", () => {
    render(
      <ResearchInspector
        activeTab="evidence"
        onTabChange={vi.fn()}
        selectedSource={{
          id: "source-1",
          projectId: "project-1",
          title: "A deliberately long imported paper title for evidence inspection",
          sourceKind: "pdf",
          authors: [],
          doi: null,
          arxivId: null,
          localPath: "/workspace/paper.pdf",
          publicationDate: null,
          ingestionStatus: "ready",
          contentHash: "a".repeat(64),
          pageCount: 12,
          createdAt: "2026-07-20T08:00:00Z",
        }}
        selection={{
          sourceId: "source-1",
          pageIndex: 3,
          evidenceId: "evidence-1",
          evidence: {
            text: "The measured outcome decreased after the intervention.",
            pageLabel: "4",
            quoteHash: "b".repeat(64),
            extractionMethod: "local-page-search",
            confidence: 1,
            verified: true,
            relationship: "supporting",
            sourceContentHash: "a".repeat(64),
            sourcePageManifestHash: "c".repeat(64),
          },
        }}
        review={null}
        events={[]}
      />,
    );

    expect(screen.getByText("Supports")).toBeInTheDocument();
    expect(
      screen.getByText("The measured outcome decreased after the intervention."),
    ).toBeInTheDocument();
    expect(screen.getByText("Source hash matches cited result")).toBeInTheDocument();
    expect(screen.getByText("Source document preview unavailable")).toBeInTheDocument();
    expect(screen.queryByText("No evidence selected")).not.toBeInTheDocument();
    expect(screen.queryByText(/100%/)).not.toBeInTheDocument();
  });

  it("shows the frozen claim–evidence matrix and opens its exact citation", () => {
    const onSelectEvidence = vi.fn();
    const evidence = {
      evidenceId: "evidence-1",
      sourceId: "source-1",
      sourceTitle: "Frozen imported study",
      sourceContentHash: "a".repeat(64),
      sourcePageManifestHash: "b".repeat(64),
      pageIndex: 3,
      pageLabel: "4",
      text: "The measured outcome decreased after the intervention.",
      bbox: null,
      coordinateSpace: "normalized-rotated-top-left-v1" as const,
      quoteHash: "c".repeat(64),
      extractionMethod: "local-page-search",
      confidence: 1,
      verified: true,
      relationship: "supporting" as const,
    };
    const result = {
      answerId: "answer-1",
      summary: "A result summary.",
      generator: "local-extractive-v1",
      model: null,
      promptVersion: "local-extractive-v1",
      integrityStatus: "verified-frozen-v2" as const,
      claims: [
        {
          id: "claim-1",
          statement: "The reported outcome moved in the same direction.",
          supportStatus: "supported" as const,
          confidence: 0.73,
          evidence: [evidence],
        },
      ],
      unresolvedQuestions: ["Does the finding generalize to other populations?"],
    };

    render(
      <ResearchInspector
        activeTab="review"
        onTabChange={vi.fn()}
        selectedSource={null}
        selection={null}
        result={result}
        review={{
          id: "review-1",
          reviewType: "deterministic-claims-v2",
          verdict: "passed",
          inputSha256: "d".repeat(64),
          result: {
            schemaVersion: "2",
            verdict: "passed",
            checks: [],
            claimResults: [],
            requiredRevisions: [],
            resultSnapshotSha256: "e".repeat(64),
            resultSnapshot: result,
          },
          createdAt: "2026-07-20T08:00:00Z",
        }}
        onSelectEvidence={onSelectEvidence}
        events={[]}
      />,
    );

    expect(screen.getByText("Claim–evidence review")).toBeInTheDocument();
    expect(screen.getByText("Claims from the frozen reviewed result")).toBeInTheDocument();
    expect(screen.getByText("The reported outcome moved in the same direction.")).toBeInTheDocument();
    expect(screen.getByText("Does the finding generalize to other populations?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open Frozen imported study, page 4" }));
    expect(onSelectEvidence).toHaveBeenCalledWith(evidence);
  });

  it("shows an honest analysis stage and elapsed seconds without inventing a percentage", () => {
    const event: WorkflowEvent = {
      id: "event-progress-1",
      sequence: 12,
      type: "analysis.run-progress",
      taskId: "task-1",
      jobId: "job-1",
      data: {
        analysisIntentId: "intent-1",
        runId: "run-1",
        taskId: "task-1",
        jobId: "job-1",
        stage: "executing-runtime",
        elapsedSeconds: 1.25,
      },
      createdAt: "2026-07-15T08:00:01Z",
    };

    render(
      <ResearchInspector
        activeTab="activity"
        onTabChange={vi.fn()}
        selectedSource={null}
        selection={null}
        review={null}
        events={[event]}
      />,
    );

    expect(
      screen.getByText("Analysis executing runtime · 1.3 seconds elapsed"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it("keeps a decided plan approval envelope auditable in activity", () => {
    const event: WorkflowEvent = {
      id: "event-1",
      sequence: 4,
      type: "approval.requested",
      taskId: null,
      jobId: "job-1",
      data: {
        approvalId: "approval-1",
        subjectType: "plan",
        subjectId: "plan-1",
        action: "approve-research-plan",
        payloadSha256: "a".repeat(64),
        riskLevel: "medium",
        reason: "Approve the source-bound research plan.",
        affectedResources: ["source:source-1:sha256:abc:verified-passages:remote"],
        approvalSchemaVersion: "workflow-plan-approval-v2",
      },
      createdAt: "2026-07-14T08:00:00Z",
    };

    render(
      <ResearchInspector
        activeTab="activity"
        onTabChange={vi.fn()}
        selectedSource={null}
        selection={null}
        review={null}
        events={[event]}
      />,
    );

    expect(screen.getByText("Approval requested: approve research plan")).toBeInTheDocument();
    expect(screen.getByText("Event 4")).toBeInTheDocument();
    expect(screen.getByText("Audit details")).toBeInTheDocument();
    expect(screen.getByText("Risk: medium")).toBeInTheDocument();
    expect(screen.getByText("Reason: Approve the source-bound research plan.")).toBeInTheDocument();
    expect(
      screen.getByText("Resource: source:source-1:sha256:abc:verified-passages:remote"),
    ).toBeInTheDocument();
    expect(screen.getByText("Schema: workflow-plan-approval-v2")).toBeInTheDocument();
    expect(screen.getByText(`Approval envelope: ${"a".repeat(64)}`)).toBeInTheDocument();
  });

  it("keeps retry-only recovery events neutral until canonical status resumes", () => {
    const failed: WorkflowEvent = {
      id: "event-failed",
      sequence: 7,
      type: "job.failed",
      taskId: "task-1",
      jobId: "job-1",
      data: {
        jobId: "job-1",
        kind: "execute-analysis",
        attempt: 1,
        errorCode: "runtime-timeout",
      },
      createdAt: "2026-07-20T08:00:07Z",
    };
    const retried: WorkflowEvent = {
      id: "event-retried",
      sequence: 8,
      type: "job.retried",
      taskId: "task-1",
      jobId: "job-1",
      data: {
        jobId: "job-1",
        kind: "execute-analysis",
        attempt: 2,
        errorCode: null,
      },
      createdAt: "2026-07-20T08:00:08Z",
    };

    render(
      <ResearchInspector
        activeTab="activity"
        onTabChange={vi.fn()}
        selectedSource={null}
        selection={null}
        review={null}
        events={[retried, failed]}
      />,
    );

    expect(screen.getByText("Run timeline")).toBeInTheDocument();
    expect(screen.getByText("1 failure events")).toBeInTheDocument();
    expect(screen.getByText("0 recovery events")).toBeInTheDocument();
    expect(screen.getByText("execute analysis job failed")).toBeInTheDocument();
    expect(screen.getByText("Failure code: runtime-timeout")).toBeInTheDocument();
    expect(screen.getByText("execute analysis job retried")).toBeInTheDocument();
    expect(screen.getByText("Attempt: 2")).toBeInTheDocument();
  });

  it.each([
    ["failed", "planning"],
    ["failed", "running"],
    ["failed", "reviewing"],
    ["blocked", "planning"],
    ["blocked", "running"],
    ["blocked", "reviewing"],
  ])(
    "counts %s-to-%s status transition as recovery resumed",
    (previousStatus, status) => {
      const resumed = {
        id: `event-${status}`,
        sequence: 9,
        type: "workflow.status-changed",
        taskId: null,
        jobId: null,
        data: { previousStatus, status },
        createdAt: "2026-07-20T08:00:09Z",
      } as WorkflowEvent;
      render(
        <ResearchInspector
          activeTab="activity"
          onTabChange={vi.fn()}
          selectedSource={null}
          selection={null}
          review={null}
          events={[resumed]}
        />,
      );
      expect(screen.getByText("1 recovery events")).toBeInTheDocument();
    },
  );
});
