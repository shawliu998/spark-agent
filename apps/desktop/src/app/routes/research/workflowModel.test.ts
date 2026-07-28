import { describe, expect, it } from "vitest";
import type {
  ResearchWorkflowSnapshot,
  WorkflowEvent,
} from "@spark/research-domain";
import {
  canonicalAutonomousLiteratureIdentity,
  generationModeForSnapshot,
  matchesAutonomousLiteratureIdentity,
  mergeWorkflowEvents,
  resultReviewState,
  sameCreateIntent,
  snapshotIsOlder,
  workflowNeedsAttention,
} from "./workflowModel";

function snapshot(
  revision = 1,
  eventCursor = 1,
): ResearchWorkflowSnapshot {
  return {
    workflow: {
      id: "workflow-1",
      projectId: "project-1",
      goal: "Compare studies",
      mode: "advanced" as const,
      workflowType: "literature-synthesis",
      status: "running",
      revision,
      currentStepId: null,
      planVersion: null,
      retryCount: 0,
      blockingReason: null,
      cancelRequestedAt: null,
      createdAt: "2026-07-14T08:00:00Z",
      updatedAt: "2026-07-14T08:00:00Z",
      completedAt: null,
    },
    plan: null,
    pendingApprovals: [],
    result: null,
    latestReview: null,
    allowedActions: ["cancel"],
    eventCursor,
  };
}

function event(id: string, sequence: number): WorkflowEvent {
  return {
    id,
    sequence,
    type: "workflow.cancel-requested",
    taskId: null,
    jobId: null,
    data: { requested: true },
    createdAt: "2026-07-14T08:00:00Z",
  };
}

describe("workflow model", () => {
  it("matches only the exact local autonomous literature identity", () => {
    const identity = canonicalAutonomousLiteratureIdentity(
      "project-1",
      "  Compare studies  ",
      ["paper-b", "paper-a", "paper-a"],
    );
    expect(identity).toEqual({
      projectId: "project-1",
      goal: "Compare studies",
      sourceIds: ["paper-a", "paper-b"],
    });
    const current = snapshot() as unknown as ResearchWorkflowSnapshot;
    current.workflow = {
      ...current.workflow,
      mode: "autonomous",
      workflowType: "literature-synthesis",
      generationMode: "local-deterministic",
      sourceIds: ["paper-b", "paper-a"],
      goal: "Compare studies",
    } as typeof current.workflow;
    expect(matchesAutonomousLiteratureIdentity(current, identity)).toBe(true);
    for (const changed of [
      { projectId: "project-2" },
      { goal: "Different question" },
      { sourceIds: ["paper-a"] },
    ]) {
      expect(matchesAutonomousLiteratureIdentity(current, { ...identity!, ...changed })).toBe(false);
    }
    (current.workflow as { generationMode: string }).generationMode = "remote-model-assisted";
    expect(matchesAutonomousLiteratureIdentity(current, identity)).toBe(false);

    (current.workflow as { generationMode: string }).generationMode = "local-deterministic";
    (current.workflow as { workflowType: null; status: string }).workflowType = null;
    (current.workflow as { workflowType: null; status: string }).status = "completed";
    expect(matchesAutonomousLiteratureIdentity(current, identity)).toBe(false);
    (current.workflow as { workflowType: null; status: string }).status = "routing";
    expect(matchesAutonomousLiteratureIdentity(current, identity)).toBe(true);
  });

  it("orders snapshots by revision before event cursor", () => {
    expect(snapshotIsOlder(snapshot(2, 99), snapshot(3, 1))).toBe(true);
    expect(snapshotIsOlder(snapshot(3, 1), snapshot(3, 2))).toBe(true);
    expect(snapshotIsOlder(snapshot(3, 3), snapshot(3, 2))).toBe(false);
  });

  it("deduplicates, orders, and bounds the event window", () => {
    const current = Array.from({ length: 100 }, (_, index) =>
      event(`event-${index + 1}`, index + 1),
    );
    const merged = mergeWorkflowEvents(current, [
      event("event-50", 150),
      event("event-101", 101),
    ]);

    expect(merged).toHaveLength(100);
    expect(merged[0]?.id).toBe("event-2");
    expect(merged[merged.length - 1]).toMatchObject({
      id: "event-50",
      sequence: 150,
    });
    expect(new Set(merged.map((item) => item.id)).size).toBe(100);
  });

  it("derives attention, generation, and review presentation from the snapshot", () => {
    const current = snapshot();
    expect(generationModeForSnapshot(current)).toBe("local-deterministic");
    expect(workflowNeedsAttention("blocked")).toBe(true);
    expect(workflowNeedsAttention("running")).toBe(false);
    expect(resultReviewState(current)).toBe("pending");

    current.workflow.status = "completed";
    current.result = {
      answerId: "answer-1",
      summary: "Summary",
      generator: "local-extractive-v1",
      model: null,
      promptVersion: null,
      integrityStatus: "verified-frozen-v2",
      claims: [],
      unresolvedQuestions: [],
    };
    current.latestReview = {
      id: "review-1",
      reviewType: "deterministic-claims-v2",
      verdict: "passed",
      inputSha256: "a".repeat(64),
      result: {
        schemaVersion: "2",
        verdict: "passed",
        checks: [],
        claimResults: [],
        requiredRevisions: [],
        resultSnapshotSha256: "b".repeat(64),
        resultSnapshot: current.result,
      },
      createdAt: "2026-07-14T08:00:01Z",
    };
    expect(resultReviewState(current)).toBe("passed");

    current.result.integrityStatus = "unfrozen";
    current.latestReview = {
      id: "review-legacy",
      reviewType: "deterministic-claims-v1",
      verdict: "passed",
      inputSha256: "c".repeat(64),
      result: {
        schemaVersion: "1",
        verdict: "passed",
        checks: [],
        claimResults: [],
        requiredRevisions: [],
        resultSnapshotSha256: null,
        resultSnapshot: null,
      },
      createdAt: "2026-07-14T08:00:02Z",
    };
    expect(resultReviewState(current)).toBe("legacy-passed");

    current.latestReview = {
      id: "review-revision",
      reviewType: "deterministic-claims-v1",
      verdict: "revision-required",
      inputSha256: "d".repeat(64),
      result: {
        schemaVersion: "1",
        verdict: "revision-required",
        checks: [],
        claimResults: [],
        requiredRevisions: ["Add verified evidence."],
        resultSnapshotSha256: null,
        resultSnapshot: null,
      },
      createdAt: "2026-07-14T08:00:03Z",
    };
    expect(resultReviewState(current)).toBe("needs-revision");
  });

  it("reuses an idempotency intent only when every request input matches", () => {
    const intent = {
      projectId: "project-1",
      goal: "Compare studies",
      mode: "advanced" as const,
      workflowType: "literature-synthesis" as const,
      datasetSourceId: null,
      generationMode: "remote-model-assisted" as const,
      remoteDataApproved: true,
      idempotencyKey: "intent-1",
    };
    expect(sameCreateIntent(intent, { ...intent })).toBe(true);
    expect(
      sameCreateIntent(intent, { ...intent, remoteDataApproved: false }),
    ).toBe(false);

    const autonomousIntent = {
      projectId: "project-1",
      goal: "Route selected sources",
      mode: "autonomous" as const,
      sourceIds: ["paper-1"],
      remoteDataApproved: true,
      idempotencyKey: "intent-2",
    };
    expect(sameCreateIntent(autonomousIntent, { ...autonomousIntent })).toBe(true);
    expect(
      sameCreateIntent(autonomousIntent, {
        ...autonomousIntent,
        remoteDataApproved: false,
      }),
    ).toBe(false);
  });
});
