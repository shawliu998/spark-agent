import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ResearchWorkflowSnapshot } from "@spark/research-domain";
import { WorkflowReviewSummary, WorkflowWorkspace } from "./WorkflowWorkspace";

const handlers = {
  remoteDestination: {
    provider: "openai-compatible" as const,
    endpointHost: "models.example.test",
    endpointIdentity: `sha256:${"e".repeat(64)}`,
    model: "provider/model-1",
  },
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

beforeEach(() => {
  vi.clearAllMocks();
});

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
            inputs: { sourceKind: "pdf", sourceIds: null },
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
  it("defaults to local generation and requires explicit approval before sending a goal remotely", async () => {
    render(
      <WorkflowWorkspace
        {...handlers}
        snapshot={null}
        sources={[]}
        loading={false}
        mutating={false}
        connection="idle"
        error={null}
        canStart
      />,
    );

    expect(
      screen.getByRole("button", { name: /Local deterministic/i }),
    ).toHaveAttribute("aria-pressed", "true");
    fireEvent.change(screen.getByLabelText("Give Spark Agent a research goal"), {
      target: { value: "Compare the imported studies" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: /Model-assisted remote/i }),
    );

    const start = screen.getByRole("button", { name: "Create plan" });
    expect(start).toBeDisabled();
    expect(
      screen.getByText(/No PDF passage is sent at this step/i),
    ).toBeInTheDocument();
    expect(screen.getByText("models.example.test")).toBeInTheDocument();
    expect(screen.getByText("provider/model-1")).toBeInTheDocument();
    expect(screen.getByText(`sha256:${"e".repeat(64)}`)).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /I approve sending this research goal/i,
      }),
    );
    expect(start).toBeEnabled();

    fireEvent.change(screen.getByLabelText("Give Spark Agent a research goal"), {
      target: { value: "Compare the imported studies and private notes" },
    });
    expect(start).toBeDisabled();
    expect(
      screen.getByRole("checkbox", {
        name: /I approve sending this research goal/i,
      }),
    ).not.toBeChecked();
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /I approve sending this research goal/i,
      }),
    );
    fireEvent.click(start);

    expect(handlers.onCreate).toHaveBeenCalledWith(
      "Compare the imported studies and private notes",
      "remote-model-assisted",
      true,
    );
  });

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
    expect(screen.getByText("findings")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText("at-least-one-verified-evidence")).toBeInTheDocument();
    expect(screen.getByText("a".repeat(64))).toBeInTheDocument();
    expect(
      screen.getByText(new RegExp(`Approval payload SHA-256: ${"a".repeat(64)}`)),
    ).toBeInTheDocument();
    expect(screen.getByText("Local only.")).toBeInTheDocument();
    expect(screen.getByText("local deterministic")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve & run" })).toBeEnabled();
  });

  it("revokes goal disclosure approval when the remote destination changes", () => {
    const { rerender } = render(
      <WorkflowWorkspace
        {...handlers}
        snapshot={null}
        sources={[]}
        loading={false}
        mutating={false}
        connection="idle"
        error={null}
        canStart
      />,
    );
    fireEvent.change(screen.getByLabelText("Give Spark Agent a research goal"), {
      target: { value: "Compare the imported studies" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Model-assisted remote/i }));
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /I approve sending this research goal/i,
      }),
    );
    expect(screen.getByRole("button", { name: "Create plan" })).toBeEnabled();

    rerender(
      <WorkflowWorkspace
        {...handlers}
        remoteDestination={{
          ...handlers.remoteDestination,
          endpointHost: "new-models.example.test",
          endpointIdentity: `sha256:${"f".repeat(64)}`,
        }}
        snapshot={null}
        sources={[]}
        loading={false}
        mutating={false}
        connection="idle"
        error={null}
        canStart
      />,
    );

    expect(screen.getByText("new-models.example.test")).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", {
        name: /I approve sending this research goal/i,
      }),
    ).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Create plan" })).toBeDisabled();
  });

  it("shows model provenance and the exact affected-resource approval scope", () => {
    const snapshot = planSnapshot();
    snapshot.workflow.generationMode = "remote-model-assisted";
    if (!snapshot.plan) throw new Error("plan fixture is missing");
    snapshot.plan.generator = "remote-model-assisted-v1";
    snapshot.plan.promptVersion = "remote-plan-v1";
    snapshot.plan.model = "provider/model-1";
    const inspectStep = snapshot.plan.spec.steps[0];
    if (!("sourceKind" in inspectStep.inputs)) throw new Error("inspect fixture is invalid");
    inspectStep.inputs.frozenSources = [
      {
        sourceId: "paper-1",
        title: "Frozen study at approval",
        contentHash: "f".repeat(64),
        pageManifestHash: "d".repeat(64),
      },
    ];
    snapshot.pendingApprovals[0] = {
      ...snapshot.pendingApprovals[0],
      riskLevel: "medium",
      affectedResources: [
        "project:project-1",
        "remote-endpoint-host:models.example.test",
        `remote-endpoint-identity:sha256:${"e".repeat(64)}`,
        "remote-model:provider/model-1",
        `source:paper-1:sha256:${"f".repeat(64)}:verified-passages:remote`,
      ],
      reason: "Approve sending verified excerpts from these frozen sources.",
    };

    render(
      <WorkflowWorkspace
        {...handlers}
        snapshot={snapshot}
        sources={[
          {
            id: "paper-1",
            projectId: "project-1",
            title: "Frozen study",
            sourceKind: "pdf",
            authors: [],
            doi: null,
            arxivId: null,
            localPath: "/tmp/frozen-study.pdf",
            publicationDate: null,
            ingestionStatus: "ready",
            contentHash: "f".repeat(64),
            pageCount: 2,
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

    expect(screen.getByText("remote model-assisted")).toBeInTheDocument();
    expect(screen.getByText("remote-model-assisted-v1")).toBeInTheDocument();
    expect(screen.getByText("remote-plan-v1")).toBeInTheDocument();
    expect(screen.getByText("provider/model-1")).toBeInTheDocument();
    expect(screen.getByText("project:project-1")).toBeInTheDocument();
    expect(screen.getByText("remote-endpoint-host:models.example.test")).toBeInTheDocument();
    expect(
      screen.getByText(`remote-endpoint-identity:sha256:${"e".repeat(64)}`),
    ).toBeInTheDocument();
    expect(screen.getByText("remote-model:provider/model-1")).toBeInTheDocument();
    expect(screen.getByText(/Frozen study at approval/)).toBeInTheDocument();
    expect(screen.getAllByText(/paper-1/)).toHaveLength(2);
    expect(screen.getByText(new RegExp(`File SHA-256: ${"f".repeat(64)}`))).toBeInTheDocument();
    expect(
      screen.getByText(new RegExp(`Parsed-page manifest SHA-256: ${"d".repeat(64)}`)),
    ).toBeInTheDocument();
    expect(screen.getByText("findings")).toBeInTheDocument();
    expect(screen.getByText("every-claim-has-verified-evidence")).toBeInTheDocument();
    expect(
      screen.getByText(
        `source:paper-1:sha256:${"f".repeat(64)}:verified-passages:remote`,
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("medium risk")).toBeInTheDocument();
    expect(
      screen.getByText(/Approving this plan permits only verified passages/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Only entries marked verified-passages:remote/i),
    ).toBeInTheDocument();
    expect(screen.queryByText("Local only.")).not.toBeInTheDocument();
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
      generator: "local-extractive-v1",
      model: null,
      promptVersion: "local-extractive-v1",
      integrityStatus: "verified-frozen-v2",
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
              sourceTitle: "Frozen imported study",
              sourceContentHash: "c".repeat(64),
              sourcePageManifestHash: "e".repeat(64),
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
      reviewType: "deterministic-claims-v2",
      verdict: "passed",
      inputSha256: "d".repeat(64),
      result: {
        schemaVersion: "2",
        verdict: "passed",
        checks: [],
        claimResults: [],
        requiredRevisions: [],
        resultSnapshotSha256: "f".repeat(64),
        resultSnapshot: snapshot.result,
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
            title: "Changed live title",
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
    render(<WorkflowReviewSummary review={snapshot.latestReview} />);

    expect(screen.getByText("supported")).toBeInTheDocument();
    expect(screen.getByText("Evidence-integrity review passed")).toBeInTheDocument();
    expect(screen.getByText(/Frozen result SHA-256/i)).toBeInTheDocument();
    expect(screen.getByText(/does not establish the scientific correctness/i)).toBeInTheDocument();
    expect(screen.queryByText(/73%/)).not.toBeInTheDocument();
    expect(screen.queryByText("Changed live title")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Frozen imported study, page 4/i }));
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
      generator: "local-extractive-v1",
      model: null,
      promptVersion: null,
      integrityStatus: "unfrozen",
      claims: [
        {
          id: "claim-pending",
          statement: "This extractive claim is waiting for deterministic review.",
          supportStatus: "pending-review",
          confidence: 1,
          evidence: [],
        },
      ],
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
    expect(screen.getByText("pending review")).toBeInTheDocument();
    expect(screen.getByText(/Unfrozen result/i)).toBeInTheDocument();
    expect(screen.queryByText("Evidence-integrity review passed")).not.toBeInTheDocument();
  });

  it("labels remote model output as provenance without presenting confidence as evidence", () => {
    const snapshot = planSnapshot();
    snapshot.workflow.status = "reviewing";
    snapshot.workflow.generationMode = "remote-model-assisted";
    snapshot.pendingApprovals = [];
    snapshot.allowedActions = ["cancel"];
    snapshot.result = {
      answerId: "answer-remote",
      summary: "A model-assisted synthesis awaiting deterministic review.",
      generator: "remote-model-assisted-v1",
      model: "provider/model-1",
      promptVersion: "remote-extractive-synthesis-v1",
      integrityStatus: "unfrozen",
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

    expect(screen.getByText("Model-assisted synthesis.")).toBeInTheDocument();
    expect(screen.getByText("remote-model-assisted-v1")).toBeInTheDocument();
    expect(screen.getByText("provider/model-1")).toBeInTheDocument();
    expect(screen.getByText("remote-extractive-synthesis-v1")).toBeInTheDocument();
    expect(
      screen.getByText(/Generation mode is provenance, not evidence strength/i),
    ).toBeInTheDocument();
    expect(screen.getByText("Provisional evidence map — review pending")).toBeInTheDocument();
  });
});
