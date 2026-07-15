import { useCallback, useEffect, useRef, useState } from "react";
import type {
  ResearchGenerationMode,
  ResearchWorkflowAllowedAction,
  ResearchWorkflowSnapshot,
  ResearchWorkflowType,
  WorkflowEvent,
} from "@spark/research-domain";
import { scienceCore } from "@/lib/scienceCore";
import {
  useWorkflowEvents,
  type WorkflowConnectionState,
} from "./useWorkflowEvents";
import {
  errorMessage,
  sameCreateIntent,
  snapshotIsOlder,
  type WorkflowCreateIntent,
} from "./workflowModel";

export type { WorkflowConnectionState } from "./useWorkflowEvents";

export interface ResearchWorkflowCreateOptions {
  workflowType: ResearchWorkflowType;
  datasetSourceId: string | null;
  generationMode: ResearchGenerationMode;
  remoteDataApproved: boolean;
}

const DEFAULT_CREATE_OPTIONS: ResearchWorkflowCreateOptions = {
  workflowType: "literature-synthesis",
  datasetSourceId: null,
  generationMode: "local-deterministic",
  remoteDataApproved: false,
};

function idempotencyKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function mergeWorkflowLists(
  current: ResearchWorkflowSnapshot[],
  incoming: ResearchWorkflowSnapshot[],
): ResearchWorkflowSnapshot[] {
  const merged = [...current];
  for (const candidate of incoming) {
    const index = merged.findIndex(
      (item) => item.workflow.id === candidate.workflow.id,
    );
    if (index === -1) {
      merged.push(candidate);
    } else if (!snapshotIsOlder(candidate, merged[index])) {
      merged[index] = candidate;
    }
  }
  return merged;
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
  create: (
    goal: string,
    options?: ResearchWorkflowCreateOptions,
  ) => Promise<void>;
  approvePlan: () => Promise<void>;
  decideAnalysis: (decision: "approved" | "rejected") => Promise<void>;
  acceptReviewWarnings: () => Promise<void>;
  cancel: () => Promise<void>;
  retry: () => Promise<void>;
  resume: () => Promise<void>;
}

export function useResearchWorkflow(projectId: string | null): ResearchWorkflowController {
  const [workflows, setWorkflows] = useState<ResearchWorkflowSnapshot[]>([]);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
  const [loadingList, setLoadingList] = useState(false);
  const [mutating, setMutating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listRefresh, setListRefresh] = useState(0);
  const mutationInFlightRef = useRef(false);
  const mutationControllerRef = useRef<AbortController | null>(null);
  const projectIdRef = useRef(projectId);
  const selectedWorkflowIdRef = useRef(selectedWorkflowId);
  const selectionEpochRef = useRef(0);
  const createIntentRef = useRef<WorkflowCreateIntent | null>(null);
  projectIdRef.current = projectId;
  selectedWorkflowIdRef.current = selectedWorkflowId;

  const abortMutation = useCallback(() => {
    mutationControllerRef.current?.abort();
    mutationControllerRef.current = null;
    mutationInFlightRef.current = false;
    setMutating(false);
  }, []);

  const updateWorkflowList = useCallback((next: ResearchWorkflowSnapshot) => {
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
  }, []);

  useEffect(() => {
    createIntentRef.current = null;
    abortMutation();
    return () => mutationControllerRef.current?.abort();
  }, [abortMutation, projectId]);

  useEffect(() => {
    const controller = new AbortController();
    selectionEpochRef.current += 1;
    const listSelectionEpoch = selectionEpochRef.current;
    setWorkflows([]);
    selectedWorkflowIdRef.current = null;
    setSelectedWorkflowId(null);
    setError(null);
    setLoadingList(false);
    if (!projectId) return () => controller.abort();

    setLoadingList(true);
    void scienceCore
      .listWorkflows(projectId, { limit: 12, signal: controller.signal })
      .then((next) => {
        if (controller.signal.aborted) return;
        if (next.some((item) => item.workflow.projectId !== projectId)) {
          throw new Error(
            "Science core returned a workflow outside the selected project",
          );
        }
        const selectionChanged =
          selectionEpochRef.current !== listSelectionEpoch;
        setWorkflows((current) =>
          selectionChanged ? mergeWorkflowLists(current, next) : next,
        );
        if (!selectionChanged && selectedWorkflowIdRef.current === null) {
          const nextSelectedWorkflowId = next[0]?.workflow.id ?? null;
          selectedWorkflowIdRef.current = nextSelectedWorkflowId;
          setSelectedWorkflowId(nextSelectedWorkflowId);
        }
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingList(false);
      });

    return () => controller.abort();
  }, [listRefresh, projectId]);

  const {
    snapshot,
    events,
    loadingSnapshot,
    connection,
    applySnapshot,
    currentSnapshot,
    clearSelectionState,
    refreshSelected,
  } = useWorkflowEvents({
    projectId,
    selectedWorkflowId,
    setError,
    onSnapshotApplied: updateWorkflowList,
  });

  const requireAction = useCallback(
    (action: ResearchWorkflowAllowedAction) => {
      const current = currentSnapshot();
      if (current === null) {
        throw new Error(`Workflow action is not currently allowed: ${action}`);
      }
      const allowedActions: readonly ResearchWorkflowAllowedAction[] =
        current.allowedActions;
      if (!allowedActions.includes(action)) {
        throw new Error(`Workflow action is not currently allowed: ${action}`);
      }
      return current;
    },
    [currentSnapshot],
  );

  const runMutation = useCallback(
    async (
      operation: (signal: AbortSignal) => Promise<ResearchWorkflowSnapshot>,
      onApplied?: (snapshot: ResearchWorkflowSnapshot) => void,
    ) => {
      if (mutationInFlightRef.current) return;
      const operationProjectId = projectIdRef.current;
      if (!operationProjectId) return;
      const operationSelectedWorkflowId = selectedWorkflowIdRef.current;
      const operationSnapshotWorkflowId =
        currentSnapshot()?.workflow.id ?? null;
      if (operationSelectedWorkflowId !== operationSnapshotWorkflowId) {
        setError("The selected workflow is still loading");
        return;
      }
      const controller = new AbortController();
      mutationControllerRef.current = controller;
      mutationInFlightRef.current = true;
      setMutating(true);
      setError(null);
      try {
        const next = await operation(controller.signal);
        const currentSnapshotWorkflowId =
          currentSnapshot()?.workflow.id ?? null;
        if (
          controller.signal.aborted ||
          projectIdRef.current !== operationProjectId ||
          selectedWorkflowIdRef.current !== operationSelectedWorkflowId ||
          currentSnapshotWorkflowId !== operationSnapshotWorkflowId ||
          next.workflow.projectId !== operationProjectId ||
          (operationSnapshotWorkflowId !== null &&
            next.workflow.id !== operationSnapshotWorkflowId)
        ) {
          return;
        }
        const expectedWorkflowId = operationSnapshotWorkflowId ?? next.workflow.id;
        if (
          applySnapshot(
            next,
            expectedWorkflowId,
            operationSnapshotWorkflowId === null,
          )
        ) {
          onApplied?.(next);
        }
      } catch (reason) {
        if (
          !controller.signal.aborted &&
          projectIdRef.current === operationProjectId &&
          selectedWorkflowIdRef.current === operationSelectedWorkflowId &&
          (currentSnapshot()?.workflow.id ?? null) ===
            operationSnapshotWorkflowId
        ) {
          const mutationError = errorMessage(reason);
          setError(mutationError);
          if (
            typeof reason === "object" &&
            reason !== null &&
            "status" in reason &&
            reason.status === 409
          ) {
            await refreshSelected();
            if (
              projectIdRef.current === operationProjectId &&
              selectedWorkflowIdRef.current === operationSelectedWorkflowId &&
              (currentSnapshot()?.workflow.id ?? null) ===
                operationSnapshotWorkflowId
            ) {
              setError(mutationError);
            }
          }
        }
      } finally {
        if (mutationControllerRef.current === controller) {
          mutationControllerRef.current = null;
          mutationInFlightRef.current = false;
          setMutating(false);
        }
      }
    },
    [applySnapshot, currentSnapshot, refreshSelected],
  );

  const create = useCallback(
    async (
      goal: string,
      options: ResearchWorkflowCreateOptions = DEFAULT_CREATE_OPTIONS,
    ) => {
      if (!projectId) return;
      const datasetSourceId =
        options.workflowType === "dataset-analysis"
          ? options.datasetSourceId
          : null;
      if (options.workflowType === "dataset-analysis" && !datasetSourceId) {
        setError("Choose a ready dataset before creating the workflow");
        return;
      }
      const generationMode =
        options.workflowType === "dataset-analysis"
          ? "local-deterministic"
          : options.generationMode;
      const remoteDataApproved =
        options.workflowType === "dataset-analysis"
          ? false
          : options.remoteDataApproved;
      const candidate = {
        projectId,
        goal: goal.trim(),
        workflowType: options.workflowType,
        datasetSourceId,
        generationMode,
        remoteDataApproved,
      };
      const existingIntent = createIntentRef.current;
      const intent = sameCreateIntent(existingIntent, candidate)
        ? existingIntent
        : { ...candidate, idempotencyKey: idempotencyKey() };
      createIntentRef.current = intent;
      selectionEpochRef.current += 1;
      await runMutation(
        (signal) =>
          scienceCore.createWorkflow(
            projectId,
            candidate.workflowType === "dataset-analysis"
              ? {
                  goal: candidate.goal,
                  workflowType: "dataset-analysis",
                  datasetSourceId: candidate.datasetSourceId as string,
                  generationMode: "local-deterministic",
                  remoteDataApproved: false,
                }
              : {
                  goal: candidate.goal,
                  workflowType: "literature-synthesis",
                  generationMode,
                  remoteDataApproved,
                },
            { idempotencyKey: intent.idempotencyKey, signal },
          ),
        (next) => {
          createIntentRef.current = null;
          selectedWorkflowIdRef.current = next.workflow.id;
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

  const decideAnalysis = useCallback(
    async (decision: "approved" | "rejected") => {
      await runMutation((signal) => {
        const current = requireAction(
          decision === "approved" ? "approve-analysis" : "reject-analysis",
        );
        if (
          current.workflow.workflowType !== "dataset-analysis" ||
          current.analysisIntent?.status !== "waiting-approval"
        ) {
          throw new Error("The analysis intent is not waiting for a decision");
        }
        const approval = current.pendingApprovals.find(
          (item) =>
            item.kind === "analysis-execution" &&
            item.analysisIntentId === current.analysisIntent?.id,
        );
        if (!approval) {
          throw new Error("The execution approval request is not available");
        }
        return scienceCore.decideWorkflowAnalysisIntent(
          current.workflow.id,
          {
            intentId: current.analysisIntent.id,
            approvalId: approval.id,
            decision,
            payloadSha256: current.analysisIntent.payloadSha256,
            expectedWorkflowRevision: current.workflow.revision,
          },
          { idempotencyKey: idempotencyKey(), signal },
        );
      });
    },
    [requireAction, runMutation],
  );

  const acceptReviewWarnings = useCallback(async () => {
    await runMutation((signal) => {
      const current = requireAction("accept-review-warnings");
      const review = current.latestReview;
      if (
        current.workflow.workflowType !== "dataset-analysis" ||
        review?.reviewType !== "deterministic-analysis-v1" ||
        review.verdict !== "passed-with-warnings"
      ) {
        throw new Error("The workflow has no warning-bearing analysis review");
      }
      return scienceCore.acceptWorkflowReviewWarnings(
        current.workflow.id,
        {
          reviewId: review.id,
          reviewInputSha256: review.inputSha256,
          expectedWorkflowRevision: current.workflow.revision,
          decision: "accepted",
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
    await refreshSelected();
  }, [projectId, refreshSelected, selectedWorkflowId]);

  const selectWorkflow = useCallback(
    (workflowId: string) => {
      if (selectedWorkflowIdRef.current === workflowId) return;
      abortMutation();
      selectionEpochRef.current += 1;
      selectedWorkflowIdRef.current = workflowId;
      setSelectedWorkflowId(workflowId);
    },
    [abortMutation],
  );

  const startNew = useCallback(() => {
    abortMutation();
    selectionEpochRef.current += 1;
    selectedWorkflowIdRef.current = null;
    setSelectedWorkflowId(null);
    clearSelectionState();
  }, [abortMutation, clearSelectionState]);

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
    decideAnalysis,
    acceptReviewWarnings,
    cancel,
    retry,
    resume,
  };
}
