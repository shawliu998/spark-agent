import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  AgentResearchWorkflowSnapshot,
  ResearchWorkflowSnapshot,
} from "@spark/research-domain";
import { useResearchWorkflow } from "./useResearchWorkflow";

const core = vi.hoisted(() => ({
  listWorkflows: vi.fn(),
  listAgentRuns: vi.fn(),
  getWorkflow: vi.fn(),
  getAgentRun: vi.fn(),
  listWorkflowInteractions: vi.fn(),
  listWorkflowEvents: vi.fn(),
  createWorkflow: vi.fn(),
  createAgentRun: vi.fn(),
  respondToInteraction: vi.fn(),
  approveWorkflowPlan: vi.fn(),
  decideWorkflowAnalysisIntent: vi.fn(),
  acceptWorkflowReviewWarnings: vi.fn(),
  cancelWorkflow: vi.fn(),
  retryWorkflow: vi.fn(),
  resumeWorkflow: vi.fn(),
}));

vi.mock("@/lib/scienceCore", () => ({ scienceCore: core }));

function snapshot(
  status: ResearchWorkflowSnapshot["workflow"]["status"] = "planning",
  options: {
    workflowId?: string;
    projectId?: string;
    revision?: number;
    eventCursor?: number;
  } = {},
): ResearchWorkflowSnapshot {
  return {
    workflow: {
      id: options.workflowId ?? "workflow-1",
      projectId: options.projectId ?? "project-1",
      goal: "Compare studies",
      workflowType: "literature-synthesis",
      status,
      revision: options.revision ?? 1,
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
    eventCursor: options.eventCursor ?? 1,
  };
}

function pendingAgentSnapshot(): AgentResearchWorkflowSnapshot {
  return {
    workflow: {
      id: "agent-run-1",
      projectId: "project-1",
      goal: "Compare the paper with the dataset",
      mode: "autonomous",
      sourceIds: ["paper-1", "dataset-1"],
      workflowType: null,
      generationMode: "local-deterministic",
      status: "waiting-clarification",
      revision: 3,
      currentStepId: null,
      planVersion: null,
      retryCount: 0,
      blockingReason: null,
      cancelRequestedAt: null,
      createdAt: "2026-07-16T08:00:00Z",
      updatedAt: "2026-07-16T08:00:01Z",
      completedAt: null,
    },
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
    intentDecision: {
      id: "intent-decision-1",
      workflowId: "agent-run-1",
      intent: "clarification-required",
      confidence: 0.61,
      reasoningSummary: "The requested outcome is ambiguous.",
      selectedSourceIds: ["paper-1", "dataset-1"],
      missingInputs: ["Primary outcome"],
      proposedWorkflowType: "mixed-research",
      promptVersion: "intent-router-v1",
      inputSha256: "b".repeat(64),
      outputSha256: "c".repeat(64),
      createdAt: "2026-07-16T08:00:01Z",
    },
    interactions: [],
    allowedActions: ["cancel"],
    eventCursor: 3,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  core.listWorkflows.mockResolvedValue([]);
  core.listAgentRuns.mockResolvedValue([]);
  core.getWorkflow.mockResolvedValue(snapshot());
  core.getAgentRun.mockResolvedValue(snapshot());
  core.listWorkflowInteractions.mockResolvedValue([]);
  core.listWorkflowEvents.mockResolvedValue({ events: [], nextAfter: 1, hasMore: false });
});

describe("useResearchWorkflow", () => {
  it("refreshes the canonical snapshot before polling events", async () => {
    const order: string[] = [];
    core.listWorkflows.mockResolvedValue([snapshot()]);
    core.getWorkflow.mockImplementation(async () => {
      order.push("snapshot");
      return snapshot();
    });
    core.listWorkflowEvents.mockImplementation(async () => {
      order.push("events");
      return { events: [], nextAfter: 1, hasMore: false };
    });

    const { unmount } = renderHook(() => useResearchWorkflow("project-1"));

    await waitFor(() => expect(core.listWorkflowEvents).toHaveBeenCalled());
    expect(order.slice(0, 2)).toEqual(["snapshot", "events"]);
    unmount();
  });

  it("restores pending autonomous runs and their durable interactions", async () => {
    const agent = pendingAgentSnapshot();
    const interaction = {
      id: "interaction-1",
      workflowId: "agent-run-1",
      stepId: null,
      requestType: "single-choice" as const,
      question: "Which outcome should be primary?",
      options: [
        { value: "accuracy", label: "accuracy" },
        { value: "latency", label: "latency" },
      ],
      required: true,
      status: "pending" as const,
      responseSchema: { type: "string" },
      workflowRevision: 3,
      latestResponse: null,
      createdAt: "2026-07-16T08:00:01Z",
      answeredAt: null,
    };
    core.listAgentRuns.mockResolvedValue([agent]);
    core.getAgentRun.mockResolvedValue(agent);
    core.listWorkflowInteractions.mockResolvedValue([interaction]);

    const { result, unmount } = renderHook(() =>
      useResearchWorkflow("project-1"),
    );

    await waitFor(() => expect(result.current.snapshot?.workflow.id).toBe("agent-run-1"));
    await waitFor(() => expect(result.current.interactions).toEqual([interaction]));
    expect(core.getAgentRun).toHaveBeenCalledWith(
      "agent-run-1",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(core.listAgentRuns).toHaveBeenCalledWith(
      "project-1",
      expect.objectContaining({
        activeOnly: true,
        limit: 100,
        signal: expect.any(AbortSignal),
      }),
    );
    expect(core.getWorkflow).not.toHaveBeenCalled();
    unmount();
  });

  it("selects the newest workflow across fixed and autonomous lists", async () => {
    const fixed = snapshot("completed", {
      workflowId: "fixed-workflow-1",
    });
    const agent = pendingAgentSnapshot();
    core.listWorkflows.mockResolvedValue([fixed]);
    core.listAgentRuns.mockResolvedValue([agent]);
    core.getAgentRun.mockResolvedValue(agent);

    const { result, unmount } = renderHook(() =>
      useResearchWorkflow("project-1"),
    );

    await waitFor(() =>
      expect(result.current.selectedWorkflowId).toBe("agent-run-1"),
    );
    expect(result.current.workflows.map((item) => item.workflow.id)).toEqual([
      "agent-run-1",
      "fixed-workflow-1",
    ]);
    expect(core.getAgentRun).toHaveBeenCalled();
    expect(core.getWorkflow).not.toHaveBeenCalled();
    unmount();
  });

  it("creates Auto research from a canonical set of selected source IDs", async () => {
    const agent = pendingAgentSnapshot();
    core.createAgentRun.mockResolvedValue(agent);
    const { result, unmount } = renderHook(() =>
      useResearchWorkflow("project-1"),
    );
    await waitFor(() => expect(core.listAgentRuns).toHaveBeenCalled());

    await act(async () => {
      await result.current.create("  Compare sources  ", {
        mode: "autonomous",
        sourceIds: ["paper-1", "dataset-1", "paper-1"],
        remoteDataApproved: true,
      });
    });

    expect(core.createAgentRun).toHaveBeenCalledWith(
      "project-1",
      {
        goal: "Compare sources",
        sourceIds: ["dataset-1", "paper-1"],
        mode: "autonomous",
        remoteDataApproved: true,
      },
      expect.objectContaining({
        idempotencyKey: expect.any(String),
        signal: expect.any(AbortSignal),
      }),
    );
    expect(core.createWorkflow).not.toHaveBeenCalled();
    unmount();
  });

  it("rejects an Auto request without a ready source before calling the API", async () => {
    const { result, unmount } = renderHook(() =>
      useResearchWorkflow("project-1"),
    );
    await waitFor(() => expect(core.listAgentRuns).toHaveBeenCalled());

    await act(async () => {
      await result.current.create("Investigate this question", {
        mode: "autonomous",
        sourceIds: [],
        remoteDataApproved: false,
      });
    });

    expect(core.createAgentRun).not.toHaveBeenCalled();
    expect(result.current.error).toMatch(/Choose at least one ready PDF or CSV/i);
    unmount();
  });

  it("binds clarification answers to the current workflow revision", async () => {
    const agent = pendingAgentSnapshot();
    const interaction = {
      id: "interaction-1",
      workflowId: "agent-run-1",
      stepId: null,
      requestType: "text" as const,
      question: "What is the primary outcome?",
      options: [],
      required: true,
      status: "pending" as const,
      responseSchema: { type: "string" },
      workflowRevision: 3,
      latestResponse: null,
      createdAt: "2026-07-16T08:00:01Z",
      answeredAt: null,
    };
    core.listAgentRuns.mockResolvedValue([agent]);
    core.getAgentRun.mockResolvedValue(agent);
    core.listWorkflowInteractions.mockResolvedValue([interaction]);
    core.respondToInteraction.mockResolvedValue({
      ...agent,
      workflow: { ...agent.workflow, revision: 4, status: "routing" },
      interactions: [{ ...interaction, status: "answered" }],
    });
    const { result, unmount } = renderHook(() =>
      useResearchWorkflow("project-1"),
    );
    await waitFor(() => expect(result.current.interactions).toEqual([interaction]));

    await act(async () => {
      await result.current.respondToInteraction("interaction-1", "accuracy");
    });

    expect(core.respondToInteraction).toHaveBeenCalledWith(
      "interaction-1",
      { response: "accuracy", expectedWorkflowRevision: 3 },
      expect.objectContaining({
        idempotencyKey: expect.any(String),
        signal: expect.any(AbortSignal),
      }),
    );
    unmount();
  });

  it("submits an answered clarification again before plan approval", async () => {
    const pending = pendingAgentSnapshot();
    const interaction = {
      id: "interaction-1",
      workflowId: "agent-run-1",
      stepId: null,
      requestType: "single-choice" as const,
      question: "Which outcome should be primary?",
      options: [
        { value: "accuracy", label: "accuracy" },
        { value: "latency", label: "latency" },
      ],
      required: true,
      status: "answered" as const,
      responseSchema: { type: "string" },
      workflowRevision: 3,
      latestResponse: {
        id: "response-1",
        interactionId: "interaction-1",
        revision: 1,
        response: "accuracy",
        responseSha256: "a".repeat(64),
        createdAt: "2026-07-16T08:00:02Z",
      },
      createdAt: "2026-07-16T08:00:01Z",
      answeredAt: "2026-07-16T08:00:02Z",
    };
    const agent: AgentResearchWorkflowSnapshot = {
      ...pending,
      workflow: {
        ...pending.workflow,
        workflowType: "literature-synthesis",
        status: "waiting-plan-approval",
        revision: 6,
        planVersion: 1,
      },
      interactions: [interaction],
      allowedActions: ["approve-plan", "cancel"],
    };
    const rerouting: AgentResearchWorkflowSnapshot = {
      ...agent,
      workflow: {
        ...agent.workflow,
        workflowType: null,
        status: "routing",
        revision: 7,
        planVersion: null,
      },
      interactions: [
        {
          ...interaction,
          latestResponse: {
            ...interaction.latestResponse,
            revision: 2,
            response: "latency",
          },
        },
      ],
      plan: null,
      pendingApprovals: [],
      allowedActions: ["cancel"],
    };
    core.listAgentRuns.mockResolvedValue([agent]);
    core.getAgentRun.mockResolvedValue(agent);
    core.listWorkflowInteractions.mockResolvedValue([interaction]);
    core.respondToInteraction.mockResolvedValue(rerouting);

    const { result, unmount } = renderHook(() =>
      useResearchWorkflow("project-1"),
    );
    await waitFor(() => expect(result.current.interactions).toEqual([interaction]));

    await act(async () => {
      await result.current.respondToInteraction("interaction-1", "latency");
    });

    expect(core.respondToInteraction).toHaveBeenCalledWith(
      "interaction-1",
      { response: "latency", expectedWorkflowRevision: 6 },
      expect.objectContaining({
        idempotencyKey: expect.any(String),
        signal: expect.any(AbortSignal),
      }),
    );
    unmount();
  });

  it("prevents duplicate workflow mutations before React rerenders", async () => {
    let resolveCreate: ((value: ResearchWorkflowSnapshot) => void) | null = null;
    core.createWorkflow.mockImplementation(
      () =>
        new Promise<ResearchWorkflowSnapshot>((resolve) => {
          resolveCreate = resolve;
        }),
    );
    const { result, unmount } = renderHook(() => useResearchWorkflow("project-1"));
    await waitFor(() => expect(core.listWorkflows).toHaveBeenCalled());

    let first: Promise<void> | undefined;
    let second: Promise<void> | undefined;
    act(() => {
      first = result.current.create("Compare studies");
      second = result.current.create("Compare studies");
    });
    expect(core.createWorkflow).toHaveBeenCalledTimes(1);

    await act(async () => {
      if (!resolveCreate) throw new Error("create resolver was not initialized");
      resolveCreate(snapshot());
      await Promise.all([first, second]);
    });
    unmount();
  });

  it("reuses the create idempotency key when the same intent is retried", async () => {
    core.createWorkflow
      .mockRejectedValueOnce(new Error("connection dropped"))
      .mockResolvedValueOnce(snapshot());
    const { result, unmount } = renderHook(() =>
      useResearchWorkflow("project-1"),
    );
    await waitFor(() => expect(core.listWorkflows).toHaveBeenCalled());

    await act(async () => {
      await result.current.create("Compare studies");
    });
    await act(async () => {
      await result.current.create("Compare studies");
    });

    expect(core.createWorkflow).toHaveBeenCalledTimes(2);
    const firstOptions = core.createWorkflow.mock.calls[0]?.[2];
    const retryOptions = core.createWorkflow.mock.calls[1]?.[2];
    expect(firstOptions.idempotencyKey).toBeTruthy();
    expect(retryOptions.idempotencyKey).toBe(firstOptions.idempotencyKey);
    expect(core.createWorkflow.mock.calls[0]?.[1]).toEqual({
      goal: "Compare studies",
      workflowType: "literature-synthesis",
      generationMode: "local-deterministic",
      remoteDataApproved: false,
    });
    unmount();
  });

  it("treats generation mode and remote approval as part of the create intent", async () => {
    core.createWorkflow
      .mockRejectedValueOnce(new Error("connection dropped"))
      .mockResolvedValueOnce(snapshot());
    const { result, unmount } = renderHook(() =>
      useResearchWorkflow("project-1"),
    );
    await waitFor(() => expect(core.listWorkflows).toHaveBeenCalled());

    await act(async () => {
      await result.current.create(
        "Compare studies",
        {
          workflowType: "literature-synthesis",
          datasetSourceId: null,
          generationMode: "remote-model-assisted",
          remoteDataApproved: true,
        },
      );
    });
    await act(async () => {
      await result.current.create(
        "Compare studies",
        {
          workflowType: "literature-synthesis",
          datasetSourceId: null,
          generationMode: "local-deterministic",
          remoteDataApproved: false,
        },
      );
    });

    expect(core.createWorkflow).toHaveBeenCalledTimes(2);
    expect(core.createWorkflow.mock.calls[0]?.[1]).toEqual({
      goal: "Compare studies",
      workflowType: "literature-synthesis",
      generationMode: "remote-model-assisted",
      remoteDataApproved: true,
    });
    expect(core.createWorkflow.mock.calls[1]?.[1]).toEqual({
      goal: "Compare studies",
      workflowType: "literature-synthesis",
      generationMode: "local-deterministic",
      remoteDataApproved: false,
    });
    expect(core.createWorkflow.mock.calls[1]?.[2].idempotencyKey).not.toBe(
      core.createWorkflow.mock.calls[0]?.[2].idempotencyKey,
    );
    unmount();
  });

  it("isolates in-flight mutations when the selected project changes", async () => {
    const resolvers = new Map<
      string,
      (value: ResearchWorkflowSnapshot) => void
    >();
    core.createWorkflow.mockImplementation(
      (projectId: string) =>
        new Promise<ResearchWorkflowSnapshot>((resolve) => {
          resolvers.set(projectId, resolve);
        }),
    );
    core.getWorkflow.mockImplementation(async (workflowId: string) =>
      snapshot("planning", {
        workflowId,
        projectId: workflowId === "workflow-2" ? "project-2" : "project-1",
        revision: 2,
        eventCursor: 2,
      }),
    );
    const { result, rerender, unmount } = renderHook(
      ({ projectId }) => useResearchWorkflow(projectId),
      { initialProps: { projectId: "project-1" } },
    );
    await waitFor(() => expect(core.listWorkflows).toHaveBeenCalled());

    act(() => {
      void result.current.create("Compare project one");
    });
    await waitFor(() => expect(resolvers.has("project-1")).toBe(true));

    rerender({ projectId: "project-2" });
    await waitFor(() => expect(result.current.mutating).toBe(false));
    act(() => {
      void result.current.create("Compare project two");
    });
    await waitFor(() => expect(resolvers.has("project-2")).toBe(true));

    await act(async () => {
      resolvers.get("project-2")?.(
        snapshot("planning", {
          workflowId: "workflow-2",
          projectId: "project-2",
          revision: 2,
          eventCursor: 2,
        }),
      );
    });
    await waitFor(() =>
      expect(result.current.snapshot?.workflow.projectId).toBe("project-2"),
    );

    await act(async () => {
      resolvers.get("project-1")?.(
        snapshot("planning", {
          workflowId: "workflow-1",
          projectId: "project-1",
          revision: 99,
          eventCursor: 99,
        }),
      );
    });
    expect(result.current.snapshot?.workflow.projectId).toBe("project-2");
    expect(result.current.selectedWorkflowId).toBe("workflow-2");
    unmount();
  });

  it.each(["list-first", "create-first"] as const)(
    "keeps a newly created workflow selected when the initial list resolves %s",
    async (responseOrder) => {
      const existing = snapshot("completed", {
        workflowId: "workflow-existing",
        revision: 3,
        eventCursor: 3,
      });
      const created = snapshot("planning", {
        workflowId: "workflow-created",
        revision: 1,
        eventCursor: 1,
      });
      let resolveList:
        | ((value: ResearchWorkflowSnapshot[]) => void)
        | null = null;
      let resolveCreate:
        | ((value: ResearchWorkflowSnapshot) => void)
        | null = null;
      core.listWorkflows.mockImplementation(
        () =>
          new Promise<ResearchWorkflowSnapshot[]>((resolve) => {
            resolveList = resolve;
          }),
      );
      core.createWorkflow.mockImplementation(
        () =>
          new Promise<ResearchWorkflowSnapshot>((resolve) => {
            resolveCreate = resolve;
          }),
      );
      core.getWorkflow.mockResolvedValue(created);

      const { result, unmount } = renderHook(() =>
        useResearchWorkflow("project-1"),
      );
      await waitFor(() => expect(core.listWorkflows).toHaveBeenCalledTimes(1));

      let createRequest: Promise<void> | undefined;
      act(() => {
        createRequest = result.current.create("Create while list is loading");
      });
      await waitFor(() => expect(core.createWorkflow).toHaveBeenCalledTimes(1));

      if (responseOrder === "list-first") {
        await act(async () => {
          if (!resolveList) throw new Error("list resolver missing");
          resolveList([existing]);
        });
        expect(result.current.selectedWorkflowId).toBeNull();
        expect(result.current.workflows).toEqual([existing]);
      }

      await act(async () => {
        if (!resolveCreate) throw new Error("create resolver missing");
        resolveCreate(created);
        await createRequest;
      });
      await waitFor(() =>
        expect(result.current.selectedWorkflowId).toBe("workflow-created"),
      );

      if (responseOrder === "create-first") {
        await act(async () => {
          if (!resolveList) throw new Error("list resolver missing");
          resolveList([existing]);
        });
      }

      expect(result.current.selectedWorkflowId).toBe("workflow-created");
      expect(result.current.snapshot).toEqual(created);
      expect(
        result.current.workflows.map((item) => item.workflow.id),
      ).toEqual(["workflow-created", "workflow-existing"]);
      unmount();
    },
  );

  it("rejects late mutation snapshots after selecting another workflow or starting new", async () => {
    const workflowA = snapshot("running", {
      workflowId: "workflow-a",
      revision: 2,
      eventCursor: 2,
    });
    const workflowB = snapshot("running", {
      workflowId: "workflow-b",
      revision: 4,
      eventCursor: 4,
    });
    const resolvers = new Map<
      string,
      (value: ResearchWorkflowSnapshot) => void
    >();
    core.listWorkflows.mockResolvedValue([workflowA, workflowB]);
    core.getWorkflow.mockImplementation(async (workflowId: string) =>
      workflowId === "workflow-b" ? workflowB : workflowA,
    );
    core.cancelWorkflow.mockImplementation(
      (workflowId: string) =>
        new Promise<ResearchWorkflowSnapshot>((resolve) => {
          resolvers.set(workflowId, resolve);
        }),
    );

    const { result, unmount } = renderHook(() =>
      useResearchWorkflow("project-1"),
    );
    await waitFor(() => expect(result.current.snapshot).toEqual(workflowA));

    let mutationA: Promise<void> | undefined;
    act(() => {
      mutationA = result.current.cancel();
    });
    await waitFor(() => expect(resolvers.has("workflow-a")).toBe(true));
    const signalA = core.cancelWorkflow.mock.calls[0]?.[2]
      ?.signal as AbortSignal;

    act(() => result.current.selectWorkflow("workflow-b"));
    expect(signalA.aborted).toBe(true);
    await waitFor(() => {
      expect(result.current.selectedWorkflowId).toBe("workflow-b");
      expect(result.current.snapshot).toEqual(workflowB);
    });

    let mutationB: Promise<void> | undefined;
    act(() => {
      mutationB = result.current.cancel();
    });
    await waitFor(() => expect(resolvers.has("workflow-b")).toBe(true));
    const signalB = core.cancelWorkflow.mock.calls[1]?.[2]
      ?.signal as AbortSignal;

    await act(async () => {
      resolvers.get("workflow-a")?.(
        snapshot("cancelled", {
          workflowId: "workflow-a",
          revision: 99,
          eventCursor: 99,
        }),
      );
      await mutationA;
    });
    expect(result.current.selectedWorkflowId).toBe("workflow-b");
    expect(result.current.snapshot).toEqual(workflowB);
    expect(result.current.mutating).toBe(true);

    act(() => result.current.startNew());
    expect(signalB.aborted).toBe(true);
    expect(result.current.selectedWorkflowId).toBeNull();
    expect(result.current.snapshot).toBeNull();

    await act(async () => {
      resolvers.get("workflow-b")?.(
        snapshot("cancelled", {
          workflowId: "workflow-b",
          revision: 100,
          eventCursor: 100,
        }),
      );
      await mutationB;
    });
    expect(result.current.selectedWorkflowId).toBeNull();
    expect(result.current.snapshot).toBeNull();
    expect(result.current.mutating).toBe(false);
    unmount();
  });

  it("clears list loading when the selected project is removed", async () => {
    let resolveList: ((value: ResearchWorkflowSnapshot[]) => void) | null = null;
    core.listWorkflows.mockImplementation(
      () =>
        new Promise<ResearchWorkflowSnapshot[]>((resolve) => {
          resolveList = resolve;
        }),
    );
    const { result, rerender, unmount } = renderHook(
      ({ projectId }: { projectId: string | null }) =>
        useResearchWorkflow(projectId),
      {
        initialProps: { projectId: "project-1" } as {
          projectId: string | null;
        },
      },
    );
    await waitFor(() => expect(result.current.loadingList).toBe(true));

    rerender({ projectId: null });
    await waitFor(() => expect(result.current.loadingList).toBe(false));
    expect(result.current.workflows).toEqual([]);

    await act(async () => {
      if (!resolveList) throw new Error("list resolver was not initialized");
      resolveList([snapshot()]);
    });
    expect(result.current.workflows).toEqual([]);
    unmount();
  });

  it("does not let an older refresh overwrite a newer mutation snapshot", async () => {
    const initial = snapshot("running", { revision: 1, eventCursor: 1 });
    const stale = snapshot("running", { revision: 2, eventCursor: 2 });
    const cancelled = snapshot("cancelled", { revision: 3, eventCursor: 3 });
    let resolveStale: ((value: ResearchWorkflowSnapshot) => void) | null = null;
    core.listWorkflows.mockResolvedValue([initial]);
    core.getWorkflow
      .mockResolvedValueOnce(initial)
      .mockImplementationOnce(
        () =>
          new Promise<ResearchWorkflowSnapshot>((resolve) => {
            resolveStale = resolve;
          }),
      );
    core.cancelWorkflow.mockResolvedValue(cancelled);
    const { result, unmount } = renderHook(() =>
      useResearchWorkflow("project-1"),
    );
    await waitFor(() => expect(result.current.snapshot).toEqual(initial));

    let refreshPromise: Promise<void> | undefined;
    act(() => {
      refreshPromise = result.current.refresh();
    });
    await waitFor(() => expect(core.getWorkflow).toHaveBeenCalledTimes(2));
    await act(async () => {
      await result.current.cancel();
    });
    expect(result.current.snapshot?.workflow.status).toBe("cancelled");

    await act(async () => {
      if (!resolveStale) throw new Error("refresh resolver was not initialized");
      resolveStale(stale);
      await refreshPromise;
    });
    expect(result.current.snapshot?.workflow.revision).toBe(3);
    expect(result.current.snapshot?.workflow.status).toBe("cancelled");
    unmount();
  });

  it("recovers polling after a transient connection failure", async () => {
    core.listWorkflows.mockResolvedValue([snapshot()]);
    core.getWorkflow
      .mockRejectedValueOnce(new Error("connection dropped"))
      .mockResolvedValueOnce(
        snapshot("running", { revision: 2, eventCursor: 2 }),
      );
    const { result, unmount } = renderHook(() =>
      useResearchWorkflow("project-1"),
    );

    await waitFor(() => expect(result.current.connection).toBe("reconnecting"));
    expect(result.current.error).toBe("connection dropped");

    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    await waitFor(() => expect(result.current.connection).toBe("live"));
    expect(result.current.snapshot?.workflow.revision).toBe(2);
    expect(result.current.error).toBeNull();
    unmount();
  });

  it.each(["approved", "rejected"] as const)(
    "sends the exact workflow analysis %s decision once",
    async (decision) => {
      const initial = datasetApprovalSnapshot();
      const decided = {
        ...initial,
        workflow: { ...initial.workflow, revision: 6 },
        pendingApprovals: [],
        analysisIntent: {
          ...initial.analysisIntent!,
          status: decision === "approved" ? "approved" : "rejected",
          decision,
        },
        allowedActions: ["cancel"],
        eventCursor: 6,
      } as unknown as ResearchWorkflowSnapshot;
      let resolveDecision:
        | ((value: ResearchWorkflowSnapshot) => void)
        | null = null;
      core.listWorkflows.mockResolvedValue([initial]);
      core.getWorkflow.mockResolvedValue(initial);
      core.decideWorkflowAnalysisIntent.mockImplementation(
        () =>
          new Promise<ResearchWorkflowSnapshot>((resolve) => {
            resolveDecision = resolve;
          }),
      );

      const { result, unmount } = renderHook(() =>
        useResearchWorkflow("project-1"),
      );
      await waitFor(() => expect(result.current.snapshot).toEqual(initial));

      let first: Promise<void> | undefined;
      let duplicate: Promise<void> | undefined;
      act(() => {
        first = result.current.decideAnalysis(decision);
        duplicate = result.current.decideAnalysis(decision);
      });
      expect(core.decideWorkflowAnalysisIntent).toHaveBeenCalledTimes(1);
      expect(core.decideWorkflowAnalysisIntent).toHaveBeenCalledWith(
        "workflow-dataset",
        {
          intentId: "intent-1",
          approvalId: "approval-analysis",
          decision,
          payloadSha256: "b".repeat(64),
          expectedWorkflowRevision: 5,
        },
        expect.objectContaining({
          idempotencyKey: expect.any(String),
          signal: expect.any(AbortSignal),
        }),
      );

      await act(async () => {
        if (!resolveDecision) throw new Error("decision resolver missing");
        resolveDecision(decided);
        await Promise.all([first, duplicate]);
      });
      expect(result.current.snapshot?.workflow.revision).toBe(6);
      unmount();
    },
  );

  it("accepts only the exact warning-bearing review and applies the returned snapshot", async () => {
    const initial = datasetWarningSnapshot();
    const completed = {
      ...initial,
      workflow: {
        ...initial.workflow,
        status: "completed",
        revision: 9,
        completedAt: "2026-07-15T00:02:00Z",
      },
      allowedActions: [],
      eventCursor: 9,
    } as unknown as ResearchWorkflowSnapshot;
    core.listWorkflows.mockResolvedValue([initial]);
    core.getWorkflow.mockResolvedValue(initial);
    core.acceptWorkflowReviewWarnings.mockResolvedValue(completed);

    const { result, unmount } = renderHook(() =>
      useResearchWorkflow("project-1"),
    );
    await waitFor(() => expect(result.current.snapshot).toEqual(initial));

    await act(async () => {
      await result.current.acceptReviewWarnings();
    });

    expect(core.acceptWorkflowReviewWarnings).toHaveBeenCalledWith(
      "workflow-dataset",
      {
        reviewId: "review-1",
        reviewInputSha256: "c".repeat(64),
        expectedWorkflowRevision: 8,
        decision: "accepted",
      },
      expect.objectContaining({
        idempotencyKey: expect.any(String),
        signal: expect.any(AbortSignal),
      }),
    );
    expect(result.current.snapshot?.workflow.status).toBe("completed");
    unmount();
  });

  it("retries a failed dataset workflow with the exact revision and idempotency envelope", async () => {
    const initial = datasetRecoverySnapshot("failed", "retry", 11);
    const retried = {
      ...initial,
      workflow: {
        ...initial.workflow,
        status: "planning",
        revision: 12,
        blockingReason: null,
      },
      allowedActions: ["cancel"],
      eventCursor: 12,
    } as unknown as ResearchWorkflowSnapshot;
    core.listWorkflows.mockResolvedValue([initial]);
    core.getWorkflow.mockResolvedValue(initial);
    core.retryWorkflow.mockResolvedValue(retried);

    const { result, unmount } = renderHook(() =>
      useResearchWorkflow("project-1"),
    );
    await waitFor(() => expect(result.current.snapshot).toEqual(initial));

    await act(async () => {
      await result.current.retry();
    });

    expect(core.retryWorkflow).toHaveBeenCalledTimes(1);
    expect(core.retryWorkflow.mock.calls[0]).toEqual([
      "workflow-dataset",
      { expectedWorkflowRevision: 11 },
      {
        idempotencyKey: expect.any(String),
        signal: expect.any(AbortSignal),
      },
    ]);
    expect(result.current.snapshot?.workflow.revision).toBe(12);
    unmount();
  });

  it("resumes a blocked dataset workflow with the exact revision and idempotency envelope", async () => {
    const initial = datasetRecoverySnapshot("blocked", "resume", 13);
    const resumed = {
      ...initial,
      workflow: {
        ...initial.workflow,
        status: "running",
        revision: 14,
        blockingReason: null,
      },
      allowedActions: ["cancel"],
      eventCursor: 14,
    } as unknown as ResearchWorkflowSnapshot;
    core.listWorkflows.mockResolvedValue([initial]);
    core.getWorkflow.mockResolvedValue(initial);
    core.resumeWorkflow.mockResolvedValue(resumed);

    const { result, unmount } = renderHook(() =>
      useResearchWorkflow("project-1"),
    );
    await waitFor(() => expect(result.current.snapshot).toEqual(initial));

    await act(async () => {
      await result.current.resume();
    });

    expect(core.resumeWorkflow).toHaveBeenCalledTimes(1);
    expect(core.resumeWorkflow.mock.calls[0]).toEqual([
      "workflow-dataset",
      { expectedWorkflowRevision: 13 },
      {
        idempotencyKey: expect.any(String),
        signal: expect.any(AbortSignal),
      },
    ]);
    expect(result.current.snapshot?.workflow.revision).toBe(14);
    unmount();
  });

  it("refreshes the canonical dataset snapshot after a stale 409 decision", async () => {
    const initial = datasetApprovalSnapshot();
    const canonical = {
      ...initial,
      workflow: { ...initial.workflow, revision: 6 },
      eventCursor: 6,
    } as unknown as ResearchWorkflowSnapshot;
    core.listWorkflows.mockResolvedValue([initial]);
    core.getWorkflow.mockResolvedValueOnce(initial).mockResolvedValue(canonical);
    core.decideWorkflowAnalysisIntent.mockRejectedValue(
      apiError(409, "The workflow changed; review the refreshed approval."),
    );

    const { result, unmount } = renderHook(() =>
      useResearchWorkflow("project-1"),
    );
    await waitFor(() => expect(result.current.snapshot).toEqual(initial));

    await act(async () => {
      await result.current.decideAnalysis("approved");
    });

    expect(core.getWorkflow).toHaveBeenCalledTimes(2);
    expect(result.current.snapshot?.workflow.revision).toBe(6);
    expect(result.current.error).toBe(
      "The workflow changed; review the refreshed approval.",
    );
    unmount();
  });

  it.each([
    [401, "Authentication expired. Reconnect science core."],
    [503, "The local runtime is unavailable."],
  ])("surfaces API %s without inventing local workflow state", async (status, detail) => {
    const initial = snapshot("running");
    core.listWorkflows.mockResolvedValue([initial]);
    core.getWorkflow.mockResolvedValue(initial);
    core.cancelWorkflow.mockRejectedValue(apiError(status, detail));

    const { result, unmount } = renderHook(() =>
      useResearchWorkflow("project-1"),
    );
    await waitFor(() => expect(result.current.snapshot).toEqual(initial));

    await act(async () => {
      await result.current.cancel();
    });

    expect(result.current.error).toBe(detail);
    expect(result.current.snapshot).toEqual(initial);
    unmount();
  });
});

function datasetApprovalSnapshot(): ResearchWorkflowSnapshot {
  return {
    workflow: {
      id: "workflow-dataset",
      projectId: "project-1",
      goal: "Summarize outcomes",
      workflowType: "dataset-analysis",
      datasetSourceId: "dataset-1",
      datasetContentHash: "a".repeat(64),
      generationMode: "local-deterministic",
      status: "running",
      revision: 5,
      currentStepId: "task-execute",
      planVersion: 1,
      retryCount: 0,
      blockingReason: null,
      cancelRequestedAt: null,
      createdAt: "2026-07-15T00:00:00Z",
      updatedAt: "2026-07-15T00:00:05Z",
      completedAt: null,
    },
    plan: null,
    pendingApprovals: [
      {
        id: "approval-analysis",
        workflowId: "workflow-dataset",
        planId: "plan-1",
        taskId: "task-execute",
        kind: "analysis-execution",
        status: "waiting",
        subjectType: "analysis-intent",
        subjectId: "intent-1",
        action: "execute-python-data-analysis",
        payloadSha256: "b".repeat(64),
        riskLevel: "high",
        reason: "Review exact code.",
        affectedResources: ["dataset:dataset-1"],
        createdAt: "2026-07-15T00:00:05Z",
        decidedAt: null,
        approvalSchemaVersion: "analysis-intent-v3",
        expectedWorkflowRevision: 5,
        analysisIntentId: "intent-1",
        planStepId: "execute-analysis",
        datasetSourceId: "dataset-1",
        datasetContentHash: "a".repeat(64),
        expectedOutputs: [
          "executed-notebook",
          "analysis-log",
          "environment-manifest",
        ],
        timeoutSeconds: 600,
        code: "print('approved')",
        codeDiff: null,
      },
    ],
    result: null,
    latestReview: null,
    datasetProfile: null,
    analysisIntent: {
      id: "intent-1",
      taskId: "task-execute",
      projectId: "project-1",
      datasetSourceId: "dataset-1",
      datasetContentHash: "a".repeat(64),
      objective: "Summarize outcomes",
      code: "print('approved')",
      payloadSha256: "b".repeat(64),
      riskLevel: "high",
      affectedResources: ["dataset:dataset-1"],
      status: "waiting-approval",
      decision: null,
      workflowId: "workflow-dataset",
      planStepId: "execute-analysis",
      previousIntentId: null,
      expectedOutputs: [
        "executed-notebook",
        "analysis-log",
        "environment-manifest",
      ],
      timeoutSeconds: 600,
      repairAttempt: 0,
      errorSummary: null,
      codeDiff: null,
      createdAt: "2026-07-15T00:00:05Z",
      updatedAt: "2026-07-15T00:00:05Z",
    },
    analysisRun: null,
    reviewWarningAcceptance: null,
    allowedActions: ["approve-analysis", "reject-analysis", "cancel"],
    eventCursor: 5,
  } as unknown as ResearchWorkflowSnapshot;
}

function datasetWarningSnapshot(): ResearchWorkflowSnapshot {
  const initial = datasetApprovalSnapshot();
  return {
    ...initial,
    workflow: {
      ...initial.workflow,
      status: "reviewing",
      revision: 8,
      currentStepId: null,
    },
    pendingApprovals: [],
    latestReview: {
      id: "review-1",
      reviewType: "deterministic-analysis-v1",
      verdict: "passed-with-warnings",
      inputSha256: "c".repeat(64),
      createdAt: "2026-07-15T00:01:00Z",
      result: {
        schemaVersion: "1",
        verdict: "passed-with-warnings",
        checks: [],
        artifactIssues: [],
        numericIssues: [],
        methodWarnings: [
          {
            code: "method-scope-limited",
            message: "Confirm the descriptive method.",
            artifactId: null,
          },
        ],
        requiredRevisions: [],
        runId: "run-1",
        analysisIntentId: "intent-1",
        inputDatasetContentHash: "a".repeat(64),
      },
    },
    allowedActions: ["accept-review-warnings", "cancel"],
    eventCursor: 8,
  } as unknown as ResearchWorkflowSnapshot;
}

function datasetRecoverySnapshot(
  status: "blocked" | "failed",
  action: "resume" | "retry",
  revision: number,
): ResearchWorkflowSnapshot {
  const initial = datasetApprovalSnapshot();
  return {
    ...initial,
    workflow: {
      ...initial.workflow,
      status,
      revision,
      currentStepId: null,
      blockingReason: {
        code: `${status}-for-test`,
        userMessage: `Dataset workflow ${status}.`,
        retryable: true,
      },
    },
    pendingApprovals: [],
    allowedActions: [action],
    eventCursor: revision,
  } as unknown as ResearchWorkflowSnapshot;
}

function apiError(status: number, detail: string): Error & { status: number } {
  return Object.assign(new Error(detail), { status });
}
