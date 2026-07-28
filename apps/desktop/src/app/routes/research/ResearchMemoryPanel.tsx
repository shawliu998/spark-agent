import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Check, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type {
  ResearchMemory,
  ResearchMemoryAction,
  ResearchMemoryContext,
  ResearchMemoryWorkspace,
  SkillActivation,
  SkillActivationPreview,
  SkillCandidate,
} from "@spark/research-domain";
import { scienceCore } from "@/lib/scienceCore";
import { cn } from "@/lib/cn";

export const RESEARCH_MEMORY_PANEL_DENSITY = {
  compact: "compact",
  full: "full",
} as const;

function memoryRequestKey(action: ResearchMemoryAction): string {
  const suffix =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `memory-${action}-${suffix}`;
}

export interface ResearchMemoryPanelController {
  projectId: string | null;
  workflowId: string | null;
  workspace: ResearchMemoryWorkspace | null;
  loading: boolean;
  error: string | null;
  actionError: { memoryId: string; message: string } | null;
  working: { memoryId: string; action: ResearchMemoryAction } | null;
  skillCandidates?: SkillCandidate[];
  skillActivations?: SkillActivation[];
  skillWorkingMemoryId?: string | null;
  skillError?: string | null;
  skillActivationPreviews?: Record<string, SkillActivationPreview>;
  skillActivationPreviewErrors?: Record<string, string>;
  skillActivationsError?: string | null;
  skillActivationWorking?: { candidateId: string; action: "approve" | "rollback" } | null;
  skillActivationError?: { candidateId: string; message: string } | null;
  refresh: () => Promise<void>;
  resolve: (
    memory: ResearchMemory,
    decision: "accept" | "reject",
  ) => Promise<boolean>;
  invalidate: (memory: ResearchMemory) => Promise<boolean>;
  saveAsProcedure?: (memory: ResearchMemory) => Promise<boolean>;
  approveSkill?: (candidate: SkillCandidate, preview: SkillActivationPreview) => Promise<boolean>;
  rollbackSkill?: (activation: SkillActivation) => Promise<boolean>;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function useResearchMemoryWorkspace(
  projectId: string | null,
  workflowId: string | null,
): ResearchMemoryPanelController {
  const [workspace, setWorkspace] = useState<ResearchMemoryWorkspace | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<{
    memoryId: string;
    message: string;
  } | null>(null);
  const [working, setWorking] = useState<{
    memoryId: string;
    action: ResearchMemoryAction;
  } | null>(null);
  const [skillCandidates, setSkillCandidates] = useState<SkillCandidate[]>([]);
  const [skillActivations, setSkillActivations] = useState<SkillActivation[]>([]);
  const [skillWorkingMemoryId, setSkillWorkingMemoryId] = useState<string | null>(
    null,
  );
  const [skillError, setSkillError] = useState<{
    identity: string;
    message: string;
  } | null>(null);
  const [skillActivationPreviews, setSkillActivationPreviews] = useState<
    Record<string, SkillActivationPreview>
  >({});
  const [skillActivationPreviewErrors, setSkillActivationPreviewErrors] = useState<
    Record<string, string>
  >({});
  const [skillActivationsError, setSkillActivationsError] = useState<string | null>(
    null,
  );
  const [skillActivationWorking, setSkillActivationWorking] = useState<{
    candidateId: string;
    action: "approve" | "rollback";
  } | null>(null);
  const [skillActivationError, setSkillActivationError] = useState<{
    candidateId: string;
    message: string;
  } | null>(null);
  const identityRef = useRef("");
  const loadControllerRef = useRef<AbortController | null>(null);
  const memoryMutationControllerRef = useRef<AbortController | null>(null);
  const procedureControllerRef = useRef<AbortController | null>(null);
  const activationListControllerRef = useRef<AbortController | null>(null);
  const activationPreviewControllerRef = useRef<AbortController | null>(null);
  const activationControllerRef = useRef<AbortController | null>(null);
  const memoryMutationEpochRef = useRef(0);
  const procedureEpochRef = useRef(0);
  const idempotencyKeysRef = useRef(new Map<string, string>());
  const identity = projectId && workflowId ? `${projectId}:${workflowId}` : "";
  identityRef.current = identity;

  const loadSkillActivationPreviews = useCallback(
    async (
      requestProjectId: string,
      requestWorkflowId: string,
      candidates: SkillCandidate[],
    ) => {
      activationPreviewControllerRef.current?.abort();
      const requestIdentity = `${requestProjectId}:${requestWorkflowId}`;
      const controller = new AbortController();
      activationPreviewControllerRef.current = controller;
      const previewCandidates = candidates.filter(
        (candidate) => candidate.status === "awaiting-approval",
      );
      try {
        const results = await Promise.allSettled(
          previewCandidates.map(async (candidate) => [
            candidate.id,
            await scienceCore.getSkillActivationPreview(
              requestProjectId,
              requestWorkflowId,
              candidate.id,
              { signal: controller.signal },
            ),
            ] as const),
        );
        if (
          !controller.signal.aborted &&
          identityRef.current === requestIdentity
        ) {
          const previews: Record<string, SkillActivationPreview> = {};
          const errors: Record<string, string> = {};
          results.forEach((result, index) => {
            if (result.status === "fulfilled") {
              previews[result.value[0]] = result.value[1];
            } else {
              const candidateId = previewCandidates[index]?.id;
              if (candidateId) errors[candidateId] = errorMessage(result.reason);
            }
          });
          setSkillActivationPreviews(previews);
          setSkillActivationPreviewErrors(errors);
        }
      } finally {
        if (activationPreviewControllerRef.current === controller) {
          activationPreviewControllerRef.current = null;
        }
      }
    },
    [],
  );

  const loadSkillActivations = useCallback(
    async (requestProjectId: string, requestWorkflowId: string) => {
      activationListControllerRef.current?.abort();
      const requestIdentity = `${requestProjectId}:${requestWorkflowId}`;
      const controller = new AbortController();
      activationListControllerRef.current = controller;
      try {
        const activations = await scienceCore.listSkillActivations(requestProjectId, {
          workflowId: requestWorkflowId,
          signal: controller.signal,
        });
        if (!controller.signal.aborted && identityRef.current === requestIdentity) {
          setSkillActivations(activations);
          setSkillActivationsError(null);
        }
      } catch (requestError) {
        if (!controller.signal.aborted && identityRef.current === requestIdentity) {
          setSkillActivations([]);
          setSkillActivationsError(errorMessage(requestError));
        }
      } finally {
        if (activationListControllerRef.current === controller) {
          activationListControllerRef.current = null;
        }
      }
    },
    [],
  );

  const refresh = useCallback(async () => {
    loadControllerRef.current?.abort();
    activationPreviewControllerRef.current?.abort();
    activationListControllerRef.current?.abort();
    if (!projectId || !workflowId) {
      setWorkspace(null);
      setSkillCandidates([]);
      setSkillActivations([]);
      setSkillActivationPreviews({});
      setSkillActivationPreviewErrors({});
      setSkillActivationsError(null);
      setSkillActivationError(null);
      setLoading(false);
      setError(null);
      setSkillError(null);
      return;
    }
    const requestIdentity = `${projectId}:${workflowId}`;
    const controller = new AbortController();
    loadControllerRef.current = controller;
    setLoading(true);
    setError(null);
    setSkillError(null);
    setSkillCandidates([]);
    setSkillActivations([]);
    setSkillActivationPreviews({});
    setSkillActivationPreviewErrors({});
    setSkillActivationsError(null);
    setSkillActivationError(null);
    const memoryRequest = scienceCore.getResearchMemoryWorkspace(
      projectId,
      workflowId,
      { signal: controller.signal },
    );
    const skillRequest = scienceCore.listSkillCandidates(projectId, workflowId, {
      signal: controller.signal,
    });
    void loadSkillActivations(projectId, workflowId);
    try {
      const next = await memoryRequest;
      if (
        !controller.signal.aborted &&
        identityRef.current === requestIdentity &&
        next.projectId === projectId &&
        next.workflowId === workflowId
      ) {
        setWorkspace(next);
      }
    } catch (requestError) {
      if (
        !controller.signal.aborted &&
        identityRef.current === requestIdentity
      ) {
        setWorkspace(null);
        setError(errorMessage(requestError));
      }
    }
    try {
      const nextSkillCandidates = await skillRequest;
      if (
        !controller.signal.aborted &&
        identityRef.current === requestIdentity
      ) {
        setSkillCandidates(nextSkillCandidates);
        void loadSkillActivationPreviews(
          projectId,
          workflowId,
          nextSkillCandidates,
        );
      }
    } catch (requestError) {
      if (
        !controller.signal.aborted &&
        identityRef.current === requestIdentity
      ) {
        setSkillCandidates([]);
        setSkillError({
          identity: requestIdentity,
          message: errorMessage(requestError),
        });
      }
    } finally {
      if (
        loadControllerRef.current === controller &&
        identityRef.current === requestIdentity
      ) {
        loadControllerRef.current = null;
        setLoading(false);
      }
    }
  }, [loadSkillActivationPreviews, loadSkillActivations, projectId, workflowId]);

  useEffect(() => {
    memoryMutationEpochRef.current += 1;
    procedureEpochRef.current += 1;
    memoryMutationControllerRef.current?.abort();
    procedureControllerRef.current?.abort();
    activationListControllerRef.current?.abort();
    activationPreviewControllerRef.current?.abort();
    activationControllerRef.current?.abort();
    setWorking(null);
    setSkillWorkingMemoryId(null);
    setActionError(null);
    setSkillError(null);
    setSkillCandidates([]);
    setSkillActivations([]);
    setSkillActivationPreviews({});
    setSkillActivationPreviewErrors({});
    setSkillActivationsError(null);
    setSkillActivationWorking(null);
    setSkillActivationError(null);
    void refresh();
    return () => {
      loadControllerRef.current?.abort();
      memoryMutationEpochRef.current += 1;
      procedureEpochRef.current += 1;
      memoryMutationControllerRef.current?.abort();
      procedureControllerRef.current?.abort();
      activationListControllerRef.current?.abort();
      activationPreviewControllerRef.current?.abort();
      activationControllerRef.current?.abort();
    };
  }, [refresh]);

  const mutate = useCallback(
    async (
      action: ResearchMemoryAction,
      memory: ResearchMemory,
    ) => {
      if (!projectId || !workflowId) return false;
      const requestIdentity = `${projectId}:${workflowId}`;
      const signature = [
        requestIdentity,
        memory.id,
        memory.memorySha256,
        action,
      ].join(":");
      let idempotencyKey = idempotencyKeysRef.current.get(signature);
      if (!idempotencyKey) {
        idempotencyKey = memoryRequestKey(action);
        idempotencyKeysRef.current.set(signature, idempotencyKey);
      }
      memoryMutationControllerRef.current?.abort();
      const controller = new AbortController();
      const requestEpoch = memoryMutationEpochRef.current + 1;
      memoryMutationEpochRef.current = requestEpoch;
      memoryMutationControllerRef.current = controller;
      setWorking({ memoryId: memory.id, action });
      setActionError(null);
      try {
        if (action === "accept" || action === "reject") {
          await scienceCore.resolveResearchMemoryCandidate(
            projectId,
            workflowId,
            memory.id,
            {
              decision: action,
              expectedContentHash: memory.memorySha256,
              expectedStatus: "candidate",
              expectedRevision: memory.revision,
              expectedSubjectHeadId: memory.subjectHeadId,
              expectedSubjectHeadRevision: memory.subjectHeadRevision,
            },
            { idempotencyKey, signal: controller.signal },
          );
        } else if (action === "invalidate") {
          await scienceCore.invalidateResearchMemory(
            projectId,
            workflowId,
            memory.id,
            {
              expectedContentHash: memory.memorySha256,
              expectedStatus: "committed",
              expectedRevision: memory.revision,
              expectedSubjectHeadId: memory.subjectHeadId,
              expectedSubjectHeadRevision: memory.subjectHeadRevision,
            },
            { idempotencyKey, signal: controller.signal },
          );
        } else return false;
        if (
          !controller.signal.aborted &&
          identityRef.current === requestIdentity
        ) {
          idempotencyKeysRef.current.delete(signature);
          await refresh();
          return true;
        }
        return false;
      } catch (mutationError) {
        if (
          !controller.signal.aborted &&
          identityRef.current === requestIdentity
        ) {
          setActionError({
            memoryId: memory.id,
            message: errorMessage(mutationError),
          });
        }
        return false;
      } finally {
        if (
          memoryMutationControllerRef.current === controller &&
          memoryMutationEpochRef.current === requestEpoch &&
          identityRef.current === requestIdentity
        ) {
          memoryMutationControllerRef.current = null;
          setWorking(null);
        }
      }
    },
    [projectId, refresh, workflowId],
  );

  const saveAsProcedure = useCallback(
    async (memory: ResearchMemory) => {
      if (!projectId || !workflowId) return false;
      const requestIdentity = `${projectId}:${workflowId}`;
      procedureControllerRef.current?.abort();
      const controller = new AbortController();
      const requestEpoch = procedureEpochRef.current + 1;
      procedureEpochRef.current = requestEpoch;
      procedureControllerRef.current = controller;
      setSkillWorkingMemoryId(memory.id);
      setSkillError(null);
      try {
        await scienceCore.createSkillCandidate(
          projectId,
          workflowId,
          {
            memoryId: memory.id,
            expectedMemoryContentHash: memory.memorySha256,
          },
          {
            idempotencyKey: `skill-${memory.id}-${memory.memorySha256}`,
            signal: controller.signal,
          },
        );
        if (
          !controller.signal.aborted &&
          identityRef.current === requestIdentity
        ) {
          await refresh();
          return true;
        }
        return false;
      } catch (requestError) {
        if (
          !controller.signal.aborted &&
          identityRef.current === requestIdentity
        ) {
          setSkillError({
            identity: requestIdentity,
            message: errorMessage(requestError),
          });
        }
        return false;
      } finally {
        if (
          procedureControllerRef.current === controller &&
          procedureEpochRef.current === requestEpoch &&
          identityRef.current === requestIdentity
        ) {
          procedureControllerRef.current = null;
          setSkillWorkingMemoryId(null);
        }
      }
    },
    [projectId, refresh, workflowId],
  );

  const approveSkill = useCallback(
    async (candidate: SkillCandidate, preview: SkillActivationPreview) => {
      if (!projectId || !workflowId) return false;
      const requestIdentity = `${projectId}:${workflowId}`;
      activationControllerRef.current?.abort();
      const controller = new AbortController();
      activationControllerRef.current = controller;
      setSkillActivationWorking({ candidateId: candidate.id, action: "approve" });
      setSkillActivationError(null);
      try {
        await scienceCore.approveSkillActivation(
          projectId,
          workflowId,
          candidate.id,
          {
            expectedStatus: preview.expectedStatus,
            expectedCandidateContentHash: preview.candidateContentHash,
            expectedTemplateSha256: preview.templateSha256,
            expectedEvaluationSha256: preview.evaluationSha256,
            expectedApprovalSha256: preview.approvalSha256,
            expectedPriorPresent: preview.priorPresent,
            expectedPriorSha256: preview.priorSha256,
            expectedTargetDirectoryPresent: preview.targetDirectoryPresent,
          },
          {
            idempotencyKey: `skill-activation-approve-${requestIdentity}-${candidate.id}-${preview.approvalSha256}`,
            signal: controller.signal,
          },
        );
        if (!controller.signal.aborted && identityRef.current === requestIdentity) {
          await refresh();
          return true;
        }
        return false;
      } catch (requestError) {
        if (!controller.signal.aborted && identityRef.current === requestIdentity) {
          setSkillActivationError({
            candidateId: candidate.id,
            message: errorMessage(requestError),
          });
        }
        return false;
      } finally {
        if (
          activationControllerRef.current === controller &&
          identityRef.current === requestIdentity
        ) {
          activationControllerRef.current = null;
          setSkillActivationWorking(null);
        }
      }
    },
    [projectId, refresh, workflowId],
  );

  const rollbackSkill = useCallback(
    async (activation: SkillActivation) => {
      if (!projectId || !workflowId) return false;
      const requestIdentity = `${projectId}:${workflowId}`;
      activationControllerRef.current?.abort();
      const controller = new AbortController();
      activationControllerRef.current = controller;
      setSkillActivationWorking({ candidateId: activation.candidateId, action: "rollback" });
      setSkillActivationError(null);
      try {
        await scienceCore.rollbackSkillActivation(
          projectId,
          activation.id,
          {
            expectedStatus: "active",
            expectedActivationId: activation.id,
            expectedApprovalSha256: activation.approvalSha256,
            expectedInstalledSha256: activation.installedSha256,
            expectedCurrentTargetSha256: activation.installedSha256,
          },
          {
            idempotencyKey: `skill-activation-rollback-${activation.id}-${activation.approvalSha256}-${activation.installedSha256}`,
            signal: controller.signal,
          },
        );
        if (!controller.signal.aborted && identityRef.current === requestIdentity) {
          await refresh();
          return true;
        }
        return false;
      } catch (requestError) {
        if (!controller.signal.aborted && identityRef.current === requestIdentity) {
          setSkillActivationError({
            candidateId: activation.candidateId,
            message: errorMessage(requestError),
          });
        }
        return false;
      } finally {
        if (
          activationControllerRef.current === controller &&
          identityRef.current === requestIdentity
        ) {
          activationControllerRef.current = null;
          setSkillActivationWorking(null);
        }
      }
    },
    [projectId, refresh, workflowId],
  );

  return {
    projectId,
    workflowId,
    workspace,
    loading,
    error,
    actionError,
    working,
    skillCandidates,
    skillActivations,
    skillWorkingMemoryId,
    skillError: skillError?.identity === identity ? skillError.message : null,
    skillActivationPreviews,
    skillActivationPreviewErrors,
    skillActivationsError,
    skillActivationWorking,
    skillActivationError,
    refresh,
    resolve: (memory, decision) => mutate(decision, memory),
    invalidate: (memory) => mutate("invalidate", memory),
    saveAsProcedure,
    approveSkill,
    rollbackSkill,
  };
}

function memorySummary(memory: ResearchMemory): string {
  const content = memory.contentJson;
  for (const key of ["question", "statement", "note", "response"] as const) {
    const value = content[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return JSON.stringify(content);
}

function statusKey(status: ResearchMemory["status"]) {
  switch (status) {
    case "candidate":
      return "research.memory.status.candidate" as const;
    case "committed":
      return "research.memory.status.committed" as const;
    case "rejected":
      return "research.memory.status.rejected" as const;
    case "superseded":
      return "research.memory.status.superseded" as const;
    case "invalidated":
      return "research.memory.status.invalidated" as const;
  }
}

function contextKey(context: ResearchMemoryContext) {
  switch (context.reasonCode) {
    case "selected-in-latest-snapshot":
      return "research.memory.context.selected" as const;
    case "eligible-for-future-snapshot":
      return "research.memory.context.future" as const;
    case "bounded-context-excluded":
      return "research.memory.context.bounded" as const;
    case "candidate-excluded":
      return "research.memory.context.candidate" as const;
    case "rejected-excluded":
      return "research.memory.context.rejected" as const;
    case "superseded-excluded":
      return "research.memory.context.superseded" as const;
    case "invalidated-excluded":
      return "research.memory.context.invalidated" as const;
    case "source-missing":
      return "research.memory.context.sourceMissing" as const;
    case "source-not-ready":
      return "research.memory.context.sourceNotReady" as const;
    case "source-stale":
      return "research.memory.context.sourceStale" as const;
    case "evidence-missing":
      return "research.memory.context.evidenceMissing" as const;
    case "evidence-invalid":
      return "research.memory.context.evidenceInvalid" as const;
  }
}

function skillActivationStatusKey(
  status: "awaiting-approval" | "failed-validation" | "installing" | "active" | "rollback-pending" | "rolled-back" | "blocked",
) {
  return `research.memory.skill.status.${status}` as const;
}

export function ResearchMemoryPanel({
  density,
  controller,
}: {
  density: "compact" | "full";
  controller: ResearchMemoryPanelController;
}) {
  const { t } = useTranslation("pages");
  const orderedItems = useMemo(() => {
    const items = controller.workspace?.items ?? [];
    const rank: Record<ResearchMemory["status"], number> = {
      candidate: 0,
      committed: 1,
      invalidated: 2,
      superseded: 3,
      rejected: 4,
    };
    return [...items].sort(
      (left, right) =>
        rank[left.status] - rank[right.status] ||
        right.updatedAt.localeCompare(left.updatedAt),
    );
  }, [controller.workspace?.items]);
  const visibleItems = density === "full" ? orderedItems : [];
  const totalCount = controller.workspace
    ? Object.values(controller.workspace.counts).reduce(
        (total, count) => total + count,
        0,
      )
    : 0;

  return (
    <section
      aria-label={t("research.memory.heading")}
      className={cn(
        "research-memory-panel min-w-0 bg-surface",
        density === "compact"
          ? "mt-8 border-y border-border"
          : "h-full overflow-y-auto",
      )}
    >
      <div className="flex min-h-11 items-center gap-3 border-b border-border-faint px-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-xs font-semibold text-text">
            {t("research.memory.heading")}
          </h3>
          <p className="truncate text-caption text-muted">
            {controller.workflowId
              ? t("research.memory.subtitle")
              : t("research.memory.noWorkflow")}
          </p>
        </div>
        {controller.workspace && (
          <span className="shrink-0 text-caption text-muted">
            {t("research.memory.pendingCount", {
              count: controller.workspace.counts.candidate,
            })}
          </span>
        )}
        <button
          type="button"
          onClick={() => void controller.refresh()}
          disabled={controller.loading || !controller.workflowId}
          className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
        >
          {t("research.memory.refresh")}
        </button>
      </div>

      {controller.loading && !controller.workspace && (
        <div
          className="space-y-0 divide-y divide-border-faint"
          aria-busy="true"
          aria-label={t("research.memory.loading")}
        >
          {[0, 1, 2].map((item) => (
            <div
              key={item}
              aria-hidden="true"
              className="animate-pulse space-y-2 px-3 py-3 motion-reduce:animate-none"
            >
              <div className="h-3 w-24 rounded-input bg-surface-2" />
              <div className="h-3 w-full rounded-input bg-surface-2" />
            </div>
          ))}
        </div>
      )}

      {controller.error && (
        <div role="alert" className="px-3 py-4 text-xs">
          <p className="font-medium text-error">{t("research.memory.loadFailed")}</p>
          <p className="mt-1 break-words text-muted">{controller.error}</p>
        </div>
      )}

      {density === "compact" &&
        controller.workspace &&
        !controller.loading &&
        !controller.error && (
          <p className="px-3 py-3 text-xs leading-5 text-muted">
            {controller.workspace.counts.candidate > 0
              ? t("research.memory.attentionSummary", {
                  count: controller.workspace.counts.candidate,
                })
              : t("research.memory.continuitySummary", {
                  count: controller.workspace.counts.committed,
                })}
          </p>
        )}

      {!controller.loading &&
        !controller.error &&
        controller.workflowId &&
        density === "full" &&
        visibleItems.length === 0 && (
          <div className="px-3 py-4 text-xs">
            <p className="font-medium text-text">{t("research.memory.empty")}</p>
            <p className="mt-1 text-muted">{t("research.memory.emptyBody")}</p>
          </div>
        )}

      {!controller.workflowId && !controller.loading && (
        <p className="px-3 py-4 text-xs text-muted">
          {t("research.memory.noWorkflowBody")}
        </p>
      )}

      {density === "full" && controller.skillError && (
        <p
          role="alert"
          className="border-b border-border-faint px-3 py-2 text-caption text-error"
        >
          {t("research.memory.skill.failed", {
            error: controller.skillError,
          })}
        </p>
      )}

      {density === "full" && controller.skillActivationError?.candidateId === "" && (
        <p
          role="alert"
          className="border-b border-border-faint px-3 py-2 text-caption text-error"
        >
          {t("research.memory.skill.activationFailed", {
            error: controller.skillActivationError.message,
          })}
        </p>
      )}

      {density === "full" && controller.skillActivationsError && (
        <p
          role="alert"
          className="border-b border-border-faint px-3 py-2 text-caption text-error"
        >
          {t("research.memory.skill.activationFailed", {
            error: controller.skillActivationsError,
          })}
        </p>
      )}

      {density === "full" &&
        (controller.skillCandidates ?? []).map((candidate) => {
          const preview = controller.skillActivationPreviews?.[candidate.id];
          const activation =
            controller.skillActivations?.find(
              (item) => item.candidateId === candidate.id,
            ) ?? preview?.latestActivation ?? null;
          const activationStatus = activation?.status ?? candidate.status;
          const isActivationWorking =
            controller.skillActivationWorking?.candidateId === candidate.id;
          const activationFailure =
            controller.skillActivationError?.candidateId === candidate.id
              ? controller.skillActivationError.message
              : null;
          const previewFailure = controller.skillActivationPreviewErrors?.[candidate.id];
          const canApprove =
            candidate.status === "awaiting-approval" &&
            preview &&
            (activationStatus === "awaiting-approval" ||
              activationStatus === "rolled-back");
          const canRollback = activationStatus === "active" && activation;
          const needsRefresh =
            activationStatus === "installing" ||
            activationStatus === "rollback-pending" ||
            activationStatus === "blocked";
          return (
            <div
              key={candidate.id}
              className="border-b border-border-faint px-3 py-3 text-caption"
              aria-busy={isActivationWorking}
            >
              <div className="flex min-w-0 items-center justify-between gap-2">
                <p className="min-w-0 font-medium text-text">
                  {t("research.memory.skill.purpose")}
                </p>
                <span
                  className={cn(
                    "shrink-0 rounded-pill px-2 py-0.5 font-medium",
                    activationStatus === "active"
                      ? "bg-ok/10 text-ok"
                      : activationStatus === "blocked" ||
                          activationStatus === "failed-validation"
                        ? "bg-error/10 text-error"
                        : "bg-warn/10 text-warn",
                  )}
                >
                  {t(skillActivationStatusKey(activationStatus))}
                </span>
              </div>
              <p className="mt-1 break-words text-muted">{candidate.description}</p>
              <p className="mt-1 break-all font-mono text-muted">
                {t("research.memory.skill.approvedTool")}: {candidate.allowedToolsJson[0]}
              </p>
              <p className="mt-1 text-muted">
                {t("research.memory.skill.boundaries")}
              </p>
              <ul
                aria-label={t("research.memory.skill.replayStates")}
                className="mt-2 space-y-1 text-muted"
              >
                {candidate.evaluationJson.results.map((result) => (
                  <li
                    key={`${candidate.id}:${result.name}`}
                    className="flex min-w-0 items-center gap-2"
                  >
                    <span
                      className={cn(
                        "shrink-0 rounded-pill px-1.5 py-0.5 font-medium",
                        result.passed ? "bg-ok/10 text-ok" : "bg-error/10 text-error",
                      )}
                    >
                      {result.passed
                        ? t("research.memory.skill.replayPassed")
                        : t("research.memory.skill.replayFailed")}
                    </span>
                    <span className="min-w-0 break-words">{result.name}</span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-muted">
                {t("research.memory.skill.replays", {
                  passed: candidate.evaluationJson.results.filter(
                    (result) => result.passed,
                  ).length,
                })}
                {" · "}
                {t("research.memory.skill.origin", {
                  id: candidate.originTraceIds[0].slice(0, 8),
                })}
              </p>

              {canApprove && (
                <details className="mt-2 min-w-0 rounded-input bg-surface-2 px-2 py-1.5 text-muted">
                  <summary className="cursor-pointer font-medium text-text">
                    {t("research.memory.skill.reviewActivation")}
                  </summary>
                  <dl className="mt-2 space-y-1.5">
                    {[
                      [t("research.memory.skill.target"), preview.targetRelativePath],
                      [t("research.memory.skill.candidateHash"), preview.candidateContentHash],
                      [t("research.memory.skill.templateHash"), preview.templateSha256],
                      [t("research.memory.skill.evaluationHash"), preview.evaluationSha256],
                      [t("research.memory.skill.approvalHash"), preview.approvalSha256],
                      [
                        t("research.memory.skill.priorHash"),
                        preview.priorSha256 ?? t("research.memory.skill.none"),
                      ],
                    ].map(([label, value]) => (
                      <div key={label} className="min-w-0">
                        <dt className="font-medium text-text">{label}</dt>
                        <dd className="mt-0.5 break-all font-mono text-muted">{value}</dd>
                      </div>
                    ))}
                  </dl>
                </details>
              )}

              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                {canApprove && controller.approveSkill && (
                  <button
                    type="button"
                    onClick={() => void controller.approveSkill?.(candidate, preview)}
                    disabled={isActivationWorking}
                    className="compact-button primary-button disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {t("research.memory.skill.approveAndActivate")}
                  </button>
                )}
                {canRollback && controller.rollbackSkill && (
                  <button
                    type="button"
                    onClick={() => void controller.rollbackSkill?.(activation!)}
                    disabled={isActivationWorking}
                    className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {t("research.memory.skill.rollback")}
                  </button>
                )}
                {needsRefresh && (
                  <button
                    type="button"
                    onClick={() => void controller.refresh()}
                    disabled={controller.loading}
                    className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {t("research.memory.skill.refreshStatus")}
                  </button>
                )}
                {isActivationWorking && (
                  <span className="text-muted" aria-live="polite">
                    {t("research.memory.skill.working")}
                  </span>
                )}
              </div>

              {activationFailure && (
                <p role="alert" className="mt-2 break-words text-error">
                  {t("research.memory.skill.activationFailed", {
                    error: activationFailure,
                  })}
                </p>
              )}
              {previewFailure && (
                <p role="alert" className="mt-2 break-words text-error">
                  {t("research.memory.skill.activationFailed", {
                    error: previewFailure,
                  })}
                </p>
              )}
            </div>
          );
        })}

      {density === "full" &&
        (controller.skillActivations ?? [])
          .filter(
            (activation) =>
              !(controller.skillCandidates ?? []).some(
                (candidate) => candidate.id === activation.candidateId,
              ),
          )
          .map((activation) => {
            const isActivationWorking =
              controller.skillActivationWorking?.candidateId === activation.candidateId;
            const needsRefresh =
              activation.status === "installing" ||
              activation.status === "rollback-pending" ||
              activation.status === "blocked";
            return (
              <div
                key={`activation:${activation.id}`}
                className="border-b border-border-faint px-3 py-3 text-caption"
                aria-busy={isActivationWorking}
              >
                <div className="flex min-w-0 items-center justify-between gap-2">
                  <p className="min-w-0 font-medium text-text">
                    {t("research.memory.skill.purpose")}
                  </p>
                  <span className="shrink-0 rounded-pill bg-surface-2 px-2 py-0.5 font-medium text-muted">
                    {t(skillActivationStatusKey(activation.status))}
                  </span>
                </div>
                <p className="mt-1 break-all font-mono text-muted">
                  {t("research.memory.skill.target")}: {activation.targetRelativePath}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  {activation.status === "active" && controller.rollbackSkill && (
                    <button
                      type="button"
                      onClick={() => void controller.rollbackSkill?.(activation)}
                      disabled={isActivationWorking}
                      className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {t("research.memory.skill.rollback")}
                    </button>
                  )}
                  {needsRefresh && (
                    <button
                      type="button"
                      onClick={() => void controller.refresh()}
                      disabled={controller.loading}
                      className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {t("research.memory.skill.refreshStatus")}
                    </button>
                  )}
                </div>
              </div>
            );
          })}

      {visibleItems.map((memory) => {
        const isWorking = controller.working?.memoryId === memory.id;
        const actionFailure =
          controller.actionError?.memoryId === memory.id
            ? controller.actionError.message
            : null;
        return (
          <article
            key={memory.id}
            className="border-b border-border-faint px-3 py-3 last:border-b-0"
            aria-busy={isWorking}
          >
            <div className="flex min-w-0 items-start gap-2">
              <span
                className={cn(
                  "mt-0.5 shrink-0 rounded-pill px-2 py-0.5 text-caption font-medium",
                  memory.status === "candidate"
                    ? "bg-warn/10 text-warn"
                    : memory.status === "committed"
                      ? "bg-ok/10 text-ok"
                      : "bg-surface-2 text-muted",
                )}
              >
                {t(statusKey(memory.status))}
              </span>
              <p
                className={cn(
                  "min-w-0 flex-1 break-words text-xs leading-5 text-text",
                  density === "compact" && "line-clamp-2",
                )}
              >
                {memory.contentJson.kind === "remembered-evidence"
                  ? t("research.memory.rememberedEvidenceSummary", {
                      page:
                        typeof memory.contentJson.pageIndex === "number"
                          ? memory.contentJson.pageIndex + 1
                          : "—",
                    })
                  : memorySummary(memory)}
              </p>
              <span className="shrink-0 text-caption text-muted">
                {t("research.memory.revision", { revision: memory.revision })}
              </span>
            </div>
            <div className="mt-1.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-caption text-muted">
              <span>{t(contextKey(memory.context))}</span>
              {density === "full" &&
                memory.context.state === "selected" &&
                memory.context.snapshotId && (
                  <>
                    <span aria-hidden="true">·</span>
                    <span
                      className="font-mono"
                      title={memory.context.snapshotId}
                    >
                      {t("research.memory.contextSnapshot", {
                        id: memory.context.snapshotId.slice(0, 8),
                      })}
                    </span>
                  </>
                )}
              <span aria-hidden="true">·</span>
              <span className="truncate">
                {t("research.memory.sourceCount", {
                  count: memory.sourceRefs.length + memory.artifactRefs.length,
                })}
              </span>
              {density === "full" && memory.previousId && (
                <>
                  <span aria-hidden="true">·</span>
                  <span className="truncate">
                    {t("research.memory.previousRevision", {
                      id: memory.previousId,
                    })}
                  </span>
                </>
              )}
            </div>

            {density === "full" &&
              memory.sourceRefs.length + memory.artifactRefs.length > 0 && (
              <ul
                aria-label={t("research.memory.dependencies")}
                className="mt-2 space-y-1 text-caption text-muted"
              >
                {memory.sourceRefs.map((source) => (
                  <li
                    key={`source:${source.type}:${source.id}`}
                    className="break-all"
                  >
                    {source.type} · {source.id}
                  </li>
                ))}
                {memory.artifactRefs.map((artifact) => (
                  <li
                    key={`artifact:${artifact.type}:${artifact.id}`}
                    className="break-all"
                  >
                    {artifact.type} · {artifact.id}
                  </li>
                ))}
              </ul>
            )}

            <div className="mt-2 flex flex-wrap gap-1.5">
                {memory.availableActions.some((action) => action === "accept") && (
                  <button
                    type="button"
                    onClick={() => void controller.resolve(memory, "accept")}
                    disabled={isWorking}
                    className="compact-button primary-button disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <Check size={12} />
                    {t("research.memory.accept")}
                  </button>
                )}
                {memory.availableActions.some((action) => action === "reject") && (
                  <button
                    type="button"
                    onClick={() => void controller.resolve(memory, "reject")}
                    disabled={isWorking}
                    className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <X size={12} />
                    {t("research.memory.reject")}
                  </button>
                )}
                {memory.availableActions.some((action) => action === "invalidate") && (
                  <button
                    type="button"
                    onClick={() => void controller.invalidate(memory)}
                    disabled={isWorking}
                    className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {t("research.memory.markStale")}
                  </button>
                )}
                {memory.status === "committed" &&
                  memory.contentJson.kind === "remembered-evidence" &&
                  controller.saveAsProcedure && (
                    <button
                      type="button"
                      onClick={() => void controller.saveAsProcedure?.(memory)}
                      disabled={controller.skillWorkingMemoryId === memory.id}
                      className="compact-button secondary-button disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {controller.skillWorkingMemoryId === memory.id
                        ? t("research.memory.skill.saving")
                        : t("research.memory.skill.save")}
                    </button>
                  )}
                {isWorking && (
                  <span className="self-center text-caption text-muted" aria-live="polite">
                    {t("research.memory.working")}
                  </span>
                )}
            </div>

            {actionFailure && (
              <p role="alert" className="mt-2 break-words text-caption text-error">
                {t("research.memory.actionFailed", { error: actionFailure })}
              </p>
            )}
          </article>
        );
      })}

      {density === "compact" && totalCount === 0 && controller.workspace && (
        <p className="border-t border-border-faint px-3 py-2 text-caption text-muted">
          {t("research.memory.empty")}
        </p>
      )}
    </section>
  );
}
