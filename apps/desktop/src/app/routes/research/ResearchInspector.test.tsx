import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WorkflowEvent } from "@spark/research-domain";
import { ResearchInspector } from "./ResearchInspector";

vi.mock("./useSourcePdfBlob", () => ({
  useSourcePdfBlob: () => ({ url: null, loading: false, error: null }),
}));

describe("ResearchInspector", () => {
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
    expect(screen.getByText("Risk: medium")).toBeInTheDocument();
    expect(screen.getByText("Reason: Approve the source-bound research plan.")).toBeInTheDocument();
    expect(
      screen.getByText("Resource: source:source-1:sha256:abc:verified-passages:remote"),
    ).toBeInTheDocument();
    expect(screen.getByText("Schema: workflow-plan-approval-v2")).toBeInTheDocument();
    expect(screen.getByText(`Approval envelope: ${"a".repeat(64)}`)).toBeInTheDocument();
  });
});
