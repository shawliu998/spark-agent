import { useState } from "react";
import { LoaderCircle, Minus, Plus, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";

export interface TaskPlanItem {
  id: string;
  title: string;
  prompt: string;
}

export interface TaskPlanComposerProps {
  /** Produces a suggested task list for the current objective. */
  generate: (objective: string) => Promise<Omit<TaskPlanItem, "id">[]> | Omit<TaskPlanItem, "id">[];
  /** Starts the accepted task plan. The parent owns scheduling and persistence. */
  onLaunch: (objective: string, tasks: TaskPlanItem[]) => Promise<void> | void;
  initialObjective?: string;
  initialTasks?: Omit<TaskPlanItem, "id">[];
}

const MIN_TASKS = 2;
const MAX_TASKS = 5;

function makeTask(task: Omit<TaskPlanItem, "id"> = { title: "", prompt: "" }): TaskPlanItem {
  return { ...task, id: crypto.randomUUID() };
}

function initialPlan(tasks?: Omit<TaskPlanItem, "id">[]): TaskPlanItem[] {
  const selected = (tasks?.slice(0, MAX_TASKS) ?? []).map(makeTask);
  while (selected.length < MIN_TASKS) selected.push(makeTask());
  return selected;
}

/**
 * An embedded, runtime-agnostic editor for turning one goal into a small set
 * of independently launchable tasks. Scheduling remains a parent concern.
 */
export function TaskPlanComposer({ generate, onLaunch, initialObjective = "", initialTasks }: TaskPlanComposerProps) {
  const { t } = useTranslation("session");
  const [objective, setObjective] = useState(initialObjective);
  const [tasks, setTasks] = useState(() => initialPlan(initialTasks));
  const [busy, setBusy] = useState<"generate" | "launch" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const updateTask = (id: string, field: "title" | "prompt", value: string) => {
    setTasks((current) => current.map((task) => (task.id === id ? { ...task, [field]: value } : task)));
  };

  const generatePlan = async () => {
    const trimmedObjective = objective.trim();
    if (!trimmedObjective) return;
    setBusy("generate");
    setError(null);
    try {
      setTasks(initialPlan(await generate(trimmedObjective)));
    } catch {
      setError(t("taskPlan.generateError", { defaultValue: "Could not generate a task plan. Try again." }));
    } finally {
      setBusy(null);
    }
  };

  const launch = async () => {
    const trimmedObjective = objective.trim();
    if (!trimmedObjective) return;
    setBusy("launch");
    setError(null);
    try {
      await onLaunch(trimmedObjective, tasks.map((task) => ({ ...task, title: task.title.trim(), prompt: task.prompt.trim() })));
    } catch {
      setError(t("taskPlan.launchError", { defaultValue: "Could not launch this plan. Try again." }));
    } finally {
      setBusy(null);
    }
  };

  const isBusy = busy !== null;
  const canGenerate = objective.trim().length > 0 && !isBusy;
  const canLaunch = objective.trim().length > 0 && !isBusy && tasks.every((task) => task.title.trim() && task.prompt.trim());

  return (
    <section aria-label={t("taskPlan.aria", { defaultValue: "Task plan" })} className="rounded-card border border-border bg-surface shadow-card">
      <div className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-medium text-text">{t("taskPlan.title", { defaultValue: "Plan parallel tasks" })}</h2>
        <p className="mt-0.5 text-xs text-muted">{t("taskPlan.subtitle", { defaultValue: "Break a research goal into independent tasks." })}</p>
      </div>
      <div className="space-y-4 p-4">
        <label className="block text-sm font-medium text-text" htmlFor="task-plan-objective">
          {t("taskPlan.objective", { defaultValue: "Objective" })}
        </label>
        <textarea
          id="task-plan-objective"
          value={objective}
          onChange={(event) => setObjective(event.target.value)}
          disabled={isBusy}
          rows={3}
          placeholder={t("taskPlan.objectivePlaceholder", { defaultValue: "What should this research accomplish?" })}
          className="-mt-2 block w-full resize-y rounded-input border border-border bg-surface-2 px-3 py-2 text-sm text-text placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-60"
        />
        <div className="flex items-center justify-between gap-3">
          <span className="text-xs text-muted">{t("taskPlan.taskCount", { defaultValue: "{{count}} of 5 tasks", count: tasks.length })}</span>
          <button type="button" onClick={generatePlan} disabled={!canGenerate} className="inline-flex items-center gap-1.5 rounded-input border border-border px-2.5 py-1.5 text-xs font-medium text-text hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-60">
            {busy === "generate" ? <LoaderCircle size={14} className="animate-spin" /> : <Sparkles size={14} />}
            {busy === "generate" ? t("taskPlan.generating", { defaultValue: "Generating…" }) : t("taskPlan.generate", { defaultValue: "Generate" })}
          </button>
        </div>

        <ol className="space-y-3" aria-label={t("taskPlan.taskList", { defaultValue: "Planned tasks" })}>
          {tasks.map((task, index) => (
            <li key={task.id} className="rounded-input border border-border bg-surface-2 p-3">
              <div className="mb-2 flex items-center gap-2">
                <span className="text-xs font-medium text-muted">{t("taskPlan.taskNumber", { defaultValue: "Task {{number}}", number: index + 1 })}</span>
                <button type="button" onClick={() => setTasks((current) => current.filter((item) => item.id !== task.id))} disabled={isBusy || tasks.length <= MIN_TASKS} aria-label={t("taskPlan.remove", { defaultValue: "Remove task {{number}}", number: index + 1 })} className="ml-auto rounded p-1 text-muted hover:bg-surface hover:text-text disabled:cursor-not-allowed disabled:opacity-40">
                  <Minus size={15} />
                </button>
              </div>
              <input value={task.title} onChange={(event) => updateTask(task.id, "title", event.target.value)} disabled={isBusy} aria-label={t("taskPlan.titleLabel", { defaultValue: "Task {{number}} title", number: index + 1 })} placeholder={t("taskPlan.titlePlaceholder", { defaultValue: "Task title" })} className="mb-2 block w-full rounded-input border border-border bg-surface px-2.5 py-1.5 text-sm text-text placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-accent disabled:opacity-60" />
              <textarea value={task.prompt} onChange={(event) => updateTask(task.id, "prompt", event.target.value)} disabled={isBusy} rows={2} aria-label={t("taskPlan.promptLabel", { defaultValue: "Task {{number}} prompt", number: index + 1 })} placeholder={t("taskPlan.promptPlaceholder", { defaultValue: "Instructions for this task" })} className="block w-full resize-y rounded-input border border-border bg-surface px-2.5 py-1.5 text-sm text-text placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-accent disabled:opacity-60" />
            </li>
          ))}
        </ol>
        <button type="button" onClick={() => setTasks((current) => [...current, makeTask()])} disabled={isBusy || tasks.length >= MAX_TASKS} className="inline-flex items-center gap-1.5 rounded-input px-2 py-1 text-xs font-medium text-accent hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-50">
          <Plus size={14} />
          {t("taskPlan.add", { defaultValue: "Add task" })}
        </button>
        {error && <p role="alert" className="text-xs text-danger">{error}</p>}
        <div className="flex justify-end border-t border-border pt-3">
          <button type="button" onClick={launch} disabled={!canLaunch} className="inline-flex items-center gap-1.5 rounded-input bg-accent px-3 py-2 text-sm font-medium text-accent-fg hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-60">
            {busy === "launch" && <LoaderCircle size={15} className="animate-spin" />}
            {busy === "launch" ? t("taskPlan.launching", { defaultValue: "Launching…" }) : t("taskPlan.launch", { defaultValue: "Launch tasks" })}
          </button>
        </div>
      </div>
    </section>
  );
}
