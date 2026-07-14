import { AlertTriangle, CheckCircle2, Loader2, Plus, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ResearchWorkflowSnapshot } from "@spark/research-domain";
import { cn } from "@/lib/cn";

export interface ResearchWorkflowListProps {
  workflows: ResearchWorkflowSnapshot[];
  selectedWorkflowId: string | null;
  loading: boolean;
  disabled: boolean;
  onSelect: (workflowId: string) => void;
  onNew: () => void;
}

function statusTone(snapshot: ResearchWorkflowSnapshot): string {
  if (snapshot.workflow.cancelRequestedAt) return "text-warn";
  if (snapshot.workflow.status === "completed") return "text-ok";
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
  onNew,
}: ResearchWorkflowListProps) {
  const { t } = useTranslation("pages");
  return (
    <section className="border-b border-border">
      <div className="flex items-center justify-between px-4 pb-2 pt-3">
        <span className="text-[11px] font-medium uppercase tracking-wider text-muted">
          {t("research.workflows.heading", { defaultValue: "Research tasks" })}
        </span>
        <div className="flex items-center gap-1">
          {loading && <Loader2 size={12} className="animate-spin text-muted" />}
          <button
            type="button"
            onClick={onNew}
            disabled={disabled}
            className="rounded p-1 text-muted hover:bg-surface-2 hover:text-text disabled:opacity-40"
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
          <p className="px-2 pb-2 text-[11px] leading-relaxed text-muted">
            {t("research.workflows.empty", {
              defaultValue: "No saved research tasks yet.",
            })}
          </p>
        )}
        {workflows.map((item) => {
          const attention = item.allowedActions.some(
            (action) => action === "approve-plan" || action === "retry" || action === "resume",
          );
          const status = item.workflow.cancelRequestedAt
            ? t("research.workflowStatus.cancelling", { defaultValue: "cancelling" })
            : t(`research.workflowStatus.${item.workflow.status}`, {
                defaultValue: item.workflow.status,
              });
          return (
            <button
              key={item.workflow.id}
              type="button"
              onClick={() => onSelect(item.workflow.id)}
              className={cn(
                "flex w-full items-start gap-2 rounded-input px-2 py-2 text-left hover:bg-surface-2",
                selectedWorkflowId === item.workflow.id && "bg-surface-2",
              )}
            >
              {attention ? (
                <AlertTriangle size={13} className="mt-0.5 shrink-0 text-warn" />
              ) : item.workflow.status === "completed" ? (
                <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-ok" />
              ) : (
                <Sparkles size={13} className="mt-0.5 shrink-0 text-accent" />
              )}
              <span className="min-w-0 flex-1">
                <span className="block line-clamp-2 text-[11px] font-medium leading-snug text-text">
                  {item.workflow.goal}
                </span>
                <span className={cn("mt-1 block text-[10px]", statusTone(item))}>
                  {status}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
