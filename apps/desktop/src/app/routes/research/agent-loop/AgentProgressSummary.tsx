import { Activity, BrainCircuit, Eye, RotateCcw } from "lucide-react";
import { useTranslation } from "react-i18next";
import type {
  AgentDecisionOut,
  AgentDecisionSummary,
  StepObservationOut,
} from "@spark/research-domain";

export interface AgentProgressSummaryProps {
  latestObservation: StepObservationOut | null;
  pendingDecision: AgentDecisionOut | null;
  decisionHistory: AgentDecisionSummary[];
}

export function AgentProgressSummary({
  latestObservation,
  pendingDecision,
  decisionHistory,
}: AgentProgressSummaryProps) {
  const { t } = useTranslation("pages");
  const retries = decisionHistory.filter(
    (decision) =>
      decision.action === "retry-step" && decision.status === "applied",
  ).length;
  const phase = pendingDecision?.requiresUserConfirmation
    ? t("research.workflow.agentLoop.phaseConfirmation", {
        defaultValue: "Waiting for user confirmation",
      })
    : pendingDecision
      ? t("research.workflow.agentLoop.phaseDecision", {
          defaultValue: "Next action selected",
        })
    : latestObservation
      ? t("research.workflow.agentLoop.phaseObserved", {
          defaultValue: "Latest step observed",
        })
      : t("research.workflow.agentLoop.phasePreparing", {
          defaultValue: "Preparing the first observation",
        });

  return (
    <section className="border-y border-border-faint py-3">
      <div className="flex items-center gap-2">
        <Activity size={15} className="text-accent" />
        <h3 className="text-sm font-medium text-text">
          {t("research.workflow.agentLoop.progress", {
            defaultValue: "Bounded agent progress",
          })}
        </h3>
        <span role="status" aria-live="polite" className="ml-auto text-caption text-muted">
          {phase}
        </span>
      </div>

      <dl className="mt-3 grid gap-x-6 gap-y-3 sm:grid-cols-3">
        <ProgressMetric
          icon={<Eye size={13} />}
          label={t("research.workflow.agentLoop.latestStep", {
            defaultValue: "Latest step",
          })}
          value={latestObservation?.stepKey ?? "—"}
        />
        <ProgressMetric
          icon={<BrainCircuit size={13} />}
          label={t("research.workflow.agentLoop.latestDecision", {
            defaultValue: "Current decision",
          })}
          value={
            pendingDecision?.action
              ? t(`research.workflow.agentLoop.actions.${pendingDecision.action}`)
              : decisionHistory[decisionHistory.length - 1]?.action
                ? t(`research.workflow.agentLoop.actions.${decisionHistory[decisionHistory.length - 1].action}`)
                : "—"
          }
        />
        <ProgressMetric
          icon={<RotateCcw size={13} />}
          label={t("research.workflow.agentLoop.retryProgress", {
            defaultValue: "Retry progress",
          })}
          value={t("research.workflow.agentLoop.retryProgressValue", {
            defaultValue: "{{retries}} retries · attempt {{attempt}}",
            retries,
            attempt: latestObservation?.attempt ?? 0,
          })}
        />
      </dl>
    </section>
  );
}

function ProgressMetric({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="min-w-0">
      <dt className="flex items-center gap-1.5 text-caption font-medium text-muted">
        {icon}
        {label}
      </dt>
      <dd className="mt-1 truncate text-xs font-medium text-text" title={value}>
        {value}
      </dd>
    </div>
  );
}
