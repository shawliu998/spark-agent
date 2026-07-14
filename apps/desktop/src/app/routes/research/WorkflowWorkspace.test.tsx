import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ResearchWorkflowSnapshot } from "@spark/research-domain";
import { WorkflowWorkspace } from "./WorkflowWorkspace";

const handlers = {
  onCreate: vi.fn(async () => {}),
  onApprovePlan: vi.fn(async () => {}),
  onCancel: vi.fn(async () => {}),
  onRetry: vi.fn(async () => {}),
  onResume: vi.fn(async () => {}),
  onRefresh: vi.fn(async () => {}),
  onNew: vi.fn(),
  onSelectEvidence: vi.fn(),
  onOpenReview: vi.fn(),
  onOpenActivity: vi.fn(),
};

function planSnapshot(): ResearchWorkflowSnapshot {
  return {
    workflow: {
      id: "workflow-1",
      projectId: "project-1",
      goal: "Compare findings and conflicting evidence",
      workflowType: "literature-synthesis",
      status: "waiting-plan-approval",
      revision: 2,
      currentStepId: null,
      planVersion: 1,
      retryCount: 0,
      blockingReason: null,
      cancelRequestedAt: null,
      createdAt: "2026-07-14T08:00:00Z",
      updatedAt: "2026-07-14T08:00:01Z",
      completedAt: null,
    },
    plan: {
      id: "plan-1",
      workflowId: "workflow-1",
      version: 1,
      status: "pending-approval",
      planSha256: "a".repeat(64),
      spec: {
        schemaVersion: "1",
        goal: "Compare findings and conflicting evidence",
        steps: [
          {
            key: "inspect",
            type: "inspect-sources",
            objective: "Inspect indexed project PDFs",
            inputs: { sourceKind: "pdf" },
            expectedOutputs: ["sources"],
            acceptanceCriteria: ["at-least-one-ready-pdf"],
          },
          {
            key: "extract",
            type: "extract-local-evidence",
            objective: "Extract verified evidence passages",
            inputs: { query: "findings", maxPassages: 12, maxPerSource: 4 },
            expectedOutputs: ["evidence"],
            acceptanceCriteria: ["at-least-one-verified-evidence"],
          },
          {
            key: "synthesize",
            type: "synthesize-extractive-claims",
            objective: "Build atomic evidence-backed claims",
            inputs: { maxClaims: 8 },
            expectedOutputs: ["claims", "evidence-map"],
            acceptanceCriteria: ["every-claim-has-verified-evidence"],
          },
        ],
      },
      steps: [],
      createdAt: "2026-07-14T08:00:01Z",
      approvedAt: null,
    },
    pendingApprovals: [
      {
        id: "approval-1",
        workflowId: "workflow-1",
        planId: "plan-1",
        taskId: null,
        kind: "plan",
        status: "waiting",
        subjectType: "workflow-plan",
        subjectId: "plan-1",
        action: "approve-plan",
        payloadSha256: "a".repeat(64),
        riskLevel: "low",
        reason: "Confirm this local read-only plan before it runs.",
        affectedResources: ["project:project-1"],
        createdAt: "2026-07-14T08:00:01Z",
        decidedAt: null,
      },
    ],
    result: null,
    latestReview: null,
    allowedActions: ["approve-plan", "cancel"],
    eventCursor: 2,
  };
}

describe("WorkflowWorkspace", () => {
  it("shows the real three-step plan and explicit local-only boundary", () => {
    render(
      <WorkflowWorkspace
        {...handlers}
        snapshot={planSnapshot()}
        sources={[]}
        loading={false}
        mutating={false}
        connection="live"
        error={null}
        canStart
      />,
    );

    expect(screen.getByText("Inspect indexed project PDFs")).toBeInTheDocument();
    expect(screen.getByText("Extract verified evidence passages")).toBeInTheDocument();
    expect(screen.getByText("Build atomic evidence-backed claims")).toBeInTheDocument();
    expect(screen.getByText("Local only.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve & run" })).toBeEnabled();
  });

  it("uses review support words instead of foregrounding model confidence", () => {
    const snapshot = planSnapshot();
    snapshot.workflow.status = "completed";
    snapshot.workflow.completedAt = "2026-07-14T08:00:10Z";
    snapshot.allowedActions = [];
    snapshot.pendingApprovals = [];
    snapshot.result = {
      answerId: "answer-1",
      summary: "The imported studies report a consistent directional finding.",
      claims: [
        {
          id: "claim-1",
          statement: "The reported outcome moved in the same direction.",
          supportStatus: "supported",
          confidence: 0.734,
          evidence: [
            {
              evidenceId: "evidence-1",
              sourceId: "source-1",
              pageIndex: 3,
              pageLabel: "4",
              text: "The measured outcome decreased after the intervention.",
              bbox: null,
              coordinateSpace: "normalized-rotated-top-left-v1",
              quoteHash: "b".repeat(64),
              extractionMethod: "local-page-search",
              confidence: 1,
              verified: true,
              relationship: "supporting",
            },
          ],
        },
      ],
      unresolvedQuestions: [],
    };
    snapshot.latestReview = {
      id: "review-1",
      reviewType: "deterministic-claims-v1",
      verdict: "passed",
      inputSha256: "d".repeat(64),
      result: {
        schemaVersion: "1",
        verdict: "passed",
        checks: [],
        claimResults: [],
        requiredRevisions: [],
      },
      createdAt: "2026-07-14T08:00:10Z",
    };

    render(
      <WorkflowWorkspace
        {...handlers}
        snapshot={snapshot}
        sources={[
          {
            id: "source-1",
            projectId: "project-1",
            title: "Imported study",
            sourceKind: "pdf",
            authors: [],
            doi: null,
            arxivId: null,
            localPath: "/not-rendered",
            publicationDate: null,
            ingestionStatus: "ready",
            contentHash: "c".repeat(64),
            pageCount: 10,
            createdAt: "2026-07-14T08:00:00Z",
          },
        ]}
        loading={false}
        mutating={false}
        connection="live"
        error={null}
        canStart
      />,
    );

    expect(screen.getByText("supported")).toBeInTheDocument();
    expect(screen.getByText("Evidence-integrity review passed")).toBeInTheDocument();
    expect(screen.queryByText(/73%/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Imported study, page 4/i }));
    expect(handlers.onSelectEvidence).toHaveBeenCalledWith(
      expect.objectContaining({ evidenceId: "evidence-1" }),
    );
  });

  it("does not label a provisional result as reviewed", () => {
    const snapshot = planSnapshot();
    snapshot.workflow.status = "reviewing";
    snapshot.pendingApprovals = [];
    snapshot.allowedActions = ["cancel"];
    snapshot.result = {
      answerId: "answer-1",
      summary: "A provisional evidence map exists while review is running.",
      claims: [],
      unresolvedQuestions: [],
    };

    render(
      <WorkflowWorkspace
        {...handlers}
        snapshot={snapshot}
        sources={[]}
        loading={false}
        mutating={false}
        connection="live"
        error={null}
        canStart
      />,
    );

    expect(screen.getByText("Provisional evidence map — review pending")).toBeInTheDocument();
    expect(screen.queryByText("Evidence-integrity review passed")).not.toBeInTheDocument();
  });
});
