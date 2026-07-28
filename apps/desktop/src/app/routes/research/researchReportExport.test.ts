import { describe, expect, it } from "vitest";
import type { ResearchSource, ResearchWorkflowResult } from "@spark/research-domain";
import { buildVerifiedReportExport, serializeCitationText } from "./researchReportExport";

const CONTENT_HASH = "a".repeat(64);
const MANIFEST_HASH = "b".repeat(64);

function source(overrides: Partial<ResearchSource> = {}): ResearchSource {
  return {
    id: "source-1",
    projectId: "project-1",
    title: "Measured outcomes in adults",
    sourceKind: "pdf",
    authors: ["Ada Researcher", "Lin Scholar"],
    doi: "10.1000/example",
    arxivId: null,
    localPath: "sources/paper.pdf",
    publicationDate: "2024-02-01",
    ingestionStatus: "ready",
    contentHash: CONTENT_HASH,
    pageCount: 12,
    createdAt: "2026-07-21T08:00:00Z",
    ...overrides,
  };
}

function result(overrides: Partial<ResearchWorkflowResult> = {}): ResearchWorkflowResult {
  const evidence = {
    evidenceId: "evidence-1",
    sourceId: "source-1",
    sourceTitle: "Measured outcomes in adults",
    sourceContentHash: CONTENT_HASH,
    sourcePageManifestHash: MANIFEST_HASH,
    pageIndex: 2,
    pageLabel: "3",
    text: "The measured outcome improved after the intervention.",
    bbox: null,
    coordinateSpace: "normalized-rotated-top-left-v1" as const,
    quoteHash: "c".repeat(64),
    extractionMethod: "text-layer-exact-v1",
    confidence: 1,
    verified: true,
    relationship: "supporting" as const,
  };
  return {
    answerId: "answer-1",
    summary: "The intervention improved the measured outcome.",
    generator: "local-deterministic",
    model: null,
    promptVersion: null,
    integrityStatus: "verified-frozen-v2",
    claims: [
      { id: "claim-1", statement: "The outcome improved.", supportStatus: "supported", confidence: 1, evidence: [evidence] },
      { id: "claim-2", statement: "The finding was measured locally.", supportStatus: "supported", confidence: 1, evidence: [{ ...evidence, evidenceId: "duplicate-id" }] },
    ],
    unresolvedQuestions: ["Does the effect persist?"],
    ...overrides,
  };
}

describe("verified research report export", () => {
  it("serializes a completed frozen report and stably deduplicates repeated citations", () => {
    const exported = buildVerifiedReportExport("completed", result(), [source()]);
    expect(exported.ok).toBe(true);
    if (!exported.ok) return;

    expect(exported.citations).toHaveLength(1);
    expect(exported.markdown).toContain("# Research synthesis");
    expect(exported.markdown).toContain("The outcome improved\\. [1]");
    expect(exported.markdown).toContain("The finding was measured locally\\. [1]");
    expect(exported.citationsCsv).toContain('"10.1000/example"');
    expect(exported.citationsJson).toContain(`"sourceContentHash": "${CONTENT_HASH}"`);
    expect(exported.citationsBibtex).toContain("author = {Ada Researcher and Lin Scholar}");
    expect(exported.citationsBibtex).toContain("doi = {10.1000/example}");
    expect(exported.citationsBibtex.match(/@misc\{/g)).toHaveLength(1);
  });

  it("fails closed for incomplete, unfrozen, changed, and unverified evidence", () => {
    expect(buildVerifiedReportExport("running", result(), [source()])).toEqual({ ok: false, reason: "workflow-not-completed" });
    expect(buildVerifiedReportExport("completed", result({ integrityStatus: "unfrozen" }), [source()])).toEqual({ ok: false, reason: "result-not-frozen" });
    expect(buildVerifiedReportExport("completed", result(), [source({ contentHash: "d".repeat(64) })])).toEqual({ ok: false, reason: "source-changed" });
    const unverified = result();
    unverified.claims[0].evidence[0].verified = false;
    expect(buildVerifiedReportExport("completed", unverified, [source()])).toEqual({ ok: false, reason: "evidence-needs-review" });
  });

  it("does not invent missing authors, dates, or DOI metadata", () => {
    const exported = buildVerifiedReportExport("completed", result(), [source({ authors: [], publicationDate: null, doi: null })]);
    expect(exported.ok).toBe(true);
    if (!exported.ok) return;

    expect(exported.citationsBibtex).not.toContain("author =");
    expect(exported.citationsBibtex).not.toContain("date =");
    expect(exported.citationsBibtex).not.toContain("doi =");
    expect(serializeCitationText(exported.citations[0])).toBe(
      "Measured outcomes in adults\nPage 3\n“The measured outcome improved after the intervention.”",
    );
  });

  it("neutralizes spreadsheet formula prefixes in every exported string cell", () => {
    const dangerous = result();
    dangerous.claims[0].statement = "-malicious claim";
    dangerous.claims[0].evidence[0].text = "@malicious quote";
    dangerous.claims.forEach((claim) => claim.evidence.forEach((evidence) => {
      evidence.sourceTitle = "=malicious title";
    }));
    const exported = buildVerifiedReportExport("completed", dangerous, [source({
      title: "=malicious title",
      authors: ["+malicious author"],
    })]);
    expect(exported.ok).toBe(true);
    if (!exported.ok) return;

    expect(exported.citationsCsv).toContain('"\'-malicious claim"');
    expect(exported.citationsCsv).toContain('"\'=malicious title"');
    expect(exported.citationsCsv).toContain('"\'+malicious author"');
    expect(exported.citationsCsv).toContain('"\'@malicious quote"');
  });

  it("escapes all TeX-special metadata without recursively corrupting escape sequences", () => {
    const special = result();
    special.claims.forEach((claim) => claim.evidence.forEach((evidence) => {
      evidence.sourceTitle = "Path \\ {x} 50% #1 $5 & A_B ~ ^";
    }));
    const exported = buildVerifiedReportExport("completed", special, [source({
      title: "Path \\ {x} 50% #1 $5 & A_B ~ ^",
      authors: ["A&B_Researcher"],
      doi: "10.1000/a_b%2",
    })]);
    expect(exported.ok).toBe(true);
    if (!exported.ok) return;

    expect(exported.citationsBibtex).toContain(
      "title = {Path \\textbackslash{} \\{x\\} 50\\% \\#1 \\$5 \\& A\\_B \\textasciitilde{} \\textasciicircum{}}",
    );
    expect(exported.citationsBibtex).toContain("author = {A\\&B\\_Researcher}");
    expect(exported.citationsBibtex).toContain("doi = {10.1000/a\\_b\\%2}");
    expect(exported.citationsBibtex).not.toContain("textbackslash\\{\\}");
  });

  it("escapes Markdown structure in summaries, claims, questions, and source titles", () => {
    const malicious = result({
      summary: "# injected heading\n![remote](https://example.test/a.png)",
      unresolvedQuestions: ["- [link](https://example.test)"],
    });
    malicious.claims[0].statement = "[claim](https://example.test)\n## injected";
    malicious.claims.forEach((claim) => claim.evidence.forEach((evidence) => {
      evidence.sourceTitle = "![source](https://example.test/source.png)";
    }));
    const exported = buildVerifiedReportExport("completed", malicious, [source({
      title: "![source](https://example.test/source.png)",
    })]);
    expect(exported.ok).toBe(true);
    if (!exported.ok) return;

    expect(exported.markdown).toContain("\\# injected heading");
    expect(exported.markdown).toContain("\\!\\[remote\\]\\(https://example\\.test/a\\.png\\)");
    expect(exported.markdown).toContain("- \\[claim\\]\\(https://example\\.test\\) \\#\\# injected [1]");
    expect(exported.markdown).toContain("- \\- \\[link\\]\\(https://example\\.test\\)");
    expect(exported.markdown).toContain("\\!\\[source\\]\\(https://example\\.test/source\\.png\\)");
    expect(exported.markdown).not.toContain("\n# injected heading");
    expect(exported.markdown).not.toContain("![remote]");
  });

  it("blocks ordered-list and Setext heading injection while preserving visible text", () => {
    const injected = result({
      summary: "1. injected item\n===",
      unresolvedQuestions: ["2. another item", "---"],
    });
    injected.claims[0].statement = "3. claim\n=== heading";
    const exported = buildVerifiedReportExport("completed", injected, [source()]);
    expect(exported.ok).toBe(true);
    if (!exported.ok) return;

    expect(exported.markdown).toContain("1\\. injected item\n\\=\\=\\=");
    expect(exported.markdown).toContain("- 3\\. claim \\=\\=\\= heading [1]");
    expect(exported.markdown).toContain("- 2\\. another item");
    expect(exported.markdown).toContain("- \\-\\-\\-");
    expect(exported.markdown).not.toContain("\n1. injected item");
    expect(exported.markdown).not.toContain("\n===");
  });
});
