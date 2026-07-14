import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ResearchWorkflowSnapshot } from "@spark/research-domain";
import { useResearchWorkflow } from "./useResearchWorkflow";

const core = vi.hoisted(() => ({
  listWorkflows: vi.fn(),
  getWorkflow: vi.fn(),
  listWorkflowEvents: vi.fn(),
  createWorkflow: vi.fn(),
  approveWorkflowPlan: vi.fn(),
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

beforeEach(() => {
  vi.clearAllMocks();
  core.listWorkflows.mockResolvedValue([]);
  core.getWorkflow.mockResolvedValue(snapshot());
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
});
