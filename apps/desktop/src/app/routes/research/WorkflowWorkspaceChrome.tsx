import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  Loader2,
  RefreshCw,
  Sparkles,
  Square,
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
    <section className="rounded-card border border-border bg-surface px-4 py-3 shadow-card">
      <div className="flex items-start gap-3">
        <Sparkles size={16} className="mt-0.5 shrink-0 text-accent" />
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
            {t("research.workflow.goal", { defaultValue: "Research goal" })}
          </p>
          <h3 className="mt-1 text-sm font-medium leading-relaxed text-text">
            {workflow.goal}
          </h3>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] text-muted">
            <WorkflowStatusBadge snapshot={snapshot} />
            <span className="rounded-full bg-surface-2 px-2 py-0.5 font-medium text-muted ring-1 ring-border">
              {workflow.workflowType === "dataset-analysis"
                ? t("research.workflow.typeDataset", {
                    defaultValue: "Dataset Analysis",
                  })
                : t("research.workflow.typeLiterature", {
                    defaultValue: "Literature Synthesis",
                  })}
            </span>
            <GenerationModeBadge mode={generationModeForSnapshot(snapshot)} />
            <ConnectionBadge connection={connection} />
            <button
              type="button"
              onClick={onOpenActivity}
              className="text-link hover:underline"
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
              className="flex items-center gap-1.5 rounded-input border border-border px-2.5 py-1.5 text-xs text-text hover:bg-surface-2 disabled:opacity-40"
            >
              {cancelling ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <Square size={11} />
              )}
              {cancelling
                ? t("research.workflow.cancelling", {
                    defaultValue: "Cancelling…",
                  })
                : t("research.workflow.cancel", { defaultValue: "Cancel" })}
            </button>
          )}
          {(workflow.status === "completed" ||
            workflow.status === "cancelled") && (
            <button
              type="button"
              onClick={onNew}
              className="rounded-input border border-border px-2.5 py-1.5 text-xs text-text hover:bg-surface-2"
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
      ? "bg-ok/10 text-ok ring-ok/20"
      : workflow.status === "blocked" || workflow.status === "failed"
        ? "bg-error/10 text-error ring-error/20"
        : "bg-accent/10 text-accent ring-accent/20";

  return (
    <span className={cn("rounded-full px-2 py-0.5 font-medium ring-1", tone)}>
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
    <span
      className={cn(
        "rounded-full px-2 py-0.5 font-medium ring-1",
        remoteAssisted
          ? "bg-warn/10 text-warn ring-warn/20"
          : "bg-surface-2 text-muted ring-border",
      )}
    >
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
        className="flex items-center gap-1 text-xs text-link hover:underline"
      >
        <RefreshCw size={12} />
        {t("research.retry", { defaultValue: "Retry" })}
      </button>
    </div>
  );
}
