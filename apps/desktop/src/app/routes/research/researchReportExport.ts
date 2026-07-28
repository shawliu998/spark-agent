import type {
  ResearchSource,
  ResearchWorkflowResult,
  ResearchWorkflowStatus,
  WorkflowEvidenceRelationship,
} from "@spark/research-domain";

export type ReportExportBlockReason =
  | "workflow-not-completed"
  | "result-not-frozen"
  | "no-verified-evidence"
  | "source-changed"
  | "evidence-needs-review";

export interface VerifiedCitationExport {
  index: number;
  canonicalEvidenceKey: string;
  evidenceId: string;
  claim: string;
  relationship: "supporting" | "contradicting";
  sourceId: string;
  sourceTitle: string;
  authors: string[];
  publicationDate: string | null;
  doi: string | null;
  arxivId: string | null;
  pageIndex: number;
  pageLabel: string | null;
  quote: string;
  quoteHash: string;
  extractionMethod: string;
  sourceContentHash: string;
  sourcePageManifestHash: string;
}

export type VerifiedReportExport =
  | {
      ok: true;
      markdown: string;
      citations: VerifiedCitationExport[];
      citationsCsv: string;
      citationsJson: string;
      citationsBibtex: string;
    }
  | { ok: false; reason: ReportExportBlockReason };

export function canonicalEvidenceKey(evidence: Pick<WorkflowEvidenceRelationship,
  | "sourceId"
  | "sourceContentHash"
  | "sourcePageManifestHash"
  | "pageIndex"
  | "quoteHash"
  | "text"
  | "relationship"
>): string {
  return JSON.stringify([
    evidence.sourceId,
    evidence.sourceContentHash,
    evidence.sourcePageManifestHash,
    evidence.pageIndex,
    evidence.quoteHash,
    evidence.text,
    evidence.relationship,
  ]);
}

function csvCell(value: string | number | null): string {
  const raw = value == null ? "" : String(value);
  const text = typeof value === "string" && /^[=+\-@\t\r]/.test(raw) ? `'${raw}` : raw;
  return `"${text.replace(/"/g, '""')}"`;
}

function bibtexValue(value: string): string {
  const escapes: Record<string, string> = {
    "\\": "\\textbackslash{}",
    "{": "\\{",
    "}": "\\}",
    "%": "\\%",
    "#": "\\#",
    "$": "\\$",
    "&": "\\&",
    "_": "\\_",
    "~": "\\textasciitilde{}",
    "^": "\\textasciicircum{}",
  };
  return [...value].map((character) => escapes[character] ?? character).join("");
}

function bibtexKey(sourceId: string, index: number): string {
  const normalized = sourceId.replace(/[^A-Za-z0-9_-]/g, "_").replace(/^_+|_+$/g, "");
  return `spark_${normalized || index}`;
}

function citationPage(citation: VerifiedCitationExport): string {
  return citation.pageLabel ?? String(citation.pageIndex + 1);
}

function markdownText(value: string): string {
  const escapes: Record<string, string> = {
    "\\": "\\\\",
    "`": "\\`",
    "*": "\\*",
    "_": "\\_",
    "{": "\\{",
    "}": "\\}",
    "[": "\\[",
    "]": "\\]",
    "<": "\\<",
    ">": "\\>",
    "(": "\\(",
    ")": "\\)",
    "#": "\\#",
    "+": "\\+",
    "-": "\\-",
    "!": "\\!",
    "|": "\\|",
    ".": "\\.",
    "=": "\\=",
  };
  return [...value.replace(/\r\n?/g, "\n")]
    .map((character) => escapes[character] ?? character)
    .join("");
}

function markdownInline(value: string): string {
  return markdownText(value).replace(/\s+/g, " ").trim();
}

export function serializeCitationText(citation: VerifiedCitationExport): string {
  const metadata = [
    citation.authors.length > 0 ? citation.authors.join(", ") : null,
    citation.publicationDate,
    citation.doi ? `DOI: ${citation.doi}` : null,
  ].filter((value): value is string => Boolean(value));
  return [
    citation.sourceTitle,
    metadata.join(" · "),
    `Page ${citationPage(citation)}`,
    `“${citation.quote}”`,
  ].filter(Boolean).join("\n");
}

export function buildVerifiedReportExport(
  status: ResearchWorkflowStatus | null,
  result: ResearchWorkflowResult | null,
  sources: readonly ResearchSource[],
  reviewPassed = true,
): VerifiedReportExport {
  if (status !== "completed" || !reviewPassed) return { ok: false, reason: "workflow-not-completed" };
  if (!result || result.integrityStatus !== "verified-frozen-v2") {
    return { ok: false, reason: "result-not-frozen" };
  }

  const allEvidence = result.claims.flatMap((claim) =>
    claim.evidence.map((evidence) => ({ claim: claim.statement, evidence })),
  );
  if (allEvidence.length === 0 || result.claims.some((claim) => claim.evidence.length === 0)) {
    return { ok: false, reason: "no-verified-evidence" };
  }

  for (const { evidence } of allEvidence) {
    const source = sources.find((candidate) => candidate.id === evidence.sourceId);
    if (source?.contentHash && evidence.sourceContentHash && source.contentHash !== evidence.sourceContentHash) {
      return { ok: false, reason: "source-changed" };
    }
    if (
      !evidence.verified ||
      !evidence.sourceContentHash ||
      !evidence.sourcePageManifestHash ||
      source?.ingestionStatus !== "ready" ||
      source.contentHash !== evidence.sourceContentHash
    ) {
      return { ok: false, reason: "evidence-needs-review" };
    }
  }

  const citations: VerifiedCitationExport[] = [];
  const citationIndexes = new Map<string, number>();
  for (const { claim, evidence } of allEvidence) {
    const key = canonicalEvidenceKey(evidence);
    if (citationIndexes.has(key)) continue;
    const source = sources.find((candidate) => candidate.id === evidence.sourceId)!;
    const index = citations.length + 1;
    citationIndexes.set(key, index);
    citations.push({
      index,
      canonicalEvidenceKey: key,
      evidenceId: evidence.evidenceId,
      claim,
      relationship: evidence.relationship,
      sourceId: source.id,
      sourceTitle: evidence.sourceTitle ?? source.title,
      authors: [...source.authors],
      publicationDate: source.publicationDate,
      doi: source.doi,
      arxivId: source.arxivId,
      pageIndex: evidence.pageIndex,
      pageLabel: evidence.pageLabel,
      quote: evidence.text,
      quoteHash: evidence.quoteHash,
      extractionMethod: evidence.extractionMethod,
      sourceContentHash: evidence.sourceContentHash!,
      sourcePageManifestHash: evidence.sourcePageManifestHash!,
    });
  }

  const findingLines = result.claims.map((claim) => {
    const indexes = [...new Set(claim.evidence.map((evidence) => citationIndexes.get(canonicalEvidenceKey(evidence))!))];
    return `- ${markdownInline(claim.statement)} ${indexes.map((index) => `[${index}]`).join(" ")}`;
  });
  const referenceLines = citations.map((citation) => {
    const parts = [
      `${citation.index}. ${markdownInline(citation.sourceTitle)}`,
      citation.authors.length > 0 ? markdownInline(citation.authors.join(", ")) : null,
      citation.publicationDate ? markdownInline(citation.publicationDate) : null,
      `p. ${markdownInline(citationPage(citation))}`,
      citation.doi ? `DOI: ${markdownInline(citation.doi)}` : null,
    ].filter((value): value is string => Boolean(value));
    return parts.join(". ");
  });
  const unresolved = result.unresolvedQuestions.length > 0
    ? `\n\n## Unresolved questions\n\n${result.unresolvedQuestions.map((item) => `- ${markdownInline(item)}`).join("\n")}`
    : "";
  const markdown = `# Research synthesis\n\n${markdownText(result.summary)}\n\n## Findings\n\n${findingLines.join("\n")}${unresolved}\n\n## References\n\n${referenceLines.join("\n")}\n`;

  const csvHeaders = [
    "index", "evidence_id", "claim", "relationship", "source_id", "source_title",
    "authors", "publication_date", "doi", "arxiv_id", "page", "quote", "quote_hash",
    "extraction_method", "source_content_hash", "source_page_manifest_hash",
  ];
  const citationsCsv = [
    csvHeaders.map(csvCell).join(","),
    ...citations.map((citation) => [
      citation.index, citation.evidenceId, citation.claim, citation.relationship,
      citation.sourceId, citation.sourceTitle, citation.authors.join("; "),
      citation.publicationDate, citation.doi, citation.arxivId, citationPage(citation),
      citation.quote, citation.quoteHash, citation.extractionMethod,
      citation.sourceContentHash, citation.sourcePageManifestHash,
    ].map(csvCell).join(",")),
  ].join("\n");
  const citationsJson = `${JSON.stringify(citations, null, 2)}\n`;
  const bibtexSources = citations.filter(
    (citation, index, all) => all.findIndex((candidate) => candidate.sourceId === citation.sourceId) === index,
  );
  const citationsBibtex = bibtexSources.map((citation) => {
    const fields = [`  title = {${bibtexValue(citation.sourceTitle)}}`];
    if (citation.authors.length > 0) fields.push(`  author = {${bibtexValue(citation.authors.join(" and "))}}`);
    if (citation.publicationDate) fields.push(`  date = {${bibtexValue(citation.publicationDate)}}`);
    if (citation.doi) fields.push(`  doi = {${bibtexValue(citation.doi)}}`);
    return `@misc{${bibtexKey(citation.sourceId, citation.index)},\n${fields.join(",\n")}\n}`;
  }).join("\n\n") + "\n";

  return { ok: true, markdown, citations, citationsCsv, citationsJson, citationsBibtex };
}
