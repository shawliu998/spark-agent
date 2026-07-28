import { AlertTriangle, Gauge } from "lucide-react";
import { useTranslation } from "react-i18next";
import type {
  AgentLoopLimitState,
  AgentLoopLimitUsage,
} from "@spark/research-domain";
import { cn } from "@/lib/cn";

const LIMIT_KEYS = [
  "agentSteps",
  "planRevisions",
  "analysisSpecRevisions",
  "stepRetries",
  "clarificationRounds",
  "modelDecisions",
  "invalidModelDecisions",
] as const satisfies readonly (keyof AgentLoopLimitState)[];

export function AgentLoopLimitsCard({
  limits,
}: {
  limits: AgentLoopLimitState;
}) {
  const { t } = useTranslation("pages");
  const reached = LIMIT_KEYS.filter((key) => limits[key].reached);
  const limitItems: Array<{
    key: (typeof LIMIT_KEYS)[number];
    label: string;
  }> = [
    {
      key: "agentSteps",
      label: t("research.workflow.agentLoop.limit.agentSteps", {
        defaultValue: "Agent steps",
      }),
    },
    {
      key: "planRevisions",
      label: t("research.workflow.agentLoop.limit.planRevisions", {
        defaultValue: "Plan revisions",
      }),
    },
    {
      key: "analysisSpecRevisions",
      label: t("research.workflow.agentLoop.limit.analysisSpecRevisions", {
        defaultValue: "AnalysisSpec revisions",
      }),
    },
    {
      key: "stepRetries",
      label: t("research.workflow.agentLoop.limit.stepRetries", {
        defaultValue: "Step retries",
      }),
    },
    {
      key: "clarificationRounds",
      label: t("research.workflow.agentLoop.limit.clarificationRounds", {
        defaultValue: "Clarification rounds",
      }),
    },
    {
      key: "modelDecisions",
      label: t("research.workflow.agentLoop.limit.modelDecisions", {
        defaultValue: "Model decisions",
      }),
    },
    {
      key: "invalidModelDecisions",
      label: t("research.workflow.agentLoop.limit.invalidModelDecisions", {
        defaultValue: "Invalid model decisions",
      }),
    },
  ];

  return (
    <section
      className={cn(
        "overflow-hidden rounded-card border bg-surface",
        reached.length > 0 ? "border-warn/35" : "border-border",
      )}
    >
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <Gauge size={15} className={reached.length > 0 ? "text-warn" : "text-accent"} />
        <h3 className="text-sm font-medium text-text">
          {t("research.workflow.agentLoop.limits", {
            defaultValue: "Agent loop limits",
          })}
        </h3>
        {reached.length > 0 && (
          <span
            role="status"
            className="ml-auto rounded-full bg-warn/10 px-2 py-0.5 text-caption font-medium text-warn"
          >
            {t("research.workflow.agentLoop.limitReached", {
              defaultValue: "Limit reached",
            })}
          </span>
        )}
      </div>

      <div className="space-y-3 p-4">
        {reached.length > 0 && (
          <div className="flex items-start gap-2 rounded-input border border-warn/25 bg-warn/5 px-3 py-2.5 text-xs text-muted">
            <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warn" />
            <span>
              {t("research.workflow.agentLoop.limitReachedDetail", {
                defaultValue:
                  "The bounded agent loop cannot continue automatically because a persisted safety limit was reached.",
              })}
            </span>
          </div>
        )}
        <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2 xl:grid-cols-3">
          {limitItems.map(({ key, label }) => (
            <LimitUsage key={key} name={label} usage={limits[key]} />
          ))}
        </dl>
      </div>
    </section>
  );
}

function LimitUsage({
  name,
  usage,
}: {
  name: string;
  usage: AgentLoopLimitUsage;
}) {
  const percentage = Math.min(100, (usage.count / Math.max(usage.limit, 1)) * 100);
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <dt className="text-caption font-medium text-text">{name}</dt>
        <dd className={cn("font-mono text-caption", usage.reached ? "text-warn" : "text-muted")}>
          {usage.count}/{usage.limit}
        </dd>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2">
        <div
          className={cn("h-full rounded-full", usage.reached ? "bg-warn" : "bg-accent")}
          style={{ width: `${percentage}%` }}
        />
      </div>
    </div>
  );
}
