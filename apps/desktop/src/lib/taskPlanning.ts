export type ResearchTaskKind = "evidence" | "execution" | "critical-review";

export interface EditableTaskPlanItem {
  id: string;
  kind: ResearchTaskKind;
  title: string;
  prompt: string;
}

export interface EditableTaskPlan {
  goal: string;
  tasks: EditableTaskPlanItem[];
}

export interface TaskPlanValidationResult {
  valid: boolean;
  errors: string[];
}

const MIN_TASKS = 2;
const MAX_TASKS = 5;

function normalizedGoal(goal: string): string {
  return goal.trim().replace(/\s+/g, " ");
}

/**
 * Produce a small, deterministic plan whose tasks can be run independently.
 * The returned array is intentionally editable by the UI before execution.
 */
export function createTaskPlan(goal: string): EditableTaskPlan {
  const subject = normalizedGoal(goal);

  return {
    goal: subject,
    tasks: [
      {
        id: "evidence",
        kind: "evidence",
        title: "Gather evidence",
        prompt: `Independently gather and assess the most relevant evidence for this research goal: ${subject}. Record sources, key findings, uncertainty, and any gaps. Do not depend on other tasks.`,
      },
      {
        id: "execution",
        kind: "execution",
        title: "Develop an execution approach",
        prompt: `Independently develop a practical research approach for this goal: ${subject}. Define methods, inputs, concrete steps, and expected outputs. State assumptions explicitly. Do not depend on other tasks.`,
      },
      {
        id: "critical-review",
        kind: "critical-review",
        title: "Critically review the goal",
        prompt: `Independently challenge this research goal: ${subject}. Identify risks, alternative explanations, confounders, validation checks, and conditions that would change the conclusion. Do not depend on other tasks.`,
      },
    ],
  };
}

export const generateTaskPlan = createTaskPlan;

/** Validate the editable task list before it is sent to the task runner. */
export function validateTaskPlan(tasks: readonly EditableTaskPlanItem[]): TaskPlanValidationResult {
  const errors: string[] = [];

  if (tasks.length < MIN_TASKS || tasks.length > MAX_TASKS) {
    errors.push(`A task plan must contain between ${MIN_TASKS} and ${MAX_TASKS} tasks.`);
  }

  const ids = new Set<string>();
  for (const task of tasks) {
    if (!task.id.trim()) errors.push("Every task needs an id.");
    else if (ids.has(task.id)) errors.push(`Task id "${task.id}" is duplicated.`);
    else ids.add(task.id);

    if (!task.title.trim()) errors.push(`Task "${task.id || "unknown"}" needs a title.`);
    if (!task.prompt.trim()) errors.push(`Task "${task.id || "unknown"}" needs a prompt.`);
  }

  return { valid: errors.length === 0, errors };
}
