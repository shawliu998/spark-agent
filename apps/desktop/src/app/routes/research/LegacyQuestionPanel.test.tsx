import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LegacyQuestionPanel } from "./LegacyQuestionPanel";

describe("LegacyQuestionPanel", () => {
  it("fails closed when the approved remote destination is unavailable", () => {
    render(
      <LegacyQuestionPanel
        question="What changed?"
        approved
        asking={false}
        answer={null}
        projectReady
        literatureReady
        remoteDestination={null}
        sources={[]}
        readySourceCount={1}
        selection={null}
        onQuestionChange={vi.fn()}
        onApprovalChange={vi.fn()}
        onSubmit={vi.fn()}
        onSelectEvidence={vi.fn()}
      />,
    );

    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ask library" })).toBeDisabled();
  });

  it("describes PaperQA evidence as a local quote match, not claim verification", () => {
    render(
      <LegacyQuestionPanel
        question="What changed?"
        approved
        asking={false}
        answer={{
          id: "answer-1",
          projectId: "project-1",
          question: "What changed?",
          answer: "The remote model generated this answer.",
          claims: [
            {
              id: "claim-1",
              statement: "The remote model generated this answer.",
              claimType: "answer",
              confidence: 0.99,
              reviewStatus: "verified",
              evidence: [
                {
                  id: "evidence-1",
                  sourceId: "source-1",
                  pageIndex: 0,
                  pageLabel: "1",
                  text: "A locally matched quotation.",
                  bbox: null,
                  coordinateSpace: "normalized-rotated-top-left-v1",
                  quoteHash: "a".repeat(64),
                  extractionMethod: "paperqa2-local-citation-v1",
                  confidence: 1,
                  verified: true,
                },
              ],
            },
          ],
          unresolvedQuestions: [],
          generator: "paperqa2-remote-v1",
          model: "provider/model-1",
          promptVersion: null,
          metadata: {
            generationMode: "remote-model-assisted",
            endpointHost: "models.example.test",
            endpointIdentity: `sha256:${"e".repeat(64)}`,
          },
          createdAt: "2026-07-14T08:00:00Z",
        }}
        projectReady
        literatureReady
        remoteDestination={{
          provider: "openai-compatible",
          endpointHost: "models.example.test",
          endpointIdentity: `sha256:${"e".repeat(64)}`,
          model: "provider/model-1",
        }}
        sources={[
          {
            id: "source-1",
            projectId: "project-1",
            title: "Imported paper",
            sourceKind: "pdf",
            authors: [],
            doi: null,
            arxivId: null,
            localPath: "/tmp/imported.pdf",
            publicationDate: null,
            ingestionStatus: "ready",
            contentHash: "b".repeat(64),
            pageCount: 1,
            createdAt: "2026-07-14T08:00:00Z",
          },
        ]}
        readySourceCount={1}
        selection={null}
        onQuestionChange={vi.fn()}
        onApprovalChange={vi.fn()}
        onSubmit={vi.fn()}
        onSelectEvidence={vi.fn()}
      />,
    );

    expect(
      screen.getByText("Remote answer with locally matched passages"),
    ).toBeInTheDocument();
    expect(screen.getByText(/confirms only that quoted text occurs/i)).toBeInTheDocument();
    expect(screen.getByText("Not reviewed for claim support")).toBeInTheDocument();
    expect(screen.getByText("Located locally")).toBeInTheDocument();
    expect(screen.getByText("paperqa2-remote-v1")).toBeInTheDocument();
    expect(screen.getByText("models.example.test")).toBeInTheDocument();
    expect(screen.getByText(`sha256:${"e".repeat(64)}`)).toBeInTheDocument();
    expect(screen.queryByText(/99% confidence/i)).not.toBeInTheDocument();
  });
});
