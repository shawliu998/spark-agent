import { screen, within } from "@testing-library/react";
import { describe, expect, it, beforeEach } from "vitest";
import { useUiStore } from "@/lib/store";
import { renderAt } from "@/test/render";

const base = "/example";

describe("SessionPage", () => {
  beforeEach(() => useUiStore.setState({ inspectorOpen: true }));

  it("renders the literature session with a reviewer finding and the PDF inspector", async () => {
    renderAt(`${base}/lit-review`);
    expect((await screen.findAllByText("Cross-species scRNA-seq Integration")).length).toBeGreaterThan(0);
    expect(screen.getByText(/PMID 31178118 assigned to both/)).toBeInTheDocument();
    const inspector = document.querySelector('[data-variant="pdf"]');
    expect(inspector).toBeInTheDocument();
    expect(within(inspector as HTMLElement).getByText("review.pdf")).toBeInTheDocument();
  });

  it("renders the sweep session with a data table and the notebook inspector", async () => {
    renderAt(`${base}/scvi-sweep`);
    expect((await screen.findAllByText("SCVI Hyperparameter Screen")).length).toBeGreaterThan(0);
    expect(screen.getByText("REMOTE · 8")).toBeInTheDocument();
    expect(document.querySelector('[data-variant="notebook"]')).toBeInTheDocument();
  });

  it("renders the figure session with the artifact inspector", async () => {
    renderAt(`${base}/figure-canvas`);
    await screen.findByText("Download script");
    expect(document.querySelector('[data-variant="artifact"]')).toBeInTheDocument();
    expect(screen.getByText("Download script")).toBeInTheDocument();
  });

  it("shows a not-found state for an unknown session", async () => {
    renderAt(`${base}/nope`);
    expect(await screen.findByText("Session not found")).toBeInTheDocument();
  });
});
