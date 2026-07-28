import type { ExtractionMatrix } from "@spark/research-domain";

export interface ExtractionExportPaper {
  sourceId: string;
  title: string;
  authors: string;
  publicationYear: string;
}

function csvCell(value: string | number): string {
  const raw = String(value);
  const safe = /^[=+\-@\t\r\n]/.test(raw) ? `'${raw}` : raw;
  return `"${safe.replace(/"/g, '""')}"`;
}

function cellKey(sourceId: string, columnId: string): string {
  return JSON.stringify([sourceId, columnId]);
}

function columnHeaders(name: string, id: string): [string, string, string] {
  const label = `${name} [${id}]`;
  return [
    `${label} value`,
    `${label} review_status`,
    `${label} linked_evidence_count`,
  ];
}

/** Builds a spreadsheet-safe wide CSV for the current extraction matrix. */
export function buildExtractionCsv(
  papers: readonly ExtractionExportPaper[],
  matrix: ExtractionMatrix,
): string {
  const cellsBySourceAndColumn = new Map<string, ExtractionMatrix["cells"][number]>();
  for (const cell of matrix.cells) {
    const key = cellKey(cell.sourceId, cell.columnId);
    if (cellsBySourceAndColumn.has(key)) {
      throw new Error(
        `Duplicate extraction cells for sourceId ${JSON.stringify(cell.sourceId)} and columnId ${JSON.stringify(cell.columnId)}`,
      );
    }
    cellsBySourceAndColumn.set(key, cell);
  }

  const headers = [
    "source_id",
    "title",
    "authors",
    "publication_year",
    ...matrix.columns.flatMap((column) => columnHeaders(column.name, column.id)),
  ];
  const rows = papers.map((paper) => {
    const values: Array<string | number> = [
      paper.sourceId,
      paper.title,
      paper.authors,
      paper.publicationYear,
    ];
    for (const column of matrix.columns) {
      const cell = cellsBySourceAndColumn.get(cellKey(paper.sourceId, column.id));
      values.push(cell?.value ?? "", cell?.reviewStatus ?? "missing", cell?.evidenceIds.length ?? 0);
    }
    return values.map(csvCell).join(",");
  });

  return [headers.map(csvCell).join(","), ...rows].join("\r\n");
}
