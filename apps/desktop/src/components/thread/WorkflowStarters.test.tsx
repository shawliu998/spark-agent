import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { WORKFLOW_STARTERS, WorkflowStarters } from "./WorkflowStarters";

// Plain closures instead of vi.fn: tinyspy's result tracking derives an extra
// promise from a rejecting spy, which vitest then reports as unhandled.
const installCalls: string[] = [];
const connectorCalls: string[] = [];
const toastErrors: string[] = [];
let failInstall = false;
let failConnector = false;
vi.mock("@/lib/tauri", () => ({
  isTauri: true,
  installExample: async (name: string) => {
    installCalls.push(name);
    if (failInstall) throw new Error("resource missing");
    return name;
  },
}));
vi.mock("@/lib/setup", () => ({
  ensureScienceConnector: async (id: string) => {
    connectorCalls.push(id);
    if (failConnector) throw new Error("connector unavailable");
  },
}));
vi.mock("@/lib/toast", () => ({
  toast: {
    error: (message: string) => toastErrors.push(message),
  },
}));

describe("WorkflowStarters", () => {
  beforeEach(() => {
    installCalls.length = 0;
    connectorCalls.length = 0;
    toastErrors.length = 0;
    failInstall = false;
    failConnector = false;
  });

  it("renders one card per starter workflow, including the climate example", () => {
    render(<WorkflowStarters onPick={() => {}} />);
    // Titles are i18n-translated (session:starters.<id>.title); WORKFLOW_STARTERS
    // itself no longer carries display copy, only ids/prompts — assert the
    // rendered English text directly.
    expect(screen.getByText("Run a demo analysis, end to end")).toBeInTheDocument();
    expect(screen.getByText("Analyze my data")).toBeInTheDocument();
    expect(screen.getByText("Audit a report for traceability")).toBeInTheDocument();
    expect(screen.getByText("Connect papers and data")).toBeInTheDocument();
    expect(screen.getByText("Explore an example: climate trends")).toBeInTheDocument();
    expect(WORKFLOW_STARTERS).toHaveLength(5);
  });

  it("ensures literature search before sending the papers-and-data prompt", async () => {
    const onPick = vi.fn();
    render(<WorkflowStarters onPick={onPick} />);

    await userEvent.click(screen.getByText("Connect papers and data"));

    await waitFor(() => expect(onPick).toHaveBeenCalledTimes(1));
    expect(connectorCalls).toEqual(["paper-search"]);
    for (const path of [
      "references/corpus.csv",
      "references/references.bib",
      "scripts/papers_data_analysis.py",
      "tables/papers_data_summary.csv",
      "figures/papers_data_analysis.png",
      "reports/papers-data-synthesis.md",
    ]) {
      expect(onPick.mock.calls[0][0]).toContain(path);
    }
  });

  it("does not send the papers-and-data prompt when connector setup fails", async () => {
    failConnector = true;
    const onPick = vi.fn();
    render(<WorkflowStarters onPick={onPick} />);

    await userEvent.click(screen.getByText("Connect papers and data"));

    await waitFor(() => expect(connectorCalls).toEqual(["paper-search"]));
    expect(onPick).not.toHaveBeenCalled();
    expect(toastErrors.join(" ")).toContain("connector unavailable");
  });

  it("sends the full-workflow prompt on click", async () => {
    const onPick = vi.fn();
    render(<WorkflowStarters onPick={onPick} />);
    await userEvent.click(screen.getByText("Run a demo analysis, end to end"));
    await waitFor(() => expect(onPick).toHaveBeenCalledWith(expect.stringContaining("figure1.png")));
    expect(onPick.mock.calls[0][0]).toContain("report.md");
    expect(installCalls).toHaveLength(0);
  });

  it("installs the example files before sending the climate prompt", async () => {
    const onPick = vi.fn();
    render(<WorkflowStarters onPick={onPick} />);

    await userEvent.click(screen.getByText("Explore an example: climate trends"));
    await waitFor(() => expect(onPick).toHaveBeenCalledTimes(1));
    expect(installCalls).toEqual(["climate-trends"]);
    expect(onPick.mock.calls[0][0]).toContain("gistemp_global_means.csv");
  });

  it("does not send the prompt when the example install fails", async () => {
    failInstall = true;
    const onPick = vi.fn();
    render(<WorkflowStarters onPick={onPick} />);

    await userEvent.click(screen.getByText("Explore an example: climate trends"));
    await waitFor(() => expect(installCalls).toHaveLength(1));
    expect(onPick).not.toHaveBeenCalled();
  });
});
