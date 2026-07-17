import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TaskPlanComposer } from "./TaskPlanComposer";

describe("TaskPlanComposer", () => {
  it("generates an editable plan and launches its edited tasks", async () => {
    const user = userEvent.setup();
    const generate = vi.fn().mockResolvedValue([
      { title: "Collect sources", prompt: "Find primary sources." },
      { title: "Analyze evidence", prompt: "Compare the findings." },
    ]);
    const onLaunch = vi.fn();
    render(<TaskPlanComposer generate={generate} onLaunch={onLaunch} />);

    await user.type(screen.getByLabelText("Objective"), "Assess battery recycling");
    await user.click(screen.getByRole("button", { name: "Generate" }));
    await waitFor(() => expect(generate).toHaveBeenCalledWith("Assess battery recycling"));
    expect(screen.getByDisplayValue("Collect sources")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Task 2 title"), { target: { value: "Review evidence" } });
    await user.click(screen.getByRole("button", { name: "Launch tasks" }));
    expect(onLaunch).toHaveBeenCalledWith(
      "Assess battery recycling",
      expect.arrayContaining([
        expect.objectContaining({ title: "Collect sources", prompt: "Find primary sources." }),
        expect.objectContaining({ title: "Review evidence", prompt: "Compare the findings." }),
      ]),
    );
  });

  it("keeps between two and five tasks", async () => {
    const user = userEvent.setup();
    render(<TaskPlanComposer generate={vi.fn()} onLaunch={vi.fn()} />);

    expect(screen.getAllByLabelText(/Task \d+ title/)).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: /Remove task/ })[0]).toBeDisabled();
    for (let index = 0; index < 4; index += 1) await user.click(screen.getByRole("button", { name: "Add task" }));
    expect(screen.getAllByLabelText(/Task \d+ title/)).toHaveLength(5);
    expect(screen.getByRole("button", { name: "Add task" })).toBeDisabled();
  });

  it("shows generating status while a plan is pending", async () => {
    let resolve!: (tasks: { title: string; prompt: string }[]) => void;
    const generate = vi.fn(() => new Promise<{ title: string; prompt: string }[]>((done) => { resolve = done; }));
    render(<TaskPlanComposer generate={generate} onLaunch={vi.fn()} initialObjective="Map the field" />);

    fireEvent.click(screen.getByRole("button", { name: "Generate" }));
    expect(screen.getByRole("button", { name: "Generating…" })).toBeDisabled();
    expect(screen.getByLabelText("Objective")).toBeDisabled();
    resolve([{ title: "Search", prompt: "Search sources" }, { title: "Synthesize", prompt: "Write summary" }]);
    await waitFor(() => expect(screen.getByRole("button", { name: "Generate" })).toBeEnabled());
  });
});
