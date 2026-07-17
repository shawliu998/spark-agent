import { CheckCircle2, Circle, Clock3, Cpu, LoaderCircle, Plus, Sparkles, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { SessionMeta } from "@ai4s/sdk";
import { cn } from "@/lib/cn";

export type ParallelTaskStatus = "running" | "waiting" | "recovering" | "complete" | "failed";

export interface ParallelTaskCenterProps {
  sessions: SessionMeta[];
  currentId: string | null;
  runningSessions: Record<string, true>;
  waitingSessions?: Record<string, true>;
  failedSessions?: Record<string, string>;
  recoveringSessions?: Record<string, true>;
  sessionModels?: Record<string, string | undefined>;
  onOpen: (sessionId: string) => void;
  onNew: () => void;
  canSynthesize?: boolean;
  synthesizing?: boolean;
  onSynthesize?: () => void;
}

function taskStatus(
  session: SessionMeta,
  runningSessions: Record<string, true>,
  waitingSessions: Record<string, true>,
  failedSessions: Record<string, string>,
  recoveringSessions: Record<string, true>,
): ParallelTaskStatus {
  if (failedSessions[session.id]) return "failed";
  if (waitingSessions[session.id]) return "waiting";
  if (recoveringSessions[session.id]) return "recovering";
  if (runningSessions[session.id]) return "running";
  return "complete";
}

function StatusIcon({ status }: { status: ParallelTaskStatus }) {
  if (status === "running") return <LoaderCircle size={15} className="animate-spin text-accent" />;
  if (status === "complete") return <CheckCircle2 size={15} className="text-emerald-600 dark:text-emerald-400" />;
  if (status === "failed") return <XCircle size={15} className="text-danger" />;
  return <Clock3 size={15} className="text-muted" />;
}

/** A read-only overview of concurrently active sessions. It deliberately has
 * no scheduling controls: session execution and approval remain owned by the
 * runtime, while this component only opens a task or starts a new draft. */
export function ParallelTaskCenter({
  sessions,
  currentId,
  runningSessions,
  waitingSessions = {},
  failedSessions = {},
  recoveringSessions = {},
  sessionModels = {},
  onOpen,
  onNew,
  canSynthesize = false,
  synthesizing = false,
  onSynthesize,
}: ParallelTaskCenterProps) {
  const { t } = useTranslation("session");
  const orderedSessions = [...sessions].sort((a, b) => {
    const weight = (id: string) => (waitingSessions[id] ? 2 : runningSessions[id] ? 1 : 0);
    return weight(b.id) - weight(a.id);
  });

  return (
    <section aria-label={t("parallelTasks.aria", { defaultValue: "Parallel tasks" })} className="rounded-card border border-border bg-surface shadow-card">
      <header className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <h2 className="text-sm font-medium text-text">{t("parallelTasks.title", { defaultValue: "Task center" })}</h2>
          <p className="mt-0.5 text-xs text-muted">
            {t("parallelTasks.subtitle", { defaultValue: "Monitor independent research tasks" })}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {onSynthesize && (
            <button
              type="button"
              onClick={onSynthesize}
              disabled={!canSynthesize || synthesizing}
              className="inline-flex items-center gap-1.5 rounded-input border border-border px-2.5 py-1.5 text-xs font-medium text-text hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {synthesizing ? (
                <LoaderCircle size={14} className="animate-spin" />
              ) : (
                <Sparkles size={14} />
              )}
              {t("parallelTasks.synthesize", { defaultValue: "Synthesize" })}
            </button>
          )}
          <button
            type="button"
            onClick={onNew}
            className="inline-flex items-center gap-1.5 rounded-input bg-accent px-2.5 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <Plus size={14} />
            {t("parallelTasks.new", { defaultValue: "New task" })}
          </button>
        </div>
      </header>

      {orderedSessions.length === 0 ? (
        <div className="px-4 py-7 text-center text-sm text-muted">
          {t("parallelTasks.empty", { defaultValue: "No research tasks yet." })}
        </div>
      ) : (
        <ul className="divide-y divide-border" aria-label={t("parallelTasks.listAria", { defaultValue: "Research tasks" })}>
          {orderedSessions.map((session) => {
            const status = taskStatus(
              session,
              runningSessions,
              waitingSessions,
              failedSessions,
              recoveringSessions,
            );
            const isCurrent = currentId === session.id;
            const routedModel =
              sessionModels[session.id] ??
              t("parallelTasks.runtimeDefault", { defaultValue: "Runtime default" });
            const statusLabel = t(`parallelTasks.status.${status}`, {
              defaultValue:
                status === "running"
                  ? "Running"
                  : status === "complete"
                    ? "Complete"
                    : status === "failed"
                      ? "Failed"
                      : status === "recovering"
                        ? "Recovering"
                      : "Waiting",
            });
            return (
              <li key={session.id}>
                <button
                  type="button"
                  onClick={() => onOpen(session.id)}
                  aria-current={isCurrent ? "page" : undefined}
                  className={cn(
                    "flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
                    isCurrent && "bg-surface-2",
                  )}
                >
                  <StatusIcon status={status} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-text">{session.title}</span>
                    <span className="mt-0.5 flex items-center gap-1.5 text-xs text-muted">
                      <span>{statusLabel}</span>
                      <span aria-hidden={true}>·</span>
                      <Cpu size={12} aria-hidden={true} />
                      <span className="truncate">
                        {t("parallelTasks.requestedModel", { defaultValue: "Requested" })}: {routedModel}
                      </span>
                    </span>
                  </span>
                  {isCurrent && <Circle size={8} fill="currentColor" className="shrink-0 text-accent" aria-label={t("parallelTasks.current", { defaultValue: "Current task" })} />}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
