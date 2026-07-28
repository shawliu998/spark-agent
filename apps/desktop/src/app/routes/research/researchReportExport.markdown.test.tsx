import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ResearchSource, ResearchWorkflowResult } from "@spark/research-domain";
import { MarkdownViewer } from "@/components/markdown-viewer/MarkdownViewer";
import { buildVerifiedReportExport } from "./researchReportExport";

describe("research report Markdown rendering", () => {
  it("preserves injected-looking text without creating an ordered list or Setext heading", () => {
    const contentHash = "a".repeat(64);
    const source: ResearchSource = {
      id: "source-1", projectId: "project-1", title: "Paper", sourceKind: "pdf",
      authors: [], doi: null, arxivId: null, localPath: "sources/paper.pdf",
      publicationDate: null, ingestionStatus: "ready", contentHash, pageCount: 1,
      createdAt: "2026-07-22T00:00:00Z",
    };
    const result: ResearchWorkflowResult = {
      answerId: "answer-1",
      summary: "1. injected item\n===",
      generator: "local-deterministic",
      model: null,
      promptVersion: null,
      integrityStatus: "verified-frozen-v2",
      claims: [{
        id: "claim-1",
        statement: "Visible claim.",
        supportStatus: "supported",
        confidence: 1,
        evidence: [{
          evidenceId: "evidence-1", sourceId: source.id, sourceTitle: source.title,
          sourceContentHash: contentHash, sourcePageManifestHash: "b".repeat(64),
          pageIndex: 0, pageLabel: "1", text: "Verified quote", bbox: null,
          coordinateSpace: "normalized-rotated-top-left-v1", quoteHash: "c".repeat(64),
          extractionMethod: "text-layer-exact-v1", confidence: 1, verified: true,
          relationship: "supporting",
        }],
      }],
      unresolvedQuestions: [],
    };
    const exported = buildVerifiedReportExport("completed", result, [source]);
    expect(exported.ok).toBe(true);
    if (!exported.ok) return;

    render(<MarkdownViewer>{exported.markdown}</MarkdownViewer>);
    const injectedText = screen.getByText(/1\. injected item/);
    expect(injectedText.tagName).toBe("P");
    expect(injectedText).toHaveTextContent("1. injected item ===");
    expect(injectedText.closest("ol")).toBeNull();
    expect(screen.queryByRole("heading", { name: "1. injected item" })).not.toBeInTheDocument();
  });

  it("turns only allowed prose citations into interactive report controls", () => {
    const onCitationClick = vi.fn();
    render(
      <MarkdownViewer
        variant="document"
        citationIndices={[1]}
        onCitationClick={onCitationClick}
        citationAriaLabel={(index) => `Open citation ${index}`}
      >
        {`A supported finding [1] and an unavailable citation [2].

\`literal [1]\`

1. Paper, page 3 <!-- [@evidence:evidence-1:${"a".repeat(64)}] -->`}
      </MarkdownViewer>,
    );

    const citation = screen.getByRole("button", { name: "Open citation 1" });
    fireEvent.click(citation);
    expect(onCitationClick).toHaveBeenCalledWith(1, citation);
    expect(screen.getByText(/unavailable citation \[2\]/)).toBeInTheDocument();
    expect(within(screen.getByText("literal [1]")).queryByRole("button")).toBeNull();
    expect(screen.queryByText(/@evidence:/)).toBeNull();
    expect(screen.getByText("Paper, page 3")).toBeInTheDocument();
  });

  it("keeps bracketed numbers as plain text outside report citation mode", () => {
    render(<MarkdownViewer>Plain citation [1].</MarkdownViewer>);
    expect(screen.queryByRole("button", { name: /citation/i })).toBeNull();
    expect(screen.getByText("Plain citation [1].")).toBeInTheDocument();
  });
});
