import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  Loader2,
  RefreshCw,
} from "lucide-react";
import type {
  ResearchGenerationMode,
  ResearchWorkflowAllowedAction,
  ResearchWorkflowSnapshot,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import type { WorkflowConnectionState } from "./useResearchWorkflow";
import {
  generationModeForSnapshot,
  statusLabel,
} from "./workflowModel";

interface WorkflowHeaderProps {
  snapshot: ResearchWorkflowSnapshot;
  mutating: boolean;
  connection: WorkflowConnectionState;
  onCancel: () => Promise<void>;
  onNew: () => void;
  onOpenActivity: () => void;
}

export function WorkflowHeader({
  snapshot,
  mutating,
  connection,
  onCancel,
  onNew,
  onOpenActivity,
}: WorkflowHeaderProps) {
  const { t } = useTranslation("pages");
  const { workflow } = snapshot;
  const allowedActions: readonly ResearchWorkflowAllowedAction[] =
    snapshot.allowedActions;
  const cancelling = workflow.cancelRequestedAt != null;

  return (
    <section className="border-b border-border-faint pb-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-muted">
            {t("research.workflow.goal", { defaultValue: "Research question" })}
          </p>
          <h3 className="mt-1 max-w-[70ch] text-base font-semibold leading-6 text-text">
            {workflow.goal}
          </h3>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-caption text-muted">
            <WorkflowStatusBadge snapshot={snapshot} />
            <span className="font-medium text-muted">
              {workflow.workflowType === null
                ? t("research.workflow.typeAuto", {
                    defaultValue: "Auto routing",
                  })
                : workflow.workflowType === "dataset-analysis"
                ? t("research.workflow.typeDataset", {
                    defaultValue: "Dataset Analysis",
                  })
                : t("research.workflow.typeLiterature", {
                    defaultValue: "Literature Synthesis",
                  })}
            </span>
            {workflow.workflowType !== null && (
              <GenerationModeBadge mode={generationModeForSnapshot(snapshot)} />
            )}
            <ConnectionBadge connection={connection} />
            <button
              type="button"
              onClick={onOpenActivity}
              className="min-h-11 text-link hover:underline"
            >
              {t("research.workflow.viewActivity", {
                defaultValue: "View activity",
              })}
            </button>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {allowedActions.includes("cancel") && (
            <button
              type="button"
              onClick={() => void onCancel()}
              disabled={mutating || cancelling}
              className="flex min-h-11 items-center gap-1.5 rounded-input border border-border px-2.5 py-1.5 text-xs text-text hover:bg-surface-2 disabled:opacity-40"
            >
              {cancelling && <Loader2 size={12} className="animate-spin" />}
              {cancelling
                ? t("research.workflow.cancelling", {
                    defaultValue: "Cancelling…",
                  })
                : t("research.workflow.cancel", { defaultValue: "Cancel" })}
            </button>
          )}
          {(workflow.status === "completed" ||
            workflow.status === "unsupported" ||
            workflow.status === "cancelled") && (
            <button
              type="button"
              onClick={onNew}
              className="min-h-11 rounded-input border border-border px-2.5 py-1.5 text-xs text-text hover:bg-surface-2"
            >
              {t("research.workflow.newTask", { defaultValue: "New task" })}
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

function WorkflowStatusBadge({
  snapshot,
}: {
  snapshot: ResearchWorkflowSnapshot;
}) {
  const { t } = useTranslation("pages");
  const { workflow } = snapshot;
  const status = workflow.cancelRequestedAt ? "cancelling" : workflow.status;
  const tone =
    workflow.status === "completed"
      ? "bg-ok"
      : workflow.status === "blocked" || workflow.status === "failed"
        ? "bg-error"
        : workflow.status === "waiting-clarification" ||
            workflow.status === "unsupported"
          ? "bg-warn"
        : "bg-accent";

  return (
    <span className="inline-flex items-center gap-1.5 font-medium text-text">
      <span aria-hidden="true" className={cn("h-1.5 w-1.5 rounded-full", tone)} />
      {t(`research.workflowStatus.${status}`, {
        defaultValue: statusLabel(status),
      })}
    </span>
  );
}

function GenerationModeBadge({ mode }: { mode: ResearchGenerationMode }) {
  const { t } = useTranslation("pages");
  const remoteAssisted = mode === "remote-model-assisted";
  return (
    <span className={cn("font-medium", remoteAssisted ? "text-warn" : "text-muted")}>
      {remoteAssisted
        ? t("research.workflow.generationMode.remote", {
            defaultValue: "remote model-assisted",
          })
        : t("research.workflow.generationMode.local", {
            defaultValue: "local deterministic",
          })}
    </span>
  );
}

function ConnectionBadge({
  connection,
}: {
  connection: WorkflowConnectionState;
}) {
  const { t } = useTranslation("pages");
  if (connection === "idle" || connection === "live") return null;
  return (
    <span className="inline-flex items-center gap-1 text-warn">
      <Loader2 size={10} className="animate-spin" />
      {t(`research.workflow.connection.${connection}`, {
        defaultValue:
          connection === "reconnecting" ? "reconnecting" : "refreshing",
      })}
    </span>
  );
}

export function WorkflowWaiting({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center gap-2 rounded-card border border-border bg-surface py-14 text-sm text-muted">
      <Loader2 size={15} className="animate-spin" />
      {label}
    </div>
  );
}

export function WorkflowError({
  message,
  onRefresh,
}: {
  message: string;
  onRefresh: () => Promise<void>;
}) {
  const { t } = useTranslation("pages");
  return (
    <div className="flex items-start gap-3 rounded-card border border-error/30 bg-error/5 p-4 text-sm">
      <AlertTriangle size={16} className="mt-0.5 shrink-0 text-error" />
      <p className="min-w-0 flex-1 break-words text-xs text-muted">{message}</p>
      <button
        type="button"
        onClick={() => void onRefresh()}
        className="flex min-h-11 items-center gap-1 text-xs text-link hover:underline"
      >
        <RefreshCw size={12} />
        {t("research.refresh", { defaultValue: "Refresh" })}
      </button>
    </div>
  );
}
