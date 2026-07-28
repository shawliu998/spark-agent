import { useTranslation } from "react-i18next";
import {
  CheckCircle2,
  Circle,
  Loader2,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import type {
  ResearchWorkflowMaterializedStep,
  ResearchWorkflowAllowedAction,
  ResearchWorkflowSnapshot,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";
import { statusLabel } from "./workflowModel";

function discoveryProviderName(provider: string): string {
  if (provider === "crossref") return "Crossref";
  if (provider === "openalex") return "OpenAlex";
  if (provider === "arxiv") return "arXiv";
  if (provider === "pubmed") return "PubMed";
  return provider;
}

export function WorkflowProgress({
  snapshot,
}: {
  snapshot: ResearchWorkflowSnapshot;
}) {
  const { t } = useTranslation("pages");
  const { workflow, plan } = snapshot;
  const datasetWorkflow = workflow.workflowType === "dataset-analysis";
  if (!plan) return null;
  const completedCount = plan.steps.filter(
    (step) => step.status === "completed",
  ).length;
  const activeStep =
    plan.steps.find((step) => step.id === workflow.currentStepId) ??
    plan.steps.find((step) =>
      ["running", "waiting-approval", "blocked", "failed"].includes(step.status),
    ) ??
    null;
  const stepObjective = (step: ResearchWorkflowMaterializedStep) => {
    if (plan.spec.planType !== "paper-discovery") return step.objective;
    const discoveryStep = plan.spec.steps.find(
      (candidate) => candidate.key === step.key,
    );
    return discoveryStep
      ? t("research.workflow.discoveryStepObjective", {
          provider: discoveryProviderName(discoveryStep.inputs.provider),
          query: discoveryStep.inputs.query,
        })
      : step.objective;
  };
  const heading = workflow.cancelRequestedAt
    ? t("research.workflow.cancelling", { defaultValue: "Cancelling…" })
    : workflow.status === "reviewing"
      ? datasetWorkflow
        ? t("research.workflow.reviewingDataset", {
            defaultValue: "Reviewing the run and verified artifacts",
          })
        : t("research.workflow.reviewingResearch", {
            defaultValue: "Reviewing claims and evidence",
          })
      : datasetWorkflow
        ? t("research.workflow.progressHeadingDataset", {
            defaultValue: "Dataset analysis progress",
          })
        : t("research.workflow.progressHeadingResearch", {
            defaultValue: "Research progress",
          });

  return (
    <section className="border-y border-border-faint bg-surface py-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h3 className="text-sm font-semibold text-text">{heading}</h3>
        <span className="ml-auto text-caption text-muted">
          {t("research.workflow.completedStepCount", {
            defaultValue: "{{completed}} of {{total}} completed",
            completed: completedCount,
            total: plan.steps.length,
          })}
        </span>
      </div>
      {activeStep && (
        <p className="mt-2 max-w-[72ch] text-xs leading-relaxed text-muted">
          <span className="font-medium text-text">
            {activeStep.status === "waiting-approval"
              ? t("research.workflow.waitingForApproval", {
                  defaultValue: "Waiting for approval",
                })
              : t("research.workflow.currentStep", {
                  defaultValue: "Current step",
                })}
            :
          </span>{" "}
          {stepObjective(activeStep)}
        </p>
      )}
      <ol className="mt-3 divide-y divide-border-faint border-t border-border-faint">
        {plan.steps.map((step) => (
          <WorkflowStepRow
            key={step.id}
            step={step}
            objective={stepObjective(step)}
            current={workflow.currentStepId === step.id}
          />
        ))}
      </ol>
    </section>
  );
}

function WorkflowStepRow({
  step,
  objective,
  current,
}: {
  step: ResearchWorkflowMaterializedStep;
  objective: string;
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
    ) : step.status === "waiting-approval" ? (
      <ShieldAlert size={15} className="text-warn" />
    ) : step.status === "cancelled" ? (
      <XCircle size={15} className="text-muted" />
    ) : (
      <Circle size={14} className="text-muted" />
    );

  return (
    <li
      aria-current={current ? "step" : undefined}
      className={cn(
        "py-3",
        current && "bg-surface-2",
      )}
    >
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 shrink-0">{icon}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <p className="min-w-0 break-words text-xs font-medium text-text">
              {objective}
            </p>
            <code className="text-caption text-muted">
              {step.type === "paper-discovery"
                ? t("research.workflow.discoveryTaskLabel")
                : step.type}
            </code>
          </div>
          <p
            className={cn(
              "mt-1 text-caption text-muted",
              step.status === "waiting-approval" && "font-medium text-warn",
              (step.status === "failed" || step.status === "blocked") &&
                "font-medium text-error",
            )}
          >
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
            <p className="mt-1.5 break-words text-xs leading-relaxed text-muted">
              {step.outputSummary === "Output recorded"
                ? t("research.workflow.outputRecorded")
                : step.outputSummary}
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
  onOpenActivity: () => void;
}

export function WorkflowNeedsAttention({
  snapshot,
  mutating,
  onRetry,
  onResume,
  onNew,
  onOpenActivity,
}: WorkflowNeedsAttentionProps) {
  const { t } = useTranslation("pages");
  const { workflow } = snapshot;
  const allowedActions: readonly ResearchWorkflowAllowedAction[] =
    snapshot.allowedActions;
  const statusReason =
    "statusReason" in workflow ? workflow.statusReason : null;
  const blockingReason = workflow.blockingReason ?? null;
  const reason = statusReason ?? blockingReason;
  const discoveryStoppedForPdf = [
    "discovery-awaiting-pdf",
    "discovery-candidate-target-reached",
    "discovery-no-novelty-limit",
    "discovery-attempt-budget-reached",
  ].includes(reason?.code ?? "");
  const localizedReason =
    discoveryStoppedForPdf
      ? t("research.workflow.discoveryAwaitingPdf")
      : reason?.userMessage;
  const failed = workflow.status === "failed";
  const blocked = workflow.status === "blocked";
  const cancelled = workflow.status === "cancelled";
  const recoveryDescriptionId = `workflow-recovery-${workflow.id}`;
  const unrecoverableUnknown =
    reason?.code === "execution-outcome-unknown" ||
    reason?.code === "orphan-running-task";
  const revisedPlanRequired = [
    "analysis-execution-rejected",
    "analysis-repair-not-safe",
    "analysis-repair-limit-exceeded",
    "analysis-compiled-execution-failed",
    "analysis-review-required",
  ].includes(reason?.code ?? "");

  return (
    <section
      className={cn(
        "border-y px-0 py-4",
        failed || blocked
          ? "border-error/25"
          : cancelled
            ? "border-border-faint"
            : "border-warn/30",
      )}
    >
      <div className="flex items-start">
        <div className="min-w-0 flex-1">
          <h3 className={cn("text-sm font-semibold", failed || blocked ? "text-error" : cancelled ? "text-text" : "text-warn")}>
            {cancelled
              ? t("research.workflow.cancelledHeading", {
                  defaultValue: "Research task cancelled",
                })
              : failed
                ? t("research.workflow.failedHeading", {
                    defaultValue: "Execution failed safely",
                  })
              : t("research.workflow.attentionHeading", {
                  defaultValue: "This task needs attention",
                })}
          </h3>
          <p
            id={recoveryDescriptionId}
            className="mt-1 max-w-[72ch] text-xs leading-relaxed text-muted"
          >
            {unrecoverableUnknown
              ? t("research.workflow.unknownOutcomeBoundary", {
                  defaultValue:
                    "The execution outcome is unknown. There is no safe retry or resume; check Activity or use the canonical cancel action.",
                })
              : revisedPlanRequired
                ? t("research.workflow.revisedPlanBoundary", {
                    defaultValue:
                      "Create a revised plan to generate a new immutable plan, re-plan approval, and a new exact Python approval when execution is needed.",
                  })
                : localizedReason ??
              t("research.workflow.attentionDefault", {
                defaultValue:
                  "Review the completed steps and choose an available recovery action.",
              })}
          </p>
          {reason?.code && (
            <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2 border-y border-current/10 py-2 text-caption text-muted">
              <div className="flex min-w-0 items-baseline gap-2">
                <dt>{t("research.workflow.failureCode", { defaultValue: "Code" })}</dt>
                <dd className="break-all font-mono text-text">{reason.code}</dd>
              </div>
              {blockingReason && (
                <div className="flex items-baseline gap-2">
                  <dt>
                    {t("research.workflow.recoverability", {
                      defaultValue: "Recovery",
                    })}
                  </dt>
                  <dd className="font-medium text-text">
                    {blockingReason.retryable
                      ? t("research.workflow.retryable", {
                          defaultValue: "Retryable from saved state",
                        })
                      : t("research.workflow.notRetryable", {
                          defaultValue: "A new task is required",
                        })}
                  </dd>
                </div>
              )}
            </dl>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            {unrecoverableUnknown ? (
              <button
                type="button"
                onClick={onOpenActivity}
                aria-describedby={recoveryDescriptionId}
                className="min-h-11 rounded-input border border-border bg-surface px-4 py-2 text-xs text-text hover:bg-surface-2"
              >
                {t("research.workflow.checkActivity", { defaultValue: "Check Activity" })}
              </button>
            ) : allowedActions.includes("resume") && (
              <button
                type="button"
                onClick={() => void onResume()}
                disabled={mutating}
                aria-describedby={recoveryDescriptionId}
                className="flex min-h-11 items-center gap-1.5 rounded-input bg-accent px-4 py-2 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
              >
                {mutating && <Loader2 size={13} className="animate-spin" />}
                {revisedPlanRequired
                  ? t("research.workflow.createRevisedPlan", { defaultValue: "Create revised plan" })
                  : t("research.workflow.resume", { defaultValue: "Resume" })}
              </button>
            )}
            {!unrecoverableUnknown && !revisedPlanRequired && allowedActions.includes("retry") && (
              <button
                type="button"
                onClick={() => void onRetry()}
                disabled={mutating}
                aria-describedby={recoveryDescriptionId}
                className="flex min-h-11 items-center gap-1.5 rounded-input bg-accent px-4 py-2 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-40"
              >
                {mutating && <Loader2 size={13} className="animate-spin" />}
                {revisedPlanRequired
                  ? t("research.workflow.createRevisedPlan", { defaultValue: "Create revised plan" })
                  : t("research.workflow.retry", { defaultValue: "Retry" })}
              </button>
            )}
            {!unrecoverableUnknown && allowedActions.length === 0 && (
              <button
                type="button"
                onClick={onNew}
                aria-describedby={recoveryDescriptionId}
                className="min-h-11 rounded-input border border-border bg-surface px-4 py-2 text-xs text-text hover:bg-surface-2"
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
