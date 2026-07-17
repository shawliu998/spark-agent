import { afterEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn();

vi.mock("@tauri-apps/api/core", () => ({ invoke }));

function setDesktop(value: boolean): void {
  if (value) {
    Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {} });
  } else {
    delete (window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
  }
}

async function loadBridge(desktop = true) {
  vi.resetModules();
  setDesktop(desktop);
  return import("./taskPlans");
}

afterEach(() => {
  invoke.mockReset();
  setDesktop(false);
});

describe("task-plan Tauri bridge", () => {
  it("uses the Rust snake_case commands with their stable payloads", async () => {
    const bridge = await loadBridge();
    const tasks = [{ id: "evidence", title: "Gather evidence", prompt: "Find primary sources." }];

    await bridge.createTaskPlanRecord({ planId: "plan_1", objective: "Test a hypothesis", tasks });
    await bridge.recordTaskSession({
      planId: "plan_1",
      taskId: "evidence",
      sessionId: "ses_1",
      agent: "researcher",
      requestedModel: "provider/terra",
      routeTier: "standard",
      matchedPreference: "terra",
    });
    await bridge.recordTaskSessionStatus({
      planId: "plan_1",
      sessionId: "ses_1",
      status: "running",
      error: null,
    });
    await bridge.recordTaskStartFailure({ planId: "plan_1", taskId: "evidence", error: "runtime unavailable" });
    await bridge.recordTaskSynthesis({
      planId: "plan_1",
      sessionId: "ses_synthesis",
      agent: "researcher",
      requestedModel: "provider/sol",
      routeTier: "deep",
      matchedPreference: "sol",
    });

    expect(invoke).toHaveBeenNthCalledWith(1, "create_task_plan", {
      planId: "plan_1",
      objective: "Test a hypothesis",
      tasks,
    });
    expect(invoke).toHaveBeenNthCalledWith(2, "record_task_session", {
      planId: "plan_1",
      taskId: "evidence",
      sessionId: "ses_1",
      agent: "researcher",
      requestedModel: "provider/terra",
      routeTier: "standard",
      matchedPreference: "terra",
    });
    expect(invoke).toHaveBeenNthCalledWith(3, "record_task_session_status", {
      planId: "plan_1",
      sessionId: "ses_1",
      status: "running",
      error: null,
    });
    expect(invoke).toHaveBeenNthCalledWith(4, "record_task_start_failure", {
      planId: "plan_1",
      taskId: "evidence",
      error: "runtime unavailable",
    });
    expect(invoke).toHaveBeenNthCalledWith(5, "record_task_synthesis", {
      planId: "plan_1",
      sessionId: "ses_synthesis",
      agent: "researcher",
      requestedModel: "provider/sol",
      routeTier: "deep",
      matchedPreference: "sol",
    });
  });

  it("reads the folded schema-v1 records without arguments", async () => {
    const bridge = await loadBridge();
    const plans = [
      {
        schemaVersion: 1,
        planId: "plan_1",
        objective: "Test a hypothesis",
        createdAt: 1,
        tasks: [
          {
            id: "evidence",
            title: "Gather evidence",
            prompt: "Find primary sources.",
            sessions: [],
            startFailures: [{ sessionId: null, error: "runtime unavailable", recordedAt: 2 }],
          },
        ],
        syntheses: [],
      },
    ];
    invoke.mockResolvedValueOnce(plans);

    await expect(bridge.listTaskPlans()).resolves.toEqual(plans);
    expect(invoke).toHaveBeenCalledWith("list_task_plans");
  });

  it("is a safe no-op in browser development", async () => {
    const bridge = await loadBridge(false);

    await bridge.createTaskPlanRecord({ planId: "plan_1", objective: "Objective", tasks: [] });
    await bridge.recordTaskSession({
      planId: "plan_1",
      taskId: "task_1",
      sessionId: "ses_1",
      agent: null,
      requestedModel: null,
      routeTier: null,
      matchedPreference: null,
    });
    await bridge.recordTaskSessionStatus({
      planId: "plan_1",
      sessionId: "ses_1",
      status: "completed",
    });
    await bridge.recordTaskStartFailure({ planId: "plan_1", taskId: "task_1", error: "no runtime" });
    await bridge.recordTaskSynthesis({
      planId: "plan_1",
      sessionId: "ses_synthesis",
      agent: null,
      requestedModel: null,
      routeTier: null,
      matchedPreference: null,
    });

    await expect(bridge.listTaskPlans()).resolves.toEqual([]);
    expect(invoke).not.toHaveBeenCalled();
  });
});
