import {
  CheckCircle2,
  Clock3,
  History,
  RotateCcw,
  XCircle,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { AgentDecisionSummary } from "@spark/research-domain";

export function DecisionHistory({
  decisions,
}: {
  decisions: AgentDecisionSummary[];
}) {
  const { t } = useTranslation("pages");
  const researchContextSnapshotLabel = t(
    "research.workflow.agentLoop.researchContextSnapshot",
    { defaultValue: "Research Memory snapshot" },
  );
  if (decisions.length === 0) return null;

  return (
    <section className="overflow-hidden rounded-card border border-border bg-surface">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <History size={15} className="text-accent" />
        <h3 className="text-sm font-medium text-text">
          {t("research.workflow.agentLoop.decisionHistory", {
            defaultValue: "Decision history",
          })}
        </h3>
        <span className="ml-auto text-caption text-muted">{decisions.length}</span>
      </div>
      <ol className="divide-y divide-border-faint px-4">
        {[...decisions].reverse().map((decision) => (
          <li key={decision.id} className="flex items-start gap-3 py-3">
            <DecisionStatusIcon status={decision.status} action={decision.action} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-xs font-medium text-text">
                  {t(`research.workflow.agentLoop.actions.${decision.action}`)}
                </p>
                <span className="rounded-full bg-surface-2 px-2 py-0.5 text-caption text-muted ring-1 ring-border">
                  {t(`research.workflow.agentLoop.decisionStatus.${decision.status}`)}
                </span>
                {decision.requiresUserConfirmation && (
                  <span className="rounded-full bg-warn/10 px-2 py-0.5 text-caption text-warn">
                    {t("research.workflow.agentLoop.userConfirmed", {
                      defaultValue: "user confirmation",
                    })}
                  </span>
                )}
              </div>
              <p className="mt-1 text-xs leading-relaxed text-muted">
                {decision.reason}
              </p>
              <p className="mt-1 break-all font-mono text-caption text-muted">
                {decision.id} · {formatDecisionTime(decision.appliedAt ?? decision.createdAt)}
              </p>
              {decision.researchContextSnapshotId &&
                decision.researchContextSnapshotSha256 && (
                  <p
                    className="mt-1 break-all font-mono text-caption text-muted"
                    title={`${researchContextSnapshotLabel} ${decision.researchContextSnapshotId}\n${decision.researchContextSnapshotSha256}`}
                  >
                    {researchContextSnapshotLabel}
                    {": "}
                    {shortIdentifier(decision.researchContextSnapshotId)}
                    {" · "}
                    {shortHash(decision.researchContextSnapshotSha256)}
                  </p>
                )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function DecisionStatusIcon({
  status,
  action,
}: Pick<AgentDecisionSummary, "status" | "action">) {
  if (status === "applied") {
    return action === "retry-step" ? (
      <RotateCcw size={14} className="mt-0.5 shrink-0 text-accent" />
    ) : (
      <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-ok" />
    );
  }
  if (status === "rejected" || status === "failed") {
    return <XCircle size={14} className="mt-0.5 shrink-0 text-error" />;
  }
  return <Clock3 size={14} className="mt-0.5 shrink-0 text-warn" />;
}

function formatDecisionTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function shortIdentifier(value: string): string {
  return value.length <= 12 ? value : `${value.slice(0, 12)}…`;
}

function shortHash(value: string): string {
  return `${value.slice(0, 12)}…`;
}
