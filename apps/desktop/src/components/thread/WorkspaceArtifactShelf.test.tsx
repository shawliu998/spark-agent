import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { WorkspaceArtifactShelf } from "./WorkspaceArtifactShelf";

describe("WorkspaceArtifactShelf", () => {
  it("renders discovered Python outputs and opens them through the existing inspector boundary", () => {
    const onOpen = vi.fn();
    render(
      <WorkspaceArtifactShelf
        artifacts={[
          {
            kind: "artifact",
            path: "results/summary.csv",
            filename: "summary.csv",
            artifact: "table",
            tool: "workspace",
          },
          {
            kind: "artifact",
            path: "results/figure.png",
            filename: "figure.png",
            artifact: "figure",
            tool: "workspace",
          },
        ]}
        onOpen={onOpen}
        onRefresh={vi.fn()}
      />,
    );

    expect(screen.getByText("summary.csv")).toBeInTheDocument();
    expect(screen.getByText("figure.png")).toBeInTheDocument();
    fireEvent.click(screen.getByText("figure.png"));
    expect(onOpen).toHaveBeenCalledWith(
      expect.objectContaining({ path: "results/figure.png", artifact: "figure" }),
    );
  });
});
