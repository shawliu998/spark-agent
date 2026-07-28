import {
  BrainCircuit,
  Check,
  ShieldAlert,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { AgentDecisionOut } from "@spark/research-domain";
import { AnalysisSpecDiffCard } from "./AnalysisSpecDiffCard";

export interface AgentDecisionCardProps {
  decision: AgentDecisionOut;
  mutating?: boolean;
  onResolve?: (decision: "approved" | "rejected") => Promise<void>;
}

export function AgentDecisionCard({
  decision,
  mutating = false,
  onResolve,
}: AgentDecisionCardProps) {
  const { t } = useTranslation("pages");
  const researchContextSnapshotLabel = t(
    "research.workflow.agentLoop.researchContextSnapshot",
    { defaultValue: "Research Memory snapshot" },
  );
  const waiting =
    decision.status === "waiting-user-confirmation" &&
    decision.requiresUserConfirmation;

  return (
    <section className="overflow-hidden rounded-card border border-accent/25 bg-surface">
      <div className="flex flex-wrap items-center gap-2 border-b border-accent/15 bg-accent/5 px-4 py-3">
        <BrainCircuit size={16} className="text-accent" />
        <h3 className="text-sm font-medium text-text">
          {t("research.workflow.agentLoop.decision", {
            defaultValue: "Agent next-action decision",
          })}
        </h3>
        <span className="font-mono text-caption font-medium text-accent">
          {t(`research.workflow.agentLoop.actions.${decision.action}`)}
        </span>
        <span
          role="status"
          aria-live="polite"
          className="ml-auto rounded-full bg-surface-2 px-2 py-0.5 text-caption text-muted ring-1 ring-border"
        >
          {t(`research.workflow.agentLoop.decisionStatus.${decision.status}`)}
        </span>
      </div>

      <div className="space-y-4 p-4">
        <div>
          <p className="text-caption font-medium text-muted">
            {t("research.workflow.agentLoop.reason", {
              defaultValue: "Reason",
            })}
          </p>
          <p className="mt-1 max-w-[70ch] text-ui leading-relaxed text-text">{decision.reason}</p>
        </div>

        {decision.targetStepKey && (
          <p className="border-y border-border-faint py-2 text-xs text-muted">
            {t("research.workflow.agentLoop.targetStep", {
              defaultValue: "Target step: {{step}}",
              step: decision.targetStepKey,
            })}
          </p>
        )}

        {decision.researchContextSnapshotId &&
          decision.researchContextSnapshotSha256 && (
            <p
              className="border-y border-border-faint py-2 font-mono text-caption text-muted"
              title={`${researchContextSnapshotLabel} ${decision.researchContextSnapshotId}\n${decision.researchContextSnapshotSha256}`}
            >
              {researchContextSnapshotLabel}
              {": "}
              {shortIdentifier(decision.researchContextSnapshotId)}
              {" · "}
              {shortHash(decision.researchContextSnapshotSha256)}
            </p>
          )}

        {decision.clarificationRequests.length > 0 && (
          <div>
            <p className="text-caption font-medium text-muted">
              {t("research.workflow.agentLoop.proposedClarifications", {
                defaultValue: "Proposed clarifications",
              })}
            </p>
            <ul className="mt-2 space-y-2">
              {decision.clarificationRequests.map((request) => (
                <li
                  key={`${request.type}:${request.question}`}
                  className="rounded-input border border-warn/20 bg-warn/5 px-3 py-2.5"
                >
                  <p className="text-ui leading-relaxed text-text">
                    {request.question}
                  </p>
                  <p className="mt-1 font-mono text-caption text-muted">
                    {request.type}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        )}

        {decision.analysisSpecDiff && (
          <AnalysisSpecDiffCard diff={decision.analysisSpecDiff} />
        )}

        {waiting && (
          <div className="space-y-3 rounded-input border border-warn/25 bg-warn/5 px-3 py-3">
            <div className="flex items-start gap-2 text-xs text-muted">
              <ShieldAlert size={14} className="mt-0.5 shrink-0 text-warn" />
              {t("research.workflow.agentLoop.confirmationRequired", {
                defaultValue:
                  "Review the exact method change. Approving creates a new plan that still requires plan and execution approval.",
              })}
            </div>
            {onResolve ? (
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  className="compact-button primary-button"
                  disabled={mutating}
                  onClick={() => void onResolve("approved")}
                >
                  <Check size={14} />
                  {t("research.workflow.agentLoop.approveRevision", {
                    defaultValue: "Approve method change",
                  })}
                </button>
                <button
                  type="button"
                  className="compact-button"
                  disabled={mutating}
                  onClick={() => void onResolve("rejected")}
                >
                  <X size={14} />
                  {t("research.workflow.agentLoop.rejectRevision", {
                    defaultValue: "Reject",
                  })}
                </button>
              </div>
            ) : (
              <p className="text-caption text-muted">
                {t("research.workflow.agentLoop.confirmationReadOnly", {
                  defaultValue:
                    "This proposal requires user confirmation. Reload the research workspace to resolve it.",
                })}
              </p>
            )}
          </div>
        )}

        <details className="border-t border-border-faint pt-2 text-caption text-muted">
          <summary className="min-h-8 cursor-pointer py-1.5 font-mono text-link">
            {decision.reasonCode}
          </summary>
          <p className="break-all pb-1 font-mono">
            {t("research.workflow.agentLoop.decisionLineage", {
              defaultValue:
                "Decision {{id}} · observation {{observationId}} · revision {{revision}}",
              id: decision.id,
              observationId: decision.observationId,
              revision: decision.decisionRevision,
            })}
          </p>
        </details>
      </div>
    </section>
  );
}

function shortIdentifier(value: string): string {
  return value.length <= 12 ? value : `${value.slice(0, 12)}…`;
}

function shortHash(value: string): string {
  return `${value.slice(0, 12)}…`;
}
