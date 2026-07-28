import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type {
  AgentDecisionOut,
  AgentLoopLimitState,
  AgentResearchWorkflowSnapshot,
  StepObservationOut,
} from "@spark/research-domain";
import { DatasetWorkflowDetails } from "../DatasetWorkflowDetails";
import { AgentDecisionCard } from "./AgentDecisionCard";
import { AgentLoopLimitsCard } from "./AgentLoopLimitsCard";
import { AgentProgressSummary } from "./AgentProgressSummary";
import { DecisionHistory } from "./DecisionHistory";
import { ObservationCard } from "./ObservationCard";
import i18n from "@/i18n";

const observation = {
  schemaVersion: "1",
  id: "observation-1",
  workflowId: "workflow-agent-1",
  planId: "plan-1",
  taskId: "task-execute",
  sourceJobId: "job-execute-2",
  runId: "run-2",
  reviewId: null,
  observationType: "analysis-execution",
  stepKey: "execute-analysis",
  attempt: 2,
  status: "failed",
  facts: [
    {
      code: "group-sample-size",
      statement: "The treatment group has 42 valid samples.",
      value: 42,
      sourceType: "structured-result",
      sourceId: "result-1",
    },
  ],
  warnings: [
    {
      code: "zero-variance",
      message: "The control group has zero variance.",
      severity: "error",
      sourceId: "run-2",
    },
  ],
  unresolvedQuestions: [
    {
      code: "method-confirmation",
      question: "Use a rank-based comparison instead?",
      answerType: "method-confirmation",
    },
  ],
  artifactIds: ["artifact-result-1"],
  failureCategory: "method",
  recommendedActions: ["revise-analysis-spec", "stop"],
  inputSha256: "a".repeat(64),
  outputSha256: "b".repeat(64),
  generator: "deterministic-observer-v1",
  promptVersion: null,
  model: null,
  modelInvocationId: null,
  createdAt: "2026-07-16T10:00:00Z",
} satisfies StepObservationOut;

const pendingDecision = {
  schemaVersion: "1",
  id: "decision-2",
  workflowId: observation.workflowId,
  observationId: observation.id,
  decisionRevision: 2,
  action: "revise-analysis-spec",
  reasonCode: "method-zero-variance",
  reason: "The current parametric method is not suitable for this dataset.",
  targetStepKey: null,
  clarificationRequests: [],
  proposedAnalysisSpec: {
    schemaVersion: "1",
    objective: "Compare score between treatment groups.",
    datasetSourceId: "dataset-1",
    datasetContentHash: "c".repeat(64),
    datasetProfileHash: "d".repeat(64),
    operation: {
      type: "two-group-comparison",
      outcomeColumn: "score",
      groupColumn: "arm",
      groups: ["control", "treatment"],
      method: "mann-whitney-u",
      effectSize: "rank-biserial",
      checkAssumptions: true,
      plot: "boxplot",
    },
    missingValuePolicy: "drop-per-operation",
    confidenceLevel: 0.95,
    randomSeed: 42,
    assumptions: ["Observations are independent."],
    limitations: ["One group has zero variance."],
  },
  analysisSpecDiff: {
    changedFields: ["operation.method", "operation.effectSize"],
    previousValues: {
      "operation.method": "welch-t-test",
      "operation.effectSize": "hedges-g",
    },
    proposedValues: {
      "operation.method": "mann-whitney-u",
      "operation.effectSize": "rank-biserial",
    },
    reason: "Use the supported rank-based comparison.",
  },
  requiresUserConfirmation: true,
  status: "waiting-user-confirmation",
  expectedWorkflowRevision: 12,
  generator: "deterministic-action-policy-v1",
  promptVersion: null,
  model: null,
  modelInvocationId: null,
  inputSha256: "e".repeat(64),
  outputSha256: "f".repeat(64),
  researchContextSnapshotId: null,
  researchContextSnapshotSha256: null,
  appliedAt: null,
  createdAt: "2026-07-16T10:00:01Z",
} satisfies AgentDecisionOut;

const limits = {
  agentSteps: { count: 4, limit: 8, reached: false },
  planRevisions: { count: 1, limit: 2, reached: false },
  analysisSpecRevisions: { count: 1, limit: 2, reached: false },
  stepRetries: { count: 2, limit: 2, reached: true },
  clarificationRounds: { count: 1, limit: 3, reached: false },
  modelDecisions: { count: 0, limit: 5, reached: false },
  invalidModelDecisions: { count: 0, limit: 2, reached: false },
} satisfies AgentLoopLimitState;

const history = [
  {
    id: "decision-1",
    observationId: "observation-previous",
    action: "retry-step",
    reason: "The runtime transport failed temporarily.",
    status: "applied",
    requiresUserConfirmation: false,
    researchContextSnapshotId: null,
    researchContextSnapshotSha256: null,
    createdAt: "2026-07-16T09:59:00Z",
    appliedAt: "2026-07-16T09:59:01Z",
  },
  {
    id: pendingDecision.id,
    observationId: pendingDecision.observationId,
    action: pendingDecision.action,
    reason: pendingDecision.reason,
    status: pendingDecision.status,
    requiresUserConfirmation: true,
    researchContextSnapshotId: null,
    researchContextSnapshotSha256: null,
    createdAt: pendingDecision.createdAt,
    appliedAt: null,
  },
] satisfies AgentResearchWorkflowSnapshot["decisionHistory"];

describe("agent loop UI", () => {
  it("renders structured observation evidence and discloses provenance on demand", async () => {
    const user = userEvent.setup();
    render(<ObservationCard observation={observation} />);

    expect(screen.getByText("The treatment group has 42 valid samples.")).toBeVisible();
    expect(screen.getByText(/control group has zero variance/i)).toBeVisible();
    expect(screen.getByText(/rank-based comparison instead/i)).toBeVisible();
    expect(screen.getByText("artifact-result-1")).toBeVisible();
    expect(screen.getByText("Revise analysis method")).toBeVisible();
    const lineage = screen.getByText(/source job job-execute-2/i);
    expect(lineage).not.toBeVisible();
    await user.click(screen.getByText("deterministic-observer-v1"));
    expect(lineage).toBeVisible();
  });

  it("shows the exact spec diff as read-only provenance", () => {
    render(<AgentDecisionCard decision={pendingDecision} />);

    expect(screen.getByText("welch-t-test")).toBeVisible();
    expect(screen.getByText("mann-whitney-u")).toBeVisible();
    expect(screen.getByText(/reload the research workspace/i)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Approve proposal" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject proposal" })).not.toBeInTheDocument();
  });

  it("shows a bound Research Memory snapshot only when decision provenance exists", () => {
    const boundDecision = {
      ...pendingDecision,
      researchContextSnapshotId: "snapshot-evidence-1",
      researchContextSnapshotSha256: "a".repeat(64),
    } satisfies AgentDecisionOut;
    render(
      <>
        <AgentDecisionCard decision={boundDecision} />
        <DecisionHistory
          decisions={[
            {
              id: boundDecision.id,
              observationId: boundDecision.observationId,
              action: boundDecision.action,
              reason: boundDecision.reason,
              status: boundDecision.status,
              requiresUserConfirmation: boundDecision.requiresUserConfirmation,
              researchContextSnapshotId: boundDecision.researchContextSnapshotId,
              researchContextSnapshotSha256:
                boundDecision.researchContextSnapshotSha256,
              createdAt: boundDecision.createdAt,
              appliedAt: boundDecision.appliedAt,
            },
          ]}
        />
      </>,
    );

    expect(screen.getAllByText(/Research Memory snapshot: snapshot-evi/)).toHaveLength(2);
    expect(screen.getAllByText(/Research Memory snapshot: snapshot-evi/)[0]).toHaveAttribute(
      "title",
      expect.stringContaining("a".repeat(64)),
    );
  });

  it("does not claim Research Memory use for legacy decision data", () => {
    render(<AgentDecisionCard decision={pendingDecision} />);

    expect(screen.queryByText(/Research Memory snapshot:/)).not.toBeInTheDocument();
  });

  it("resolves an exact pending method revision from the existing decision card", async () => {
    const user = userEvent.setup();
    const onResolve = vi.fn(async () => {});
    render(
      <AgentDecisionCard
        decision={pendingDecision}
        mutating={false}
        onResolve={onResolve}
      />,
    );

    await user.click(
      screen.getByRole("button", { name: "Approve method change" }),
    );
    expect(onResolve).toHaveBeenCalledWith("approved");
    await user.click(screen.getByRole("button", { name: "Reject" }));
    expect(onResolve).toHaveBeenCalledWith("rejected");
  });

  it("shows bounded limits, retries, attempts, and durable decision history", () => {
    render(
      <>
        <AgentProgressSummary
          latestObservation={observation}
          pendingDecision={pendingDecision}
          decisionHistory={history}
        />
        <AgentLoopLimitsCard limits={limits} />
        <DecisionHistory decisions={history} />
      </>,
    );

    expect(screen.getByText("1 retries · attempt 2")).toBeVisible();
    expect(screen.getByText("Limit reached")).toBeVisible();
    expect(screen.getByText("2/2")).toBeVisible();
    expect(screen.getByText("The runtime transport failed temporarily.")).toBeVisible();
  });

  it("mounts persisted agent-loop state in dataset details as a read-only fallback", () => {
    render(
      <DatasetWorkflowDetails
        snapshot={agentSnapshot()}
        mutating={false}
        onDecision={vi.fn()}
        onCancel={vi.fn()}
        onAcceptReviewWarnings={vi.fn()}
      />,
    );

    expect(screen.getByText("Bounded agent progress")).toBeVisible();
    expect(screen.getByText("Agent observation")).toBeVisible();
    expect(screen.getByText("Agent next-action decision")).toBeVisible();
    expect(screen.getByText(/reload the research workspace/i)).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Approve proposal" }),
    ).not.toBeInTheDocument();
  });

  it("localizes decision history, observation state, and current progress in Simplified Chinese", async () => {
    await act(async () => { await i18n.changeLanguage("zh-Hans"); });
    render(
      <>
        <AgentProgressSummary
          latestObservation={observation}
          pendingDecision={pendingDecision}
          decisionHistory={history}
        />
        <ObservationCard observation={observation} />
        <AgentDecisionCard decision={pendingDecision} />
        <DecisionHistory decisions={history} />
      </>,
    );

    expect(screen.getAllByText("修订分析方法").length).toBeGreaterThanOrEqual(3);
    expect(screen.getAllByText("等待用户确认").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("分析执行")).toBeVisible();
    expect(screen.getByText("方法")).toBeVisible();
    expect(screen.getByText("失败")).toBeVisible();
    expect(screen.getByText("重试步骤")).toBeVisible();
    expect(screen.getByText("已应用")).toBeVisible();

    await act(async () => { await i18n.changeLanguage("en"); });
  });
});

function agentSnapshot(): AgentResearchWorkflowSnapshot {
  return {
    workflow: {
      id: observation.workflowId,
      projectId: "project-1",
      goal: "Compare treatment and control score.",
      mode: "autonomous",
      sourceIds: ["dataset-1"],
      workflowType: "dataset-analysis",
      generationMode: "local-deterministic",
      status: "reviewing",
      revision: 12,
      currentStepId: "task-execute",
      planVersion: 1,
      retryCount: 1,
      statusReason: null,
      cancelRequestedAt: null,
      createdAt: "2026-07-16T09:00:00Z",
      updatedAt: "2026-07-16T10:00:01Z",
      completedAt: null,
    },
    intentDecision: {
      id: "intent-decision-1",
      workflowId: observation.workflowId,
      intent: "dataset-analysis",
      confidence: 1,
      reasoningSummary: "The selected source and goal require dataset analysis.",
      selectedSourceIds: ["dataset-1"],
      missingInputs: [],
      proposedWorkflowType: "dataset-analysis",
      promptVersion: "intent-router-v1",
      inputSha256: "1".repeat(64),
      outputSha256: "2".repeat(64),
      createdAt: "2026-07-16T09:00:00Z",
    },
    interactions: [],
    plan: null,
    pendingApprovals: [],
    result: null,
    latestReview: null,
    datasetProfile: null,
    analysisIntent: null,
    analysisRun: null,
    analysisSpec: null,
    structuredResult: null,
    reviewWarningAcceptance: null,
    latestObservation: observation,
    pendingDecision,
    decisionHistory: history,
    agentLoopLimits: limits,
    allowedActions: ["cancel"],
    eventCursor: 21,
  };
}
