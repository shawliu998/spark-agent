import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SessionMeta } from "@ai4s/sdk";
import { ParallelTaskCenter } from "./ParallelTaskCenter";

const sessions: SessionMeta[] = [
  { id: "ses_waiting", title: "Collect papers" },
  { id: "ses_running", title: "Analyze results" },
  { id: "ses_done", title: "Review draft" },
];

describe("ParallelTaskCenter", () => {
  it("shows task status and the latest routed model", () => {
    render(
      <ParallelTaskCenter
        sessions={sessions}
        currentId="ses_running"
        runningSessions={{ ses_running: true }}
        waitingSessions={{ ses_waiting: true }}
        sessionModels={{
          ses_waiting: "openai/gpt-5.6-luna",
          ses_running: "openai/gpt-5.6-terra",
          ses_done: "openai/gpt-5.6-sol",
        }}
        onOpen={vi.fn()}
        onNew={vi.fn()}
      />,
    );

    expect(screen.getByRole("region", { name: "Parallel tasks" })).toBeInTheDocument();
    expect(screen.getByText("Analyze results")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Waiting")).toBeInTheDocument();
    expect(screen.getByText("Complete")).toBeInTheDocument();
    expect(screen.getByText("openai/gpt-5.6-luna")).toBeInTheDocument();
    expect(screen.getByText("openai/gpt-5.6-terra")).toBeInTheDocument();
    expect(screen.getByText("openai/gpt-5.6-sol")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /analyze results/i })).toHaveAttribute("aria-current", "page");
  });

  it("opens tasks and starts a new draft through callbacks", () => {
    const onOpen = vi.fn();
    const onNew = vi.fn();
    render(
      <ParallelTaskCenter
        sessions={sessions}
        currentId={null}
        runningSessions={{}}
        onOpen={onOpen}
        onNew={onNew}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /review draft/i }));
    fireEvent.click(screen.getByRole("button", { name: "New task" }));

    expect(onOpen).toHaveBeenCalledWith("ses_done");
    expect(onNew).toHaveBeenCalledOnce();
    expect(screen.getAllByText("Runtime default")).toHaveLength(3);
  });

  it("renders an explicit empty state", () => {
    render(
      <ParallelTaskCenter sessions={[]} currentId={null} runningSessions={{}} onOpen={vi.fn()} onNew={vi.fn()} />,
    );

    expect(screen.getByText("No research tasks yet.")).toBeInTheDocument();
  });
});
