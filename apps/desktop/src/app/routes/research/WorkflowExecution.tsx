import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CheckCircle2,
  Circle,
  Loader2,
  Play,
  RotateCcw,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import type {
  ResearchWorkflowMaterializedStep,
  ResearchWorkflowAllowedAction,
  ResearchWorkflowSnapshot,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import { statusLabel } from "./workflowModel";

export function WorkflowProgress({
  snapshot,
}: {
  snapshot: ResearchWorkflowSnapshot;
}) {
  const { t } = useTranslation("pages");
  const { workflow, plan } = snapshot;
  const datasetWorkflow = workflow.workflowType === "dataset-analysis";
  if (!plan) return null;
  const heading = workflow.cancelRequestedAt
    ? t("research.workflow.cancelling", { defaultValue: "Cancelling…" })
    : workflow.status === "reviewing"
      ? t("research.workflow.reviewing", {
          defaultValue: datasetWorkflow
            ? "Reviewing the run and verified artifacts"
            : "Reviewing claims and evidence",
        })
      : t("research.workflow.progressHeading", {
          defaultValue: datasetWorkflow
            ? "Dataset analysis progress"
            : "Research progress",
        });

  return (
    <section className="rounded-card border border-border bg-surface p-4">
      <div className="flex items-center gap-2">
        {workflow.status === "reviewing" ? (
          <ShieldCheck size={15} className="text-accent" />
        ) : (
          <Loader2 size={15} className="animate-spin text-accent" />
        )}
        <h3 className="text-sm font-medium text-text">{heading}</h3>
      </div>
      <ol className="mt-4 space-y-2">
        {plan.steps.map((step) => (
          <WorkflowStepRow
            key={step.id}
            step={step}
            current={workflow.currentStepId === step.id}
          />
        ))}
      </ol>
    </section>
  );
}

function WorkflowStepRow({
  step,
  current,
}: {
  step: ResearchWorkflowMaterializedStep;
  current: boolean;
}) {
  const { t } = useTranslation("pages");
  const icon =
    step.status === "completed" ? (
      <CheckCircle2 size={15} className="text-ok" />
    ) : step.status === "failed" || step.status === "blocked" ? (
      <XCircle size={15} className="text-error" />
    ) : step.status === "running" ? (
      <Loader2 size={15} className="animate-spin text-accent" />
    ) : (
      <Circle size={14} className="text-muted" />
    );

  return (
    <li
      className={cn(
        "rounded-input border px-3 py-2.5",
        current ? "border-accent/35 bg-accent/5" : "border-border bg-bg",
      )}
    >
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 shrink-0">{icon}</span>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-text">{step.objective}</p>
          <p className="mt-1 text-[10px] text-muted">
            {t(`research.workflow.stepStatus.${step.status}`, {
              defaultValue: statusLabel(step.status),
            })}
            {step.retryCount > 0 &&
              t("research.workflow.retryCount", {
                defaultValue: " · retry {{count}}",
                count: step.retryCount,
              })}
          </p>
          {step.outputSummary && (
            <p className="mt-1.5 text-[11px] leading-relaxed text-muted">
              {step.outputSummary}
            </p>
          )}
        </div>
      </div>
    </li>
  );
}

interface WorkflowNeedsAttentionProps {
  snapshot: ResearchWorkflowSnapshot;
  mutating: boolean;
  onRetry: () => Promise<void>;
  onResume: () => Promise<void>;
  onNew: () => void;
}

export function WorkflowNeedsAttention({
  snapshot,
  mutating,
  onRetry,
  onResume,
  onNew,
}: WorkflowNeedsAttentionProps) {
  const { t } = useTranslation("pages");
  const { workflow } = snapshot;
  const allowedActions: readonly ResearchWorkflowAllowedAction[] =
    snapshot.allowedActions;
  const statusReason =
    "statusReason" in workflow ? workflow.statusReason : null;

  return (
    <section className="rounded-card border border-warn/35 bg-warn/5 p-4">
      <div className="flex items-start gap-3">
        <AlertTriangle size={16} className="mt-0.5 shrink-0 text-warn" />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-medium text-text">
            {workflow.status === "cancelled"
              ? t("research.workflow.cancelledHeading", {
                  defaultValue: "Research task cancelled",
                })
              : t("research.workflow.attentionHeading", {
                  defaultValue: "This task needs attention",
                })}
          </h3>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            {statusReason?.userMessage ??
              workflow.blockingReason?.userMessage ??
              t("research.workflow.attentionDefault", {
                defaultValue:
                  "Review the completed steps and choose an available recovery action.",
              })}
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            {allowedActions.includes("resume") && (
              <button
                type="button"
                onClick={() => void onResume()}
                disabled={mutating}
                className="flex items-center gap-1.5 rounded-input bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg disabled:opacity-40"
              >
                {mutating ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Play size={13} />
                )}
                {t("research.workflow.resume", { defaultValue: "Resume" })}
              </button>
            )}
            {allowedActions.includes("retry") && (
              <button
                type="button"
                onClick={() => void onRetry()}
                disabled={mutating}
                className="flex items-center gap-1.5 rounded-input border border-border bg-surface px-3 py-1.5 text-xs text-text disabled:opacity-40"
              >
                {mutating ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <RotateCcw size={13} />
                )}
                {t("research.workflow.retry", { defaultValue: "Retry" })}
              </button>
            )}
            {allowedActions.length === 0 && (
              <button
                type="button"
                onClick={onNew}
                className="rounded-input border border-border bg-surface px-3 py-1.5 text-xs text-text"
              >
                {t("research.workflow.newTask", { defaultValue: "New task" })}
              </button>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
