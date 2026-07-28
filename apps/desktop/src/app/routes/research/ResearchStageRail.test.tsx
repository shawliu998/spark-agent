import type { ResearchWorkflowSnapshot } from "@spark/research-domain";
import { describe, expect, it } from "vitest";
import { workflowStageIndex } from "./ResearchStageRail";

function snapshotForStep(
  type: "inspect-sources" | "extract-local-evidence" | "synthesize-extractive-claims",
): ResearchWorkflowSnapshot {
  return {
    workflow: {
      status: "running",
      currentStepId: "current-step",
    },
    plan: {
      steps: [{ id: "current-step", type }],
    },
    latestReview: null,
    result: null,
  } as unknown as ResearchWorkflowSnapshot;
}

describe("workflowStageIndex", () => {
  it("keeps project, source, and plan setup explicit", () => {
    expect(workflowStageIndex(false, 0, null)).toBe(0);
    expect(workflowStageIndex(true, 0, null)).toBe(1);
    expect(workflowStageIndex(true, 2, null)).toBe(2);
  });

  it("maps materialized literature steps to execution, evidence, and results", () => {
    expect(workflowStageIndex(true, 2, snapshotForStep("inspect-sources"))).toBe(3);
    expect(workflowStageIndex(true, 2, snapshotForStep("extract-local-evidence"))).toBe(4);
    expect(
      workflowStageIndex(true, 2, snapshotForStep("synthesize-extractive-claims")),
    ).toBe(5);
  });

  it("prioritizes a persisted workflow over the PDF source count", () => {
    const snapshot = snapshotForStep("inspect-sources");
    snapshot.workflow.status = "waiting-plan-approval";
    expect(workflowStageIndex(true, 0, snapshot)).toBe(2);
  });

  it("moves reviewing workflows to the review stage", () => {
    const snapshot = snapshotForStep("synthesize-extractive-claims");
    snapshot.workflow.status = "reviewing";
    expect(workflowStageIndex(true, 2, snapshot)).toBe(6);
  });
});
