import { describe, expect, it } from "vitest";
import { createTaskPlan, generateTaskPlan, validateTaskPlan } from "./taskPlanning";

describe("task planning", () => {
  it("creates three deterministic, independent research tasks", () => {
    const first = createTaskPlan("  Evaluate a new battery material  ");
    const second = generateTaskPlan("Evaluate a new battery material");

    expect(first).toEqual(second);
    expect(first.goal).toBe("Evaluate a new battery material");
    expect(first.tasks.map((task) => task.id)).toEqual(["evidence", "execution", "critical-review"]);
    expect(first.tasks.map((task) => task.kind)).toEqual(["evidence", "execution", "critical-review"]);
    expect(first.tasks.every((task) => task.prompt.includes("Do not depend on other tasks."))).toBe(true);
  });

  it("accepts an editable plan with two to five complete tasks", () => {
    const tasks = createTaskPlan("Measure reproducibility").tasks;

    expect(validateTaskPlan(tasks)).toEqual({ valid: true, errors: [] });
    expect(validateTaskPlan(tasks.slice(0, 2)).valid).toBe(true);
    expect(validateTaskPlan([...tasks, { ...tasks[0], id: "four" }, { ...tasks[1], id: "five" }]).valid).toBe(true);
  });

  it("rejects task counts outside the supported range and incomplete edits", () => {
    const tasks = createTaskPlan("Measure reproducibility").tasks;

    expect(validateTaskPlan(tasks.slice(0, 1)).valid).toBe(false);
    expect(validateTaskPlan([...tasks, ...tasks]).valid).toBe(false);
    expect(validateTaskPlan([{ ...tasks[0], title: "", prompt: "" }, tasks[1]]).errors).toContain('Task "evidence" needs a title.');
  });
});
