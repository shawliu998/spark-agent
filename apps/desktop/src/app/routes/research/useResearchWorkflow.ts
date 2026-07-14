import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ResearchWorkflowAllowedAction,
  ResearchWorkflowSnapshot,
  WorkflowEvent,
} from "@spark/research-domain";
import { scienceCore } from "@/lib/scienceCore";

export type WorkflowConnectionState =
  | "idle"
  | "connecting"
  | "live"
  | "reconnecting";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function idempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function mergeEvents(current: WorkflowEvent[], incoming: WorkflowEvent[]): WorkflowEvent[] {
  const byId = new Map(current.map((event) => [event.id, event]));
  for (const event of incoming) byId.set(event.id, event);
  return [...byId.values()]
    .sort((left, right) => left.sequence - right.sequence)
    .slice(-100);
}

function snapshotIsOlder(
  candidate: ResearchWorkflowSnapshot,
  current: ResearchWorkflowSnapshot,
): boolean {
  if (candidate.workflow.revision !== current.workflow.revision) {
    return candidate.workflow.revision < current.workflow.revision;
  }
  return candidate.eventCursor < current.eventCursor;
}

export interface ResearchWorkflowController {
  workflows: ResearchWorkflowSnapshot[];
  selectedWorkflowId: string | null;
  snapshot: ResearchWorkflowSnapshot | null;
  events: WorkflowEvent[];
  loadingList: boolean;
  loadingSnapshot: boolean;
  mutating: boolean;
  connection: WorkflowConnectionState;
  error: string | null;
  selectWorkflow: (workflowId: string) => void;
  startNew: () => void;
  refresh: () => Promise<void>;
  create: (goal: string) => Promise<void>;
  approvePlan: () => Promise<void>;
  cancel: () => Promise<void>;
  retry: () => Promise<void>;
  resume: () => Promise<void>;
}

export function useResearchWorkflow(projectId: string | null): ResearchWorkflowController {
  const [workflows, setWorkflows] = useState<ResearchWorkflowSnapshot[]>([]);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
  const [snapshot, setSnapshot] = useState<ResearchWorkflowSnapshot | null>(null);
  const [events, setEvents] = useState<WorkflowEvent[]>([]);
  const [loadingList, setLoadingList] = useState(false);
  const [loadingSnapshot, setLoadingSnapshot] = useState(false);
  const [mutating, setMutating] = useState(false);
  const [connection, setConnection] = useState<WorkflowConnectionState>("idle");
  const [error, setError] = useState<string | null>(null);
  const [listRefresh, setListRefresh] = useState(0);
  const selectedWorkflowIdRef = useRef<string | null>(null);
  const eventAfterRef = useRef(0);
  const snapshotRef = useRef<ResearchWorkflowSnapshot | null>(null);
  const mutationInFlightRef = useRef(false);
  const mutationControllerRef = useRef<AbortController | null>(null);
  const projectIdRef = useRef(projectId);
  const createIntentRef = useRef<{
    projectId: string;
    goal: string;
    idempotencyKey: string;
  } | null>(null);
  projectIdRef.current = projectId;

  const applySnapshot = useCallback((next: ResearchWorkflowSnapshot) => {
    const selected = snapshotRef.current;
    if (
      selected?.workflow.id === next.workflow.id &&
      snapshotIsOlder(next, selected)
    ) {
      return false;
    }
    snapshotRef.current = next;
    setSnapshot(next);
    setWorkflows((current) => {
      const existing = current.find(
        (item) => item.workflow.id === next.workflow.id,
      );
      if (existing && snapshotIsOlder(next, existing)) return current;
      return [
        next,
        ...current.filter((item) => item.workflow.id !== next.workflow.id),
      ];
    });
    return true;
  }, []);

  useEffect(() => {
    selectedWorkflowIdRef.current = selectedWorkflowId;
  }, [selectedWorkflowId]);

  useEffect(() => {
    createIntentRef.current = null;
    mutationControllerRef.current?.abort();
    mutationControllerRef.current = null;
    mutationInFlightRef.current = false;
    setMutating(false);
    return () => mutationControllerRef.current?.abort();
  }, [projectId]);

  useEffect(() => {
    const controller = new AbortController();
    setWorkflows([]);
    setSelectedWorkflowId(null);
    setSnapshot(null);
    snapshotRef.current = null;
    setEvents([]);
    eventAfterRef.current = 0;
    setError(null);
    setConnection("idle");
    setLoadingList(false);
    if (!projectId) return () => controller.abort();

    setLoadingList(true);
    void scienceCore
      .listWorkflows(projectId, { limit: 12, signal: controller.signal })
      .then((next) => {
        if (controller.signal.aborted) return;
        if (next.some((item) => item.workflow.projectId !== projectId)) {
          throw new Error("Science core returned a workflow outside the selected project");
        }
        setWorkflows(next);
        setSelectedWorkflowId(next[0]?.workflow.id ?? null);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingList(false);
      });

    return () => controller.abort();
  }, [listRefresh, projectId]);

  const refreshWorkflow = useCallback(
    async (workflowId: string, signal?: AbortSignal) => {
      const nextSnapshot = await scienceCore.getWorkflow(workflowId, { signal });
      if (signal?.aborted || selectedWorkflowIdRef.current !== workflowId) return;
      if (
        nextSnapshot.workflow.id !== workflowId ||
        nextSnapshot.workflow.projectId !== projectIdRef.current
      ) {
        throw new Error("Science core returned a workflow outside the selected project");
      }
      applySnapshot(nextSnapshot);

      let after = eventAfterRef.current;
      for (let pageNumber = 0; pageNumber < 3; pageNumber += 1) {
        const page = await scienceCore.listWorkflowEvents(workflowId, {
          after,
          limit: 50,
          signal,
        });
        if (signal?.aborted || selectedWorkflowIdRef.current !== workflowId) return;
        setEvents((current) => mergeEvents(current, page.events));
        after = page.nextAfter;
        eventAfterRef.current = after;
        if (!page.hasMore) break;
      }
    },
    [applySnapshot],
  );

  useEffect(() => {
    setSnapshot(null);
    snapshotRef.current = null;
    setEvents([]);
    eventAfterRef.current = 0;
    setError(null);
    if (!selectedWorkflowId) {
      setConnection("idle");
      return;
    }

    let stopped = false;
    let failures = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;

    const schedule = (delay: number) => {
      if (stopped) return;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        timer = null;
        void poll();
      }, delay);
    };

    const poll = async () => {
      if (stopped || (controller && !controller.signal.aborted)) return;
      const pollController = new AbortController();
      controller = pollController;
      setLoadingSnapshot(snapshotRef.current == null);
      setConnection(failures === 0 ? "connecting" : "reconnecting");
      try {
        await refreshWorkflow(selectedWorkflowId, pollController.signal);
        if (stopped || pollController.signal.aborted) return;
        failures = 0;
        setConnection("live");
        setError(null);
      } catch (reason) {
        if (stopped || pollController.signal.aborted) return;
        failures += 1;
        setConnection("reconnecting");
        setError(errorMessage(reason));
      } finally {
        if (
          !stopped &&
          !pollController.signal.aborted &&
          controller === pollController
        ) {
          setLoadingSnapshot(false);
          const hidden = typeof document !== "undefined" && document.hidden;
          const selected = snapshotRef.current;
          const active =
            selected != null &&
            selected.workflow.status !== "completed" &&
            selected.workflow.status !== "failed" &&
            selected.workflow.status !== "cancelled";
          const baseDelay = hidden ? 15_000 : active ? 1_500 : 10_000;
          schedule(Math.min(baseDelay * 2 ** failures, 30_000));
        }
        if (controller === pollController) controller = null;
      }
    };

    const onVisibilityChange = () => {
      if (document.hidden || stopped) return;
      if (timer) clearTimeout(timer);
      timer = null;
      controller?.abort();
      schedule(0);
    };

    document.addEventListener("visibilitychange", onVisibilityChange);
    void poll();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [refreshWorkflow, selectedWorkflowId]);

  const requireAction = useCallback((action: ResearchWorkflowAllowedAction) => {
    const current = snapshotRef.current;
    if (!current?.allowedActions.includes(action)) {
      throw new Error(`Workflow action is not currently allowed: ${action}`);
    }
    return current;
  }, []);

  const runMutation = useCallback(
    async (
      operation: (signal: AbortSignal) => Promise<ResearchWorkflowSnapshot>,
      onApplied?: (snapshot: ResearchWorkflowSnapshot) => void,
    ) => {
      if (mutationInFlightRef.current) return;
      const operationProjectId = projectIdRef.current;
      if (!operationProjectId) return;
      const controller = new AbortController();
      mutationControllerRef.current = controller;
      mutationInFlightRef.current = true;
      setMutating(true);
      setError(null);
      try {
        const next = await operation(controller.signal);
        if (
          controller.signal.aborted ||
          projectIdRef.current !== operationProjectId ||
          next.workflow.projectId !== operationProjectId
        ) {
          return;
        }
        if (applySnapshot(next)) onApplied?.(next);
      } catch (reason) {
        if (!controller.signal.aborted && projectIdRef.current === operationProjectId) {
          setError(errorMessage(reason));
        }
      } finally {
        if (mutationControllerRef.current === controller) {
          mutationControllerRef.current = null;
          mutationInFlightRef.current = false;
          setMutating(false);
        }
      }
    },
    [applySnapshot],
  );

  const create = useCallback(
    async (goal: string) => {
      if (!projectId) return;
      const normalizedGoal = goal.trim();
      const existingIntent = createIntentRef.current;
      const intent =
        existingIntent?.projectId === projectId &&
        existingIntent.goal === normalizedGoal
          ? existingIntent
          : {
              projectId,
              goal: normalizedGoal,
              idempotencyKey: idempotencyKey(),
            };
      createIntentRef.current = intent;
      await runMutation(
        (signal) =>
          scienceCore.createWorkflow(
            projectId,
            { goal: normalizedGoal, workflowType: "literature-synthesis" },
            { idempotencyKey: intent.idempotencyKey, signal },
          ),
        (next) => {
          createIntentRef.current = null;
          setSelectedWorkflowId(next.workflow.id);
        },
      );
    },
    [projectId, runMutation],
  );

  const approvePlan = useCallback(async () => {
    await runMutation((signal) => {
      const current = requireAction("approve-plan");
      if (!current.plan) throw new Error("The workflow plan is not available");
      const approval = current.pendingApprovals.find(
        (item) => item.kind === "plan" && item.planId === current.plan?.id,
      );
      if (!approval) throw new Error("The plan approval request is not available");
      return scienceCore.approveWorkflowPlan(
        current.workflow.id,
        {
          approvalId: approval.id,
          planId: current.plan.id,
          planVersion: current.plan.version,
          planSha256: current.plan.planSha256,
          expectedWorkflowRevision: current.workflow.revision,
        },
        { idempotencyKey: idempotencyKey(), signal },
      );
    });
  }, [requireAction, runMutation]);

  const cancel = useCallback(async () => {
    await runMutation((signal) => {
      const current = requireAction("cancel");
      return scienceCore.cancelWorkflow(
        current.workflow.id,
        { expectedWorkflowRevision: current.workflow.revision },
        { idempotencyKey: idempotencyKey(), signal },
      );
    });
  }, [requireAction, runMutation]);

  const retry = useCallback(async () => {
    await runMutation((signal) => {
      const current = requireAction("retry");
      return scienceCore.retryWorkflow(
        current.workflow.id,
        { expectedWorkflowRevision: current.workflow.revision },
        { idempotencyKey: idempotencyKey(), signal },
      );
    });
  }, [requireAction, runMutation]);

  const resume = useCallback(async () => {
    await runMutation((signal) => {
      const current = requireAction("resume");
      return scienceCore.resumeWorkflow(
        current.workflow.id,
        { expectedWorkflowRevision: current.workflow.revision },
        { idempotencyKey: idempotencyKey(), signal },
      );
    });
  }, [requireAction, runMutation]);

  const refresh = useCallback(async () => {
    if (!selectedWorkflowId) {
      if (projectId) setListRefresh((current) => current + 1);
      return;
    }
    const controller = new AbortController();
    try {
      await refreshWorkflow(selectedWorkflowId, controller.signal);
    } catch (reason) {
      if (!controller.signal.aborted) setError(errorMessage(reason));
    }
  }, [projectId, refreshWorkflow, selectedWorkflowId]);

  const selectWorkflow = useCallback((workflowId: string) => {
    setSelectedWorkflowId(workflowId);
  }, []);

  const startNew = useCallback(() => {
    setSelectedWorkflowId(null);
    setSnapshot(null);
    snapshotRef.current = null;
    setEvents([]);
    eventAfterRef.current = 0;
  }, []);

  return {
    workflows,
    selectedWorkflowId,
    snapshot,
    events,
    loadingList,
    loadingSnapshot,
    mutating,
    connection,
    error,
    selectWorkflow,
    startNew,
    refresh,
    create,
    approvePlan,
    cancel,
    retry,
    resume,
  };
}
