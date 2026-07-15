import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import type {
  ResearchWorkflowSnapshot,
  WorkflowEvent,
} from "@spark/research-domain";
import { scienceCore } from "@/lib/scienceCore";
import {
  errorMessage,
  mergeWorkflowEvents,
  snapshotIsOlder,
} from "./workflowModel";

export type WorkflowConnectionState =
  | "idle"
  | "connecting"
  | "live"
  | "reconnecting";

interface UseWorkflowEventsOptions {
  projectId: string | null;
  selectedWorkflowId: string | null;
  setError: Dispatch<SetStateAction<string | null>>;
  onSnapshotApplied: (snapshot: ResearchWorkflowSnapshot) => void;
}

interface WorkflowEventsController {
  snapshot: ResearchWorkflowSnapshot | null;
  events: WorkflowEvent[];
  loadingSnapshot: boolean;
  connection: WorkflowConnectionState;
  applySnapshot: (
    snapshot: ResearchWorkflowSnapshot,
    expectedWorkflowId: string,
    allowUnselectedCreate?: boolean,
  ) => boolean;
  currentSnapshot: () => ResearchWorkflowSnapshot | null;
  clearSelectionState: () => void;
  refreshSelected: () => Promise<void>;
}

export function useWorkflowEvents({
  projectId,
  selectedWorkflowId,
  setError,
  onSnapshotApplied,
}: UseWorkflowEventsOptions): WorkflowEventsController {
  const [snapshot, setSnapshot] = useState<ResearchWorkflowSnapshot | null>(null);
  const [events, setEvents] = useState<WorkflowEvent[]>([]);
  const [loadingSnapshot, setLoadingSnapshot] = useState(false);
  const [connection, setConnection] =
    useState<WorkflowConnectionState>("idle");
  const selectedWorkflowIdRef = useRef<string | null>(null);
  const eventAfterRef = useRef(0);
  const snapshotRef = useRef<ResearchWorkflowSnapshot | null>(null);
  const projectIdRef = useRef(projectId);
  projectIdRef.current = projectId;
  selectedWorkflowIdRef.current = selectedWorkflowId;

  const clearSelectionState = useCallback(() => {
    setSnapshot(null);
    snapshotRef.current = null;
    setEvents([]);
    eventAfterRef.current = 0;
    setLoadingSnapshot(false);
    setConnection("idle");
  }, []);

  const applySnapshot = useCallback(
    (
      next: ResearchWorkflowSnapshot,
      expectedWorkflowId: string,
      allowUnselectedCreate = false,
    ) => {
      const selectedWorkflowId = selectedWorkflowIdRef.current;
      if (
        next.workflow.projectId !== projectIdRef.current ||
        next.workflow.id !== expectedWorkflowId ||
        (selectedWorkflowId !== expectedWorkflowId &&
          !(allowUnselectedCreate && selectedWorkflowId === null))
      ) {
        return false;
      }
      const selected = snapshotRef.current;
      if (
        selected?.workflow.id === next.workflow.id &&
        snapshotIsOlder(next, selected)
      ) {
        return false;
      }
      snapshotRef.current = next;
      setSnapshot(next);
      onSnapshotApplied(next);
      return true;
    },
    [onSnapshotApplied],
  );

  const currentSnapshot = useCallback(() => snapshotRef.current, []);

  useEffect(() => {
    clearSelectionState();
  }, [clearSelectionState, projectId]);

  const refreshWorkflow = useCallback(
    async (workflowId: string, signal?: AbortSignal) => {
      const nextSnapshot = await scienceCore.getWorkflow(workflowId, { signal });
      if (signal?.aborted || selectedWorkflowIdRef.current !== workflowId) return;
      if (
        nextSnapshot.workflow.id !== workflowId ||
        nextSnapshot.workflow.projectId !== projectIdRef.current
      ) {
        throw new Error(
          "Science core returned a workflow outside the selected project",
        );
      }
      applySnapshot(nextSnapshot, workflowId);

      let after = eventAfterRef.current;
      for (let pageNumber = 0; pageNumber < 3; pageNumber += 1) {
        const page = await scienceCore.listWorkflowEvents(workflowId, {
          after,
          limit: 50,
          signal,
        });
        if (signal?.aborted || selectedWorkflowIdRef.current !== workflowId) return;
        setEvents((current) => mergeWorkflowEvents(current, page.events));
        after = page.nextAfter;
        eventAfterRef.current = after;
        if (!page.hasMore) break;
      }
    },
    [applySnapshot],
  );

  useEffect(() => {
    clearSelectionState();
    setError(null);
    if (!selectedWorkflowId) return;

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
  }, [clearSelectionState, refreshWorkflow, selectedWorkflowId, setError]);

  const refreshSelected = useCallback(async () => {
    if (!selectedWorkflowId) return;
    const controller = new AbortController();
    try {
      await refreshWorkflow(selectedWorkflowId, controller.signal);
    } catch (reason) {
      if (!controller.signal.aborted) setError(errorMessage(reason));
    }
  }, [refreshWorkflow, selectedWorkflowId, setError]);

  return {
    snapshot,
    events,
    loadingSnapshot,
    connection,
    applySnapshot,
    currentSnapshot,
    clearSelectionState,
    refreshSelected,
  };
}
