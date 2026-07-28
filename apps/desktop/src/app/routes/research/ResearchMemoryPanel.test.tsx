import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type {
  ResearchMemory,
  ResearchMemoryWorkspace,
  SkillActivationPreview,
  SkillCandidate,
} from "@spark/research-domain";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ResearchMemoryPanel,
  type ResearchMemoryPanelController,
  useResearchMemoryWorkspace,
} from "./ResearchMemoryPanel";

const core = vi.hoisted(() => ({
  getResearchMemoryWorkspace: vi.fn(),
  listSkillCandidates: vi.fn(),
  listSkillActivations: vi.fn(),
  getSkillActivationPreview: vi.fn(),
  approveSkillActivation: vi.fn(),
  rollbackSkillActivation: vi.fn(),
  createSkillCandidate: vi.fn(),
  resolveResearchMemoryCandidate: vi.fn(),
  invalidateResearchMemory: vi.fn(),
}));

vi.mock("@/lib/scienceCore", () => ({ scienceCore: core }));

function memory(
  id: string,
  status: ResearchMemory["status"],
  statement: string,
): ResearchMemory {
  const shared = {
    id,
    projectId: "project-1",
    scopeWorkflowId: "workflow-1",
    subjectKey: `subject-${id}`,
    revision: 1,
    previousId: null,
    schemaVersion: "1" as const,
    type: "operational-fact" as const,
    contentJson: { statement },
    sourceRefs: [
      { id: `source-${id}`, sha256: "a".repeat(64), type: "source" as const },
    ],
    artifactRefs: [],
    invalidationRule: null,
    createdBy: "test",
    memorySha256: id.padEnd(64, "0").slice(0, 64),
    createdAt: "2026-07-23T01:00:00Z",
    updatedAt: "2026-07-23T01:00:00Z",
    subjectHeadId: id,
    subjectHeadRevision: 1,
  };
  if (status === "candidate") {
    return {
      ...shared,
      status,
      context: {
        state: "excluded",
        reasonCode: "candidate-excluded",
        snapshotId: null,
        snapshotSha256: null,
      },
      availableActions: ["accept", "reject"],
    };
  }
  if (status === "committed") {
    return {
      ...shared,
      status,
      context: {
        state: "eligible",
        reasonCode: "eligible-for-future-snapshot",
        snapshotId: null,
        snapshotSha256: null,
      },
      availableActions: ["invalidate"],
    };
  }
  return {
    ...shared,
    status,
    context: {
      state: "excluded",
      reasonCode:
        status === "rejected"
          ? "rejected-excluded"
          : status === "superseded"
            ? "superseded-excluded"
            : "invalidated-excluded",
      snapshotId: null,
      snapshotSha256: null,
    },
    availableActions: [],
  };
}

function workspace(
  projectId = "project-1",
  workflowId = "workflow-1",
  items: ResearchMemory[] = [],
): ResearchMemoryWorkspace {
  return {
    schemaVersion: "1",
    projectId,
    workflowId,
    latestContextSnapshotId: null,
    latestContextSnapshotSha256: null,
    counts: {
      candidate: items.filter((item) => item.status === "candidate").length,
      committed: items.filter((item) => item.status === "committed").length,
      rejected: items.filter((item) => item.status === "rejected").length,
      superseded: items.filter((item) => item.status === "superseded").length,
      invalidated: items.filter((item) => item.status === "invalidated").length,
    },
    items,
    workspaceSha256: "f".repeat(64),
  };
}

function skillCandidate(id = "skill-candidate"): SkillCandidate {
  return {
    id,
    projectId: "project-1",
    workflowId: "workflow-1",
    schemaVersion: "1",
    name: "remember-verified-evidence",
    description: "Remember verified evidence without promoting a claim.",
    scope: "project",
    triggerJson: {},
    inputsJson: {},
    preconditionsJson: [],
    allowedToolsJson: ["spark.research_memory.remember_verified_evidence@1"],
    requiredPermissionsJson: ["project-memory:candidate-write"],
    procedureJson: [],
    postconditionsJson: [],
    failurePolicyJson: {},
    provenanceRequirementsJson: [],
    originTraceIds: ["episode-1234567890"],
    sanitizedSourceHash: "s".repeat(64),
    parentSkillId: null,
    version: 1,
    contentHash: "c".repeat(64),
    status: "awaiting-approval",
    generatedSkillMd: "# Skill",
    evaluationJson: {
      schemaVersion: "1",
      runner: "isolated-sqlite-capability-replay-v1",
      passed: true,
      results: [
        "happy-path",
        "wrong-project",
        "network",
        "file-write",
        "prompt-injection",
        "restart-recovery",
      ].map((name) => ({
        name: name as SkillCandidate["evaluationJson"]["results"][number]["name"],
        fixtureSha256: "f".repeat(64),
        outcome: "accepted",
        passed: true,
        postconditionSha256: "p".repeat(64),
        resultSha256: "r".repeat(64),
      })),
    },
    createdAt: "2026-07-24T01:00:00Z",
  };
}

function activationPreview(
  candidate: SkillCandidate,
  status:
    | "active"
    | "installing"
    | "rollback-pending"
    | "rolled-back"
    | "blocked"
    | null = null,
): SkillActivationPreview {
  const activation = status
    ? {
        id: "activation-1",
        projectId: candidate.projectId,
        workflowId: candidate.workflowId,
        candidateId: candidate.id,
        schemaVersion: "1" as const,
        targetRelativePath: ".opencode/skills/remember-verified-evidence/SKILL.md" as const,
        candidateContentHash: candidate.contentHash,
        templateSha256: "t".repeat(64),
        evaluationSha256: "e".repeat(64),
        approvalSha256: "a".repeat(64),
        priorPresent: false,
        priorSha256: null,
        installedSha256: "i".repeat(64),
        createdDirectory: true,
        status,
        createdAt: "2026-07-24T01:00:00Z",
        updatedAt: "2026-07-24T01:00:00Z",
        activatedAt: "2026-07-24T01:00:00Z",
        rolledBackAt: null,
      }
    : null;
  return {
    schemaVersion: "1",
    projectId: candidate.projectId,
    workflowId: candidate.workflowId,
    candidateId: candidate.id,
    expectedStatus: "awaiting-approval",
    targetRelativePath: ".opencode/skills/remember-verified-evidence/SKILL.md",
    candidateContentHash: candidate.contentHash,
    templateSha256: "t".repeat(64),
    evaluationSha256: "e".repeat(64),
    approvalSha256: "a".repeat(64),
    priorPresent: false,
    priorSha256: null,
    targetDirectoryPresent: false,
    latestActivation: activation,
  };
}

function controller(
  items: ResearchMemory[],
  overrides: Partial<ResearchMemoryPanelController> = {},
): ResearchMemoryPanelController {
  return {
    projectId: "project-1",
    workflowId: "workflow-1",
    workspace: workspace("project-1", "workflow-1", items),
    loading: false,
    error: null,
    actionError: null,
    working: null,
    refresh: vi.fn(async () => undefined),
    resolve: vi.fn(async () => true),
    invalidate: vi.fn(async () => true),
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  core.resolveResearchMemoryCandidate.mockResolvedValue({});
  core.invalidateResearchMemory.mockResolvedValue({});
  core.listSkillCandidates.mockResolvedValue([]);
  core.listSkillActivations.mockResolvedValue([]);
  core.getSkillActivationPreview.mockResolvedValue(null);
  core.approveSkillActivation.mockResolvedValue({});
  core.rollbackSkillActivation.mockResolvedValue({});
  core.createSkillCandidate.mockResolvedValue({});
});

describe("ResearchMemoryPanel", () => {
  it("keeps Home compact to attention summary and the Inspector as the complete untrusted-text ledger", async () => {
    const items = [
      memory("candidate", "candidate", '<img src=x onerror="alert(1)">'),
      {
        ...memory("committed", "committed", "Committed decision"),
        artifactRefs: [
          {
            id: "artifact-committed",
            sha256: "b".repeat(64),
            type: "artifact" as const,
          },
        ],
      },
      memory("invalidated", "invalidated", "Stale assumption"),
      memory("rejected", "rejected", "Rejected note"),
    ];
    const panelController = controller(items);
    const { container, rerender } = render(
      <ResearchMemoryPanel density="compact" controller={panelController} />,
    );

    expect(
      screen.getByText(/1 memory item.*need review/i),
    ).toBeInTheDocument();
    expect(screen.queryByText('<img src=x onerror="alert(1)">')).not.toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.queryByText("Committed decision")).not.toBeInTheDocument();
    expect(screen.queryByText("Stale assumption")).not.toBeInTheDocument();
    expect(screen.queryByText("Rejected note")).not.toBeInTheDocument();

    rerender(
      <ResearchMemoryPanel density="full" controller={panelController} />,
    );
    expect(screen.getByText('<img src=x onerror="alert(1)">')).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("Rejected note")).toBeInTheDocument();
    expect(
      screen.getAllByRole("list", {
        name: "Memory sources and dependencies",
      }),
    ).not.toHaveLength(0);
    expect(screen.getByText("artifact · artifact-committed")).toBeInTheDocument();
  });

  it("distinguishes committed memory awaiting the next context from memory already selected", () => {
    const awaiting = memory("awaiting", "committed", "Awaiting next turn");
    const selected = {
      ...memory("selected", "committed", "Already in context"),
      context: {
        state: "selected" as const,
        reasonCode: "selected-in-latest-snapshot" as const,
        snapshotId: "snapshot-1234567890",
        snapshotSha256: "b".repeat(64),
      },
    };

    render(
      <ResearchMemoryPanel
        density="full"
        controller={controller([awaiting, selected])}
      />,
    );

    expect(
      screen.getByText("Committed — available to the next context snapshot"),
    ).toBeInTheDocument();
    expect(screen.getByText("Used by the latest context snapshot")).toBeInTheDocument();
    expect(screen.getByText("Snapshot snapshot")).toHaveAttribute(
      "title",
      "snapshot-1234567890",
    );
  });

  it("shows dependency failures as stale context exclusions without changing committed status", () => {
    const reasons = [
      ["source-missing", "Stale — excluded from context: source is missing"],
      ["source-not-ready", "Stale — excluded from context: source is not ready"],
      ["source-stale", "Stale — excluded from context: source content changed"],
      ["evidence-missing", "Stale — excluded from context: evidence is missing"],
      [
        "evidence-invalid",
        "Stale — excluded from context: evidence no longer matches its source",
      ],
    ] as const;
    const items = reasons.map(([reasonCode], index) => ({
      ...memory(`dependency-${index}`, "committed", `Dependency ${index}`),
      context: {
        state: "excluded" as const,
        reasonCode,
        snapshotId: "snapshot-dependency",
        snapshotSha256: "c".repeat(64),
      },
    }));

    render(
      <ResearchMemoryPanel density="full" controller={controller(items)} />,
    );

    for (const [, label] of reasons) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getAllByText("Committed")).toHaveLength(reasons.length);
  });

  it("supports keyboard review and exposes disabled working and error states", async () => {
    const candidate = memory("candidate", "candidate", "Original memory");
    const panelController = controller([candidate], {
      working: { memoryId: candidate.id, action: "accept" },
      actionError: { memoryId: candidate.id, message: "Revision changed" },
    });
    const user = userEvent.setup();
    const { rerender } = render(
      <ResearchMemoryPanel density="full" controller={panelController} />,
    );

    expect(screen.getByRole("button", { name: "Accept" })).toBeDisabled();
    expect(screen.getByText("Saving…")).toBeInTheDocument();
    expect(
      screen.getByText("Could not update memory: Revision changed"),
    ).toHaveAttribute("role", "alert");

    const readyController = controller([candidate]);
    rerender(
      <ResearchMemoryPanel density="full" controller={readyController} />,
    );
    const accept = screen.getByRole("button", { name: "Accept" });
    accept.focus();
    await user.keyboard("{Enter}");
    expect(readyController.resolve).toHaveBeenCalledWith(candidate, "accept");
  });

  it("offers the reusable procedure action only for committed remembered evidence", async () => {
    const remembered = {
      ...memory("remembered", "committed", "Ignored"),
      contentJson: { kind: "remembered-evidence", pageIndex: 0 },
    };
    const saveAsProcedure = vi.fn(async () => true);
    render(
      <ResearchMemoryPanel
        density="full"
        controller={controller([remembered], { saveAsProcedure })}
      />,
    );
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Save as reusable procedure" }));
    expect(saveAsProcedure).toHaveBeenCalledWith(remembered);
  });

  it("keeps Memory visible when optional Skill validation fails and clears the error on project change", async () => {
    core.getResearchMemoryWorkspace.mockImplementation(
      async (projectId: string, workflowId: string) =>
        workspace(projectId, workflowId, [
          {
            ...memory(
              projectId === "project-1" ? "first-memory" : "second-memory",
              "committed",
              projectId === "project-1" ? "First memory remains" : "Second memory",
            ),
            projectId,
            scopeWorkflowId: workflowId,
          },
        ]),
    );
    core.listSkillCandidates.mockImplementation(async (projectId: string) => {
      if (projectId === "project-1") throw new Error("Skill origin is stale");
      return [];
    });

    function Harness({
      projectId,
      workflowId,
    }: {
      projectId: string;
      workflowId: string;
    }) {
      const panelController = useResearchMemoryWorkspace(projectId, workflowId);
      return (
        <ResearchMemoryPanel
          density="full"
          controller={panelController}
        />
      );
    }

    const { rerender } = render(
      <Harness projectId="project-1" workflowId="workflow-1" />,
    );
    expect(await screen.findByText("First memory remains")).toBeInTheDocument();
    expect(
      await screen.findByText(
        "Could not create procedure candidate: Skill origin is stale",
      ),
    ).toHaveAttribute("role", "alert");
    expect(
      screen.queryByText("Could not load research memory"),
    ).not.toBeInTheDocument();

    rerender(<Harness projectId="project-2" workflowId="workflow-2" />);
    expect(await screen.findByText("Second memory")).toBeInTheDocument();
    expect(
      screen.queryByText(
        "Could not create procedure candidate: Skill origin is stale",
      ),
    ).not.toBeInTheDocument();
  });

  it("discards a stale project response and aborts the previous project load", async () => {
    let resolveFirst: ((value: ResearchMemoryWorkspace) => void) | undefined;
    let firstSignal: AbortSignal | undefined;
    core.getResearchMemoryWorkspace.mockImplementation(
      (
        projectId: string,
        workflowId: string,
        options: { signal?: AbortSignal },
      ) => {
        if (projectId === "project-1") {
          firstSignal = options.signal;
          return new Promise<ResearchMemoryWorkspace>((resolve) => {
            resolveFirst = resolve;
          });
        }
        return Promise.resolve(
          workspace(projectId, workflowId, [
            {
              ...memory("second", "committed", "Second project memory"),
              projectId,
              scopeWorkflowId: workflowId,
            },
          ]),
        );
      },
    );

    function Harness({
      projectId,
      workflowId,
    }: {
      projectId: string;
      workflowId: string;
    }) {
      const panelController = useResearchMemoryWorkspace(projectId, workflowId);
      return (
        <ResearchMemoryPanel density="full" controller={panelController} />
      );
    }

    const { rerender } = render(
      <Harness projectId="project-1" workflowId="workflow-1" />,
    );
    rerender(<Harness projectId="project-2" workflowId="workflow-2" />);

    await screen.findByText("Second project memory");
    expect(firstSignal?.aborted).toBe(true);
    resolveFirst?.(
      workspace("project-1", "workflow-1", [
        memory("first", "committed", "First project memory"),
      ]),
    );
    await waitFor(() =>
      expect(screen.queryByText("First project memory")).not.toBeInTheDocument(),
    );
  });

  it("shows six replay states and keeps activation controls out of the compact summary", async () => {
    const candidate = skillCandidate();
    const preview = activationPreview(candidate);
    const fullController = controller([], {
      skillCandidates: [candidate],
      skillActivationPreviews: { [candidate.id]: preview },
      approveSkill: vi.fn(async () => true),
    });
    const { rerender } = render(
      <ResearchMemoryPanel density="full" controller={fullController} />,
    );

    expect(screen.getByText(/Approved tool:/)).toBeInTheDocument();
    expect(
      screen.getByRole("list", { name: "Deterministic replay states" }),
    ).toHaveTextContent("restart-recovery");
    expect(screen.getAllByText("Passed")).toHaveLength(6);
    await userEvent
      .setup()
      .click(screen.getByText("Review activation record"));
    expect(
      screen.getByText(".opencode/skills/remember-verified-evidence/SKILL.md"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve and activate" })).toBeEnabled();

    rerender(
      <ResearchMemoryPanel density="compact" controller={fullController} />,
    );
    expect(
      screen.queryByRole("button", { name: "Approve and activate" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Approved tool:/)).not.toBeInTheDocument();
  });

  it("marks activation updates busy, surfaces their error separately, and refreshes transitional status", async () => {
    const candidate = skillCandidate();
    const refreshing = vi.fn(async () => undefined);
    render(
      <ResearchMemoryPanel
        density="full"
        controller={controller([], {
          skillCandidates: [candidate],
          skillActivationPreviews: {
            [candidate.id]: activationPreview(candidate, "installing"),
          },
          skillActivationWorking: { candidateId: candidate.id, action: "approve" },
          skillActivationError: { candidateId: candidate.id, message: "Target changed" },
          refresh: refreshing,
        })}
      />,
    );

    expect(screen.getByText("Installing").closest("div[aria-busy]"))
      .toHaveAttribute("aria-busy", "true");
    expect(
      screen.getByText("Could not update procedure activation: Target changed"),
    ).toHaveAttribute("role", "alert");
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: "Refresh status" }));
    expect(refreshing).toHaveBeenCalledTimes(1);
  });

  it("echoes the complete activation preview and uses a stable approval idempotency key", async () => {
    const candidate = skillCandidate();
    const preview = activationPreview(candidate);
    core.getResearchMemoryWorkspace.mockResolvedValue(workspace());
    core.listSkillCandidates.mockResolvedValue([candidate]);
    core.getSkillActivationPreview.mockResolvedValue(preview);

    function Harness() {
      const panelController = useResearchMemoryWorkspace("project-1", "workflow-1");
      return <ResearchMemoryPanel density="full" controller={panelController} />;
    }

    render(<Harness />);
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Approve and activate" }));

    await waitFor(() =>
      expect(core.approveSkillActivation).toHaveBeenCalledWith(
        "project-1",
        "workflow-1",
        candidate.id,
        {
          expectedStatus: "awaiting-approval",
          expectedCandidateContentHash: preview.candidateContentHash,
          expectedTemplateSha256: preview.templateSha256,
          expectedEvaluationSha256: preview.evaluationSha256,
          expectedApprovalSha256: preview.approvalSha256,
          expectedPriorPresent: preview.priorPresent,
          expectedPriorSha256: preview.priorSha256,
          expectedTargetDirectoryPresent: preview.targetDirectoryPresent,
        },
        expect.objectContaining({
          idempotencyKey: `skill-activation-approve-project-1:workflow-1-${candidate.id}-${preview.approvalSha256}`,
          signal: expect.any(AbortSignal),
        }),
      ),
    );
  });

  it("rolls back with the installed hash as the current target hash and exposes pending refresh", async () => {
    const candidate = skillCandidate();
    const activePreview = activationPreview(candidate, "active");
    core.getResearchMemoryWorkspace.mockResolvedValue(workspace());
    core.listSkillCandidates.mockResolvedValue([candidate]);
    core.getSkillActivationPreview.mockResolvedValue(activePreview);

    function Harness() {
      const panelController = useResearchMemoryWorkspace("project-1", "workflow-1");
      return <ResearchMemoryPanel density="full" controller={panelController} />;
    }

    const { rerender } = render(<Harness />);
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Roll back" }));
    const activation = activePreview.latestActivation!;
    await waitFor(() =>
      expect(core.rollbackSkillActivation).toHaveBeenCalledWith(
        "project-1",
        activation.id,
        {
          expectedStatus: "active",
          expectedActivationId: activation.id,
          expectedApprovalSha256: activation.approvalSha256,
          expectedInstalledSha256: activation.installedSha256,
          expectedCurrentTargetSha256: activation.installedSha256,
        },
        expect.objectContaining({
          idempotencyKey: `skill-activation-rollback-${activation.id}-${activation.approvalSha256}-${activation.installedSha256}`,
          signal: expect.any(AbortSignal),
        }),
      ),
    );

    const pendingPreview = activationPreview(candidate, "rollback-pending");
    rerender(
      <ResearchMemoryPanel
        density="full"
        controller={controller([], {
          skillCandidates: [candidate],
          skillActivationPreviews: { [candidate.id]: pendingPreview },
        })}
      />,
    );
    expect(screen.getByText("Rollback pending")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh status" })).toBeEnabled();
  });

  it("keeps valid awaiting previews when a failed-validation candidate is present", async () => {
    const invalid = { ...skillCandidate("invalid"), status: "failed-validation" as const };
    const valid = skillCandidate("valid");
    core.getResearchMemoryWorkspace.mockResolvedValue(workspace());
    core.listSkillCandidates.mockResolvedValue([invalid, valid]);
    core.getSkillActivationPreview.mockImplementation(async (_projectId, _workflowId, id) => {
      if (id !== valid.id) throw new Error("unexpected preview");
      return activationPreview(valid);
    });

    function Harness() {
      const panelController = useResearchMemoryWorkspace("project-1", "workflow-1");
      return <ResearchMemoryPanel density="full" controller={panelController} />;
    }

    render(<Harness />);
    expect(
      await screen.findByRole("button", { name: "Approve and activate" }),
    ).toBeEnabled();
    expect(core.getSkillActivationPreview).toHaveBeenCalledTimes(1);
    expect(core.getSkillActivationPreview).toHaveBeenCalledWith(
      "project-1",
      "workflow-1",
      valid.id,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(screen.getByText("Failed validation")).toBeInTheDocument();
  });

  it("keeps a workflow-filtered active activation rollbackable when candidate loading fails", async () => {
    const candidate = skillCandidate();
    const active = activationPreview(candidate, "active").latestActivation!;
    core.getResearchMemoryWorkspace.mockResolvedValue(workspace());
    core.listSkillCandidates.mockRejectedValue(new Error("Candidate ledger unavailable"));
    core.listSkillActivations.mockResolvedValue([active]);

    function Harness() {
      const panelController = useResearchMemoryWorkspace("project-1", "workflow-1");
      return <ResearchMemoryPanel density="full" controller={panelController} />;
    }

    render(<Harness />);
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Roll back" }));
    await waitFor(() =>
      expect(core.listSkillActivations).toHaveBeenCalledWith(
        "project-1",
        expect.objectContaining({
          workflowId: "workflow-1",
          signal: expect.any(AbortSignal),
        }),
      ),
    );
    expect(core.rollbackSkillActivation).toHaveBeenCalledWith(
      "project-1",
      active.id,
      expect.any(Object),
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });

  it("keeps approval retries idempotent for one preview and changes the key with approval evidence", async () => {
    const candidate = skillCandidate();
    const firstPreview = activationPreview(candidate);
    let currentPreview = firstPreview;
    core.getResearchMemoryWorkspace.mockResolvedValue(workspace());
    core.listSkillCandidates.mockResolvedValue([candidate]);
    core.getSkillActivationPreview.mockImplementation(async () => currentPreview);
    core.approveSkillActivation.mockRejectedValue(new Error("Retry later"));

    function Harness() {
      const panelController = useResearchMemoryWorkspace("project-1", "workflow-1");
      return <ResearchMemoryPanel density="full" controller={panelController} />;
    }

    render(<Harness />);
    const user = userEvent.setup();
    const approve = await screen.findByRole("button", { name: "Approve and activate" });
    await user.click(approve);
    await screen.findByText("Could not update procedure activation: Retry later");
    await user.click(screen.getByRole("button", { name: "Approve and activate" }));
    await waitFor(() => expect(core.approveSkillActivation).toHaveBeenCalledTimes(2));
    const firstKey = core.approveSkillActivation.mock.calls[0][4].idempotencyKey;
    const retryKey = core.approveSkillActivation.mock.calls[1][4].idempotencyKey;
    expect(retryKey).toBe(firstKey);

    currentPreview = { ...firstPreview, approvalSha256: "b".repeat(64) };
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await user.click(await screen.findByRole("button", { name: "Approve and activate" }));
    await waitFor(() => expect(core.approveSkillActivation).toHaveBeenCalledTimes(3));
    expect(core.approveSkillActivation.mock.calls[2][4].idempotencyKey).not.toBe(firstKey);
  });

  it("keeps memory review and procedure creation independent through completion and identity abort", async () => {
    const candidateMemory = memory("review", "candidate", "Review this memory");
    const remembered = {
      ...memory("remembered", "committed", "Ignored"),
      contentJson: { kind: "remembered-evidence", pageIndex: 0 },
    };
    let resolveReview: (() => void) | undefined;
    let resolveProcedure: (() => void) | undefined;
    let reviewSignal: AbortSignal | undefined;
    let procedureSignal: AbortSignal | undefined;
    core.getResearchMemoryWorkspace.mockImplementation(async (projectId, workflowId) =>
      workspace(projectId, workflowId, projectId === "project-1" ? [candidateMemory, remembered] : []),
    );
    core.resolveResearchMemoryCandidate.mockImplementation(
      (_projectId, _workflowId, _memoryId, _input, options) => {
        reviewSignal = options.signal;
        return new Promise<void>((resolve) => {
          resolveReview = resolve;
        });
      },
    );
    core.createSkillCandidate.mockImplementation(
      (_projectId, _workflowId, _input, options) => {
        procedureSignal = options.signal;
        return new Promise<void>((resolve) => {
          resolveProcedure = resolve;
        });
      },
    );

    function Harness({ projectId }: { projectId: string }) {
      const panelController = useResearchMemoryWorkspace(projectId, "workflow-1");
      return <ResearchMemoryPanel density="full" controller={panelController} />;
    }

    const { rerender } = render(<Harness projectId="project-1" />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Accept" }));
    await user.click(screen.getByRole("button", { name: "Save as reusable procedure" }));
    expect(reviewSignal?.aborted).toBe(false);
    expect(procedureSignal?.aborted).toBe(false);
    expect(screen.getByText("Saving…")).toBeInTheDocument();
    expect(screen.getByText("Evaluating procedure…")).toBeInTheDocument();

    resolveReview?.();
    resolveProcedure?.();
    await waitFor(() =>
      expect(screen.queryByText("Saving…")).not.toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.queryByText("Evaluating procedure…")).not.toBeInTheDocument(),
    );

    await user.click(screen.getByRole("button", { name: "Accept" }));
    await user.click(screen.getByRole("button", { name: "Save as reusable procedure" }));
    rerender(<Harness projectId="project-2" />);
    expect(reviewSignal?.aborted).toBe(true);
    expect(procedureSignal?.aborted).toBe(true);
    await waitFor(() =>
      expect(screen.queryByText("Saving…")).not.toBeInTheDocument(),
    );
    expect(screen.queryByText("Evaluating procedure…")).not.toBeInTheDocument();
    rerender(<Harness projectId="project-1" />);
    expect(
      await screen.findByRole("button", { name: "Save as reusable procedure" }),
    ).toBeEnabled();
    expect(screen.queryByText("Evaluating procedure…")).not.toBeInTheDocument();
  });

  it("aborts stale activation preview loading across workflow changes and keeps activation errors separate", async () => {
    const candidate = skillCandidate();
    let firstSignal: AbortSignal | undefined;
    let resolveFirst: ((value: SkillActivationPreview) => void) | undefined;
    core.getResearchMemoryWorkspace.mockImplementation(async (projectId, workflowId) =>
      workspace(projectId, workflowId),
    );
    core.listSkillCandidates.mockResolvedValue([candidate]);
    core.getSkillActivationPreview.mockImplementation(
      (_projectId, workflowId, _candidateId, options) => {
        if (workflowId === "workflow-1") {
          firstSignal = options.signal;
          return new Promise<SkillActivationPreview>((resolve) => {
            resolveFirst = resolve;
          });
        }
        return Promise.resolve({
          ...activationPreview(candidate, "blocked"),
          workflowId,
        });
      },
    );

    function Harness({ workflowId }: { workflowId: string }) {
      const panelController = useResearchMemoryWorkspace("project-1", workflowId);
      return <ResearchMemoryPanel density="full" controller={panelController} />;
    }

    const { rerender } = render(<Harness workflowId="workflow-1" />);
    await waitFor(() => expect(firstSignal).toBeDefined());
    rerender(<Harness workflowId="workflow-2" />);
    expect(await screen.findByText("Blocked")).toBeInTheDocument();
    expect(firstSignal?.aborted).toBe(true);
    resolveFirst?.(activationPreview(candidate));
    await waitFor(() =>
      expect(screen.queryByText("Awaiting approval")).not.toBeInTheDocument(),
    );

    const activationErrorController = controller([], {
      skillCandidates: [candidate],
      skillActivationPreviews: { [candidate.id]: activationPreview(candidate) },
      skillActivationError: { candidateId: candidate.id, message: "Target changed" },
    });
    rerender(
      <ResearchMemoryPanel density="full" controller={activationErrorController} />,
    );
    expect(
      screen.getByText("Could not update procedure activation: Target changed"),
    ).toHaveAttribute("role", "alert");
  });
});
