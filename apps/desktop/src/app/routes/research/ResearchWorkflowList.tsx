import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  FileSearch,
  Loader2,
  Plus,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ResearchWorkflowSnapshot } from "@spark/research-domain";
import { cn } from "@/lib/cn";

export interface ResearchWorkflowListProps {
  workflows: ResearchWorkflowSnapshot[];
  selectedWorkflowId: string | null;
  loading: boolean;
  disabled: boolean;
  onSelect: (workflowId: string) => void;
  onOpenReport: (workflowId: string) => void;
  onNew: () => void;
}

function statusTone(snapshot: ResearchWorkflowSnapshot): string {
  if (snapshot.workflow.cancelRequestedAt) return "text-warn";
  if (snapshot.workflow.status === "completed") return "text-ok";
  if (
    snapshot.workflow.status === "waiting-clarification" ||
    snapshot.workflow.status === "unsupported"
  ) {
    return "text-warn";
  }
  if (snapshot.workflow.status === "failed" || snapshot.workflow.status === "blocked") {
    return "text-error";
  }
  return "text-muted";
}

export function ResearchWorkflowList({
  workflows,
  selectedWorkflowId,
  loading,
  disabled,
  onSelect,
  onOpenReport,
  onNew,
}: ResearchWorkflowListProps) {
  const { t } = useTranslation("pages");
  return (
    <section className="border-b border-border">
      <div className="flex items-center justify-between px-4 pb-3 pt-4">
        <span className="text-xs font-medium text-muted">
          {t("research.workflows.heading", { defaultValue: "Research tasks" })}
        </span>
        <div className="flex items-center gap-1">
          {loading && <Loader2 size={12} className="animate-spin text-muted" />}
          <button
            type="button"
            onClick={onNew}
            disabled={disabled}
            className="touch-target flex h-10 w-10 items-center justify-center rounded-input text-muted hover:bg-surface-2 hover:text-text disabled:opacity-40"
            aria-label={t("research.workflows.newAria", {
              defaultValue: "Start a new research task",
            })}
          >
            <Plus size={13} />
          </button>
        </div>
      </div>
      <div className="max-h-40 overflow-y-auto px-2 pb-2">
        {!loading && workflows.length === 0 && (
          <p className="px-2 pb-4 pt-2 text-xs leading-relaxed text-muted">
            {t("research.workflows.empty", {
              defaultValue: "No saved research tasks yet.",
            })}
          </p>
        )}
        {workflows.map((item) => {
          const attention = item.allowedActions.some(
            (action) =>
              action === "approve-plan" ||
              action === "approve-analysis" ||
              action === "reject-analysis" ||
              action === "accept-review-warnings" ||
              action === "retry" ||
              action === "resume",
          ) || item.workflow.status === "waiting-clarification";
          const status = item.workflow.cancelRequestedAt
            ? t("research.workflowStatus.cancelling", { defaultValue: "cancelling" })
            : t(`research.workflowStatus.${item.workflow.status}`, {
                defaultValue: item.workflow.status,
              });
          const remoteAssisted =
            item.workflow.generationMode === "remote-model-assisted";
          const reportReady =
            item.workflow.status === "completed" &&
            item.workflow.workflowType === "literature-synthesis" &&
            item.result !== null;
          return (
            <div
              key={item.workflow.id}
              className={cn(
                "rounded-input hover:bg-surface-2",
                selectedWorkflowId === item.workflow.id &&
                  "bg-surface text-text",
              )}
            >
              <button
                type="button"
                onClick={() => onSelect(item.workflow.id)}
                disabled={disabled}
                className="flex w-full items-start gap-2 px-2 py-2.5 text-left disabled:cursor-not-allowed disabled:opacity-40"
              >
                {attention ? (
                  <AlertTriangle size={13} className="mt-0.5 shrink-0 text-warn" />
                ) : item.workflow.status === "completed" ? (
                  <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-ok" />
                ) : item.workflow.workflowType === "dataset-analysis" ? (
                  <BarChart3 size={13} className="mt-0.5 shrink-0 text-accent" />
                ) : (
                  <FileSearch size={13} className="mt-0.5 shrink-0 text-accent" />
                )}
                <span className="min-w-0 flex-1">
                  <span className="block line-clamp-2 text-xs font-medium leading-snug text-text">
                    {item.workflow.goal}
                  </span>
                  <span className={cn("mt-1 block text-caption", statusTone(item))}>
                    {status}
                    <span className="text-muted">
                      {item.workflow.workflowType === null
                        ? t("research.workflow.listAutoType", {
                            defaultValue: " · auto research",
                          })
                        : item.workflow.workflowType === "dataset-analysis"
                        ? t("research.workflow.listDatasetType", {
                            defaultValue: " · dataset analysis · local isolated runtime",
                          })
                        : remoteAssisted
                          ? t("research.workflow.listLiteratureRemoteType", {
                              defaultValue: " · literature synthesis · model-assisted",
                            })
                          : t("research.workflow.listLiteratureLocalType", {
                              defaultValue: " · literature synthesis · local",
                            })}
                    </span>
                  </span>
                </span>
              </button>
              {reportReady && (
                <button
                  type="button"
                  onClick={() => onOpenReport(item.workflow.id)}
                  disabled={disabled}
                  className="mb-2 ml-7 min-h-8 rounded-input px-2 text-caption font-medium text-link hover:bg-bg disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {t("research.workflows.viewReport", {
                    defaultValue: "View report",
                  })}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
