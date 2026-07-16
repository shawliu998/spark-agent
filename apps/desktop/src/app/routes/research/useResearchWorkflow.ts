import { useCallback, useEffect, useRef, useState } from "react";
import type {
  InteractionRequest,
  InteractionResponseValue,
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
  type WorkflowCreateCandidate,
  type WorkflowCreateIntent,
} from "./workflowModel";

export type { WorkflowConnectionState } from "./useWorkflowEvents";

export interface AutonomousResearchWorkflowCreateOptions {
  mode: "autonomous";
  sourceIds: string[];
}

export interface AdvancedResearchWorkflowCreateOptions {
  mode?: "advanced";
  workflowType: ResearchWorkflowType;
  datasetSourceId: string | null;
  generationMode: ResearchGenerationMode;
  remoteDataApproved: boolean;
}

export type ResearchWorkflowCreateOptions =
  | AutonomousResearchWorkflowCreateOptions
  | AdvancedResearchWorkflowCreateOptions;

const DEFAULT_CREATE_OPTIONS: AdvancedResearchWorkflowCreateOptions = {
  mode: "advanced",
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
  ...lists: ResearchWorkflowSnapshot[][]
): ResearchWorkflowSnapshot[] {
  const merged: ResearchWorkflowSnapshot[] = [];
  for (const candidates of lists) {
    for (const candidate of candidates) {
      const index = merged.findIndex(
        (item) => item.workflow.id === candidate.workflow.id,
      );
      if (index === -1) {
        merged.push(candidate);
      } else if (!snapshotIsOlder(candidate, merged[index])) {
        merged[index] = candidate;
      }
    }
  }
  return merged.sort((left, right) => {
    const updatedDifference =
      workflowTimestamp(right.workflow.updatedAt) -
      workflowTimestamp(left.workflow.updatedAt);
    if (updatedDifference !== 0) return updatedDifference;
    const createdDifference =
      workflowTimestamp(right.workflow.createdAt) -
      workflowTimestamp(left.workflow.createdAt);
    if (createdDifference !== 0) return createdDifference;
    return left.workflow.id.localeCompare(right.workflow.id);
  });
}

function workflowTimestamp(value: string): number {
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : 0;
}

export interface ResearchWorkflowController {
  workflows: ResearchWorkflowSnapshot[];
  selectedWorkflowId: string | null;
  snapshot: ResearchWorkflowSnapshot | null;
  events: WorkflowEvent[];
  interactions: InteractionRequest[];
  loadingList: boolean;
  loadingSnapshot: boolean;
  loadingInteractions: boolean;
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
  respondToInteraction: (
    interactionId: string,
    response: InteractionResponseValue,
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
  const [interactions, setInteractions] = useState<InteractionRequest[]>([]);
  const [loadingInteractions, setLoadingInteractions] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listRefresh, setListRefresh] = useState(0);
  const mutationInFlightRef = useRef(false);
  const mutationControllerRef = useRef<AbortController | null>(null);
  const projectIdRef = useRef(projectId);
  const selectedWorkflowIdRef = useRef(selectedWorkflowId);
  const selectionEpochRef = useRef(0);
  const createIntentRef = useRef<WorkflowCreateIntent | null>(null);
  const responseIntentRef = useRef<{
    interactionId: string;
    workflowRevision: number;
    responseKey: string;
    idempotencyKey: string;
  } | null>(null);
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
      return mergeWorkflowLists(current, [next]);
    });
  }, []);

  useEffect(() => {
    createIntentRef.current = null;
    responseIntentRef.current = null;
    setInteractions([]);
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
    void Promise.all([
      scienceCore.listWorkflows(projectId, {
        limit: 12,
        signal: controller.signal,
      }),
      scienceCore.listAgentRuns(projectId, {
        limit: 20,
        signal: controller.signal,
      }),
      scienceCore.listAgentRuns(projectId, {
        activeOnly: true,
        limit: 100,
        signal: controller.signal,
      }),
    ])
      .then(([resolvedWorkflows, recentAgentRuns, activeAgentRuns]) => {
        if (controller.signal.aborted) return;
        const next = mergeWorkflowLists(
          resolvedWorkflows,
          recentAgentRuns,
          activeAgentRuns,
        );
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

  const selectedIsAgentRun = workflows.some(
    (item) =>
      item.workflow.id === selectedWorkflowId &&
      (item.workflow.mode === "autonomous" || "intentDecision" in item),
  );

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
    selectedIsAgentRun,
    setError,
    onSnapshotApplied: updateWorkflowList,
  });

  useEffect(() => {
    const controller = new AbortController();
    responseIntentRef.current = null;
    if (!selectedWorkflowId || !selectedIsAgentRun) {
      setInteractions([]);
      setLoadingInteractions(false);
      return () => controller.abort();
    }

    const current = currentSnapshot();
    if (current && "interactions" in current) {
      setInteractions(current.interactions);
    }
    const expectedProjectId = projectId;
    setLoadingInteractions(true);
    void scienceCore
      .listWorkflowInteractions(selectedWorkflowId, {
        signal: controller.signal,
      })
      .then((next) => {
        if (
          controller.signal.aborted ||
          projectIdRef.current !== expectedProjectId ||
          selectedWorkflowIdRef.current !== selectedWorkflowId
        ) {
          return;
        }
        if (next.some((item) => item.workflowId !== selectedWorkflowId)) {
          throw new Error(
            "Science core returned an interaction outside the selected workflow",
          );
        }
        setInteractions(next);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(errorMessage(reason));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingInteractions(false);
      });

    return () => controller.abort();
  }, [
    projectId,
    currentSnapshot,
    selectedIsAgentRun,
    selectedWorkflowId,
    snapshot?.workflow.revision,
  ]);

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
      const normalizedGoal = goal.trim();
      if (options.mode === "autonomous") {
        const sourceIds = [...new Set(options.sourceIds)].sort();
        if (sourceIds.length === 0) {
          setError(
            "Choose at least one ready PDF or CSV source before starting Auto research.",
          );
          return;
        }
        const candidate = {
          projectId,
          goal: normalizedGoal,
          mode: "autonomous" as const,
          sourceIds,
        } satisfies WorkflowCreateCandidate;
        const existingIntent = createIntentRef.current;
        const intent = sameCreateIntent(existingIntent, candidate)
          ? existingIntent
          : { ...candidate, idempotencyKey: idempotencyKey() };
        createIntentRef.current = intent;
        selectionEpochRef.current += 1;
        await runMutation(
          (signal) =>
            scienceCore.createAgentRun(
              projectId,
              { goal: normalizedGoal, sourceIds, mode: "autonomous" },
              { idempotencyKey: intent.idempotencyKey, signal },
            ),
          (next) => {
            createIntentRef.current = null;
            if ("interactions" in next) setInteractions(next.interactions);
            selectedWorkflowIdRef.current = next.workflow.id;
            setSelectedWorkflowId(next.workflow.id);
          },
        );
        return;
      }

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
        goal: normalizedGoal,
        mode: "advanced" as const,
        workflowType: options.workflowType,
        datasetSourceId,
        generationMode,
        remoteDataApproved,
      } satisfies WorkflowCreateCandidate;
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

  const respondToInteraction = useCallback(
    async (interactionId: string, response: InteractionResponseValue) => {
      await runMutation(
        (signal) => {
          const current = currentSnapshot();
          if (!current) throw new Error("The workflow is still loading");
          const interaction = interactions.find(
            (item) =>
              item.id === interactionId &&
              (item.status === "pending" || item.status === "answered"),
          );
          if (!interaction) {
            throw new Error("The clarification request is no longer answerable");
          }
          if (interaction.workflowId !== current.workflow.id) {
            throw new Error("The clarification request belongs to another workflow");
          }
          if (
            interaction.status === "answered" &&
            current.workflow.status !== "planning" &&
            current.workflow.status !== "waiting-plan-approval"
          ) {
            throw new Error(
              "The clarification answer can no longer be changed after execution begins",
            );
          }
          const workflowRevision = current.workflow.revision;
          const responseKey = JSON.stringify(response);
          const existing = responseIntentRef.current;
          const intent =
            existing?.interactionId === interactionId &&
            existing.workflowRevision === workflowRevision &&
            existing.responseKey === responseKey
              ? existing
              : {
                  interactionId,
                  workflowRevision,
                  responseKey,
                  idempotencyKey: idempotencyKey(),
                };
          responseIntentRef.current = intent;
          return scienceCore.respondToInteraction(
            interactionId,
            { response, expectedWorkflowRevision: workflowRevision },
            { idempotencyKey: intent.idempotencyKey, signal },
          );
        },
        (next) => {
          responseIntentRef.current = null;
          if ("interactions" in next) {
            setInteractions(next.interactions);
          } else {
            setInteractions((current) =>
              current.filter((item) => item.id !== interactionId),
            );
          }
        },
      );
    },
    [currentSnapshot, interactions, runMutation],
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
      setInteractions([]);
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
    setInteractions([]);
    clearSelectionState();
  }, [abortMutation, clearSelectionState]);

  return {
    workflows,
    selectedWorkflowId,
    snapshot,
    events,
    interactions,
    loadingList,
    loadingSnapshot,
    loadingInteractions,
    mutating,
    connection,
    error,
    selectWorkflow,
    startNew,
    refresh,
    create,
    respondToInteraction,
    approvePlan,
    decideAnalysis,
    acceptReviewWarnings,
    cancel,
    retry,
    resume,
  };
}
