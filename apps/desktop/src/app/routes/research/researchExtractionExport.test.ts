import { describe, expect, it } from "vitest";
import type { ExtractionCell, ExtractionColumn, ExtractionMatrix } from "@spark/research-domain";
import { buildExtractionCsv, type ExtractionExportPaper } from "./researchExtractionExport";

const papers: ExtractionExportPaper[] = [
  { sourceId: "source-2", title: "Second paper", authors: "B. Researcher", publicationYear: "2021" },
  { sourceId: "source-1", title: "First paper", authors: "A. Researcher", publicationYear: "2020" },
];

function column(id: string, name: string): ExtractionColumn {
  return {
    id,
    projectId: "project-1",
    name,
    instructions: null,
    orderIndex: 0,
    rowVersion: 1,
    createdAt: "2026-07-25T00:00:00Z",
    updatedAt: "2026-07-25T00:00:00Z",
  };
}

function cell(overrides: Partial<ExtractionCell> = {}): ExtractionCell {
  return {
    id: "cell-1",
    projectId: "project-1",
    sourceId: "source-1",
    columnId: "method",
    value: "Randomized trial",
    reviewStatus: "unreviewed",
    evidenceIds: [],
    rowVersion: 1,
    createdAt: "2026-07-25T00:00:00Z",
    updatedAt: "2026-07-25T00:00:00Z",
    ...overrides,
  };
}

describe("buildExtractionCsv", () => {
  it("preserves paper and column order and fills missing cells explicitly", () => {
    const matrix: ExtractionMatrix = {
      columns: [column("method", "Method"), column("result", "Result")],
      cells: [cell()],
    };

    expect(buildExtractionCsv(papers, matrix)).toBe([
      '"source_id","title","authors","publication_year","Method [method] value","Method [method] review_status","Method [method] linked_evidence_count","Result [result] value","Result [result] review_status","Result [result] linked_evidence_count"',
      '"source-2","Second paper","B. Researcher","2021","","missing","0","","missing","0"',
      '"source-1","First paper","A. Researcher","2020","Randomized trial","unreviewed","0","","missing","0"',
    ].join("\r\n"));
  });

  it("exports unreviewed and confirmed cells without calling either verified", () => {
    const matrix: ExtractionMatrix = {
      columns: [column("method", "Method"), column("result", "Result")],
      cells: [
        cell({ evidenceIds: ["evidence-1"] }),
        cell({ id: "cell-2", columnId: "result", value: "Improved", reviewStatus: "confirmed", evidenceIds: ["evidence-2", "evidence-3"] }),
      ],
    };

    const csv = buildExtractionCsv([papers[1]], matrix);
    expect(csv).toContain('"Randomized trial","unreviewed","1","Improved","confirmed","2"');
    expect(csv).not.toContain("verified");
  });

  it("keeps duplicate column names unambiguous using their stable ids", () => {
    const matrix: ExtractionMatrix = {
      columns: [column("population-a", "Population"), column("population-b", "Population")],
      cells: [],
    };

    const header = buildExtractionCsv([], matrix);
    expect(header).toContain('"Population [population-a] value"');
    expect(header).toContain('"Population [population-b] value"');
  });

  it("quotes CSV special characters and neutralizes formula prefixes", () => {
    const matrix: ExtractionMatrix = {
      columns: [column("=column", "=Potential formula")],
      cells: [cell({ sourceId: "+source", columnId: "=column", value: '@SUM(1,1)\n"quoted"', evidenceIds: ["evidence-1"] })],
    };
    const dangerousPaper: ExtractionExportPaper = {
      sourceId: "+source",
      title: '-A, "title"',
      authors: "\tAuthor\rName",
      publicationYear: "\r2020",
    };

    expect(buildExtractionCsv([dangerousPaper], matrix)).toBe([
      '"source_id","title","authors","publication_year","\'=Potential formula [=column] value","\'=Potential formula [=column] review_status","\'=Potential formula [=column] linked_evidence_count"',
      '"\'+source","\'-A, ""title""","\'\tAuthor\rName","\'\r2020","\'@SUM(1,1)\n""quoted""","unreviewed","1"',
    ].join("\r\n"));
    expect(buildExtractionCsv(
      [{ ...dangerousPaper, title: "\n=SUM(1,1)" }],
      { columns: [], cells: [] },
    )).toContain('"\'\n=SUM(1,1)"');
  });

  it("fails closed on duplicate source and column cell pairs", () => {
    const matrix: ExtractionMatrix = {
      columns: [column("method", "Method")],
      cells: [
        cell(),
        cell({ id: "cell-2", value: "Duplicate" }),
      ],
    };

    expect(() => buildExtractionCsv(papers, matrix)).toThrowError(
      'Duplicate extraction cells for sourceId "source-1" and columnId "method"',
    );
  });

  it("does not mutate the supplied papers or matrix", () => {
    const matrix: ExtractionMatrix = { columns: [column("method", "Method")], cells: [cell()] };
    const input = { papers, matrix };
    const before = structuredClone(input);

    buildExtractionCsv(input.papers, input.matrix);

    expect(input).toStrictEqual(before);
  });
});
