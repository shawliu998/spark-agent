import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LabNotebookPage } from "./LabNotebookPage";

const readArtifact = vi.fn();
vi.mock("@/lib/artifactFile", () => ({ readArtifact: (...args: unknown[]) => readArtifact(...args) }));

describe("LabNotebookPage", () => {
  beforeEach(() => readArtifact.mockResolvedValue({ encoding: "utf8", data: [
    { version: 1, id: "a", timestamp: "2026-07-17T06:00:00.000Z", type: "decision", content: "Use the preregistered outcome.", sessionId: "ses_a" },
    { version: 1, id: "b", timestamp: "2026-07-17T07:00:00.000Z", type: "limitation", content: "The sample is small.", sessionId: "ses_b" },
  ].map(JSON.stringify).join("\n") + "\n" }));
  it("renders recoverable notebook records and scoped filters", async () => {
    render(<LabNotebookPage />);
    expect(await screen.findByText("Use the preregistered outcome.")).toBeInTheDocument();
    expect(screen.getByLabelText("Type")).toBeInTheDocument();
    expect(screen.getByLabelText("Session")).toBeInTheDocument();
    expect(readArtifact).toHaveBeenCalledWith(".spark/lab-notebook.jsonl", "workspace");
  });

  it("filters entries by type and originating session", async () => {
    render(<LabNotebookPage />);
    await screen.findByText("The sample is small.");
    await userEvent.selectOptions(screen.getByLabelText("Type"), "decision");
    expect(screen.getByText("Use the preregistered outcome.")).toBeInTheDocument();
    expect(screen.queryByText("The sample is small.")).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Session"), "ses_b");
    expect(screen.getByText("No notebook entries match these filters.")).toBeInTheDocument();
  });
});
