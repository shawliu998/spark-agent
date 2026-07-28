import { useTranslation } from "react-i18next";
import type { ResearchWorkflowSnapshot } from "@spark/research-domain";
import { cn } from "@/lib/cn";

interface ResearchStageRailProps {
  projectReady: boolean;
  sourceCount: number;
  snapshot: ResearchWorkflowSnapshot | null;
}

export function ResearchStageRail({
  projectReady,
  sourceCount,
  snapshot,
}: ResearchStageRailProps) {
  const { t } = useTranslation("pages");
  const stages = [
    t("research.stage.project", { defaultValue: "Project" }),
    t("research.stage.sources", { defaultValue: "Sources" }),
    t("research.stage.plan", { defaultValue: "Plan" }),
    t("research.stage.execution", { defaultValue: "Execution" }),
    t("research.stage.evidence", { defaultValue: "Evidence" }),
    t("research.stage.results", { defaultValue: "Results" }),
    t("research.stage.review", { defaultValue: "Review" }),
  ];

  const activeIndex = workflowStageIndex(projectReady, sourceCount, snapshot);
  const completed = snapshot?.workflow.status === "completed";
  const attention = snapshot
    ? snapshot.workflow.status === "waiting-clarification" ||
      snapshot.workflow.status === "waiting-plan-approval" ||
      snapshot.workflow.status === "blocked" ||
      snapshot.workflow.status === "failed" ||
      snapshot.workflow.status === "cancelled"
    : false;

  return (
    <nav
      aria-label={t("research.stage.aria", {
        defaultValue: "Research workflow stages",
      })}
      className="border-b border-border px-3 py-3"
    >
      <div className="mb-2 flex items-baseline justify-between gap-2 px-1">
        <h2 className="text-xs font-medium text-muted">
          {t("research.stage.heading", { defaultValue: "Research process" })}
        </h2>
        <span className="text-caption tabular-nums text-muted">
          {t("research.stage.position", {
            defaultValue: "{{current}} of {{total}}",
            current: completed ? stages.length : activeIndex + 1,
            total: stages.length,
          })}
        </span>
      </div>
      <ol className="space-y-0.5">
        {stages.map((stage, index) => {
          const isComplete = completed || index < activeIndex;
          const isActive = !completed && index === activeIndex;
          const needsAttention = isActive && attention;
          return (
            <li
              key={stage}
              aria-current={isActive ? "step" : undefined}
              className={cn(
                "relative flex min-h-9 items-center gap-2 rounded-input px-1.5 text-xs",
                isActive && "bg-surface text-text",
                !isActive && (isComplete ? "text-text" : "text-muted"),
              )}
            >
              {index < stages.length - 1 && (
                <span
                  aria-hidden="true"
                  className={cn(
                    "absolute left-[11px] top-6 h-6 w-px",
                    isComplete ? "bg-accent/45" : "bg-border",
                  )}
                />
              )}
              <span
                className={cn(
                  "relative z-[1] flex h-3 w-3 shrink-0 items-center justify-center rounded-full bg-surface-2",
                )}
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    "block rounded-full",
                    isComplete && "h-1.5 w-1.5 bg-accent",
                    isActive && !needsAttention && "h-2 w-2 border border-accent bg-surface",
                    needsAttention && "h-2 w-2 bg-warn",
                    !isComplete && !isActive && "h-1.5 w-1.5 border border-muted/70 bg-surface-2",
                  )}
                />
              </span>
              <span className={cn("truncate", isActive && "font-medium")}>{stage}</span>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export function workflowStageIndex(
  projectReady: boolean,
  sourceCount: number,
  snapshot: ResearchWorkflowSnapshot | null,
): number {
  if (!projectReady) return 0;
  if (!snapshot) return sourceCount === 0 ? 1 : 2;
  if (snapshot.workflow.status === "reviewing" || snapshot.latestReview) return 6;
  if (snapshot.workflow.status === "completed") return 6;
  if (
    snapshot.workflow.status === "routing" ||
    snapshot.workflow.status === "waiting-clarification" ||
    snapshot.workflow.status === "planning" ||
    snapshot.workflow.status === "waiting-plan-approval" ||
    snapshot.workflow.status === "unsupported"
  ) {
    return 2;
  }
  const currentStep = snapshot.plan?.steps.find(
    (step) => step.id === snapshot.workflow.currentStepId,
  );
  if (currentStep?.type === "extract-local-evidence") return 4;
  if (
    currentStep?.type === "synthesize-extractive-claims" ||
    currentStep?.type === "collect-artifacts"
  ) {
    return 5;
  }
  if (snapshot.result) return 5;
  return 3;
}
