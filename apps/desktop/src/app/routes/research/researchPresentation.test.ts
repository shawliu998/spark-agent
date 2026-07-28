import { describe, expect, it } from "vitest";
import type { ResearchSource } from "@spark/research-domain";
import {
  displayValue,
  presentArtifact,
  presentDatasetProfile,
  presentTable,
  presentWorkflowClaim,
} from "./researchPresentation";

describe("research presentation adapters", () => {
  it("preserves current profile values without presentation issues", () => {
    const view = presentDatasetProfile({
      schemaVersion: "1",
      datasetSourceId: "dataset-1",
      filename: "study.csv",
      contentHash: "a".repeat(64),
      fileSizeBytes: 120,
      encoding: "utf-8",
      delimiter: ",",
      rowCount: 4,
      columnCount: 1,
      columns: [{
        index: 0,
        name: "outcome",
        inferredType: "number",
        missingCount: 0,
        uniqueCount: 4,
        numericRange: { minimum: 1, maximum: 4 },
        lowCardinality: null,
        potentialDate: false,
        potentialId: false,
        mixedType: false,
      }],
      sampling: {
        method: "head-and-reservoir-v1",
        rowsRead: 4,
        rowsProfiled: 4,
        maxSampleRows: 500,
        seed: 7,
      },
      warnings: [],
    });

    expect(view.issues).toEqual([]);
    expect(view.columns[0]).toMatchObject({ name: "outcome", uniqueCount: 4 });
  });

  it("marks legacy or partial profiles without inventing scientific values", () => {
    const view = presentDatasetProfile({
      schemaVersion: "0",
      filename: "legacy.csv",
      rowCount: "unknown",
      columnCount: 2,
      columns: [{ name: "group" }],
    });

    expect(view.rowCount).toBeNull();
    expect(view.columns[0]).toMatchObject({
      name: "group",
      inferredType: "unknown",
      uniqueCount: null,
    });
    expect(view.issues).toEqual([
      "schema-version",
      "missing-identity",
      "column-count-mismatch",
      "missing-sampling",
    ]);
  });

  it("normalizes uneven or unnamed table data for stable rendering", () => {
    expect(
      presentTable({
        columns: ["group"],
        rows: [["control", 1.5], ["treated"]],
        truncated: false,
      }),
    ).toEqual({
      columns: ["group", "Column 2"],
      rows: [["control", "1.5"], ["treated", ""]],
      truncated: false,
    });
  });

  it("uses an explicit fallback for absent and invalid display values", () => {
    expect(displayValue(null)).toBe("—");
    expect(displayValue(Number.NaN)).toBe("—");
    expect(displayValue(0)).toBe("0");
  });

  it("keeps incomplete citations visible but not source-selectable", () => {
    const claim = presentWorkflowClaim(
      {
        id: "claim-1",
        statement: "The intervention improved the outcome.",
        supportStatus: "supported",
        confidence: 0.8,
        evidence: [{
          evidenceId: "evidence-1",
          sourceId: "paper-1",
          sourceTitle: null,
          pageIndex: null,
          text: "Reported outcome text",
          relationship: "supporting",
          verified: true,
          quoteHash: null,
        }],
      },
      [{
        id: "paper-1",
        projectId: "project-1",
        title: "Current paper title",
        sourceKind: "pdf",
        authors: [],
        doi: null,
        arxivId: null,
        localPath: "sources/paper.pdf",
        publicationDate: null,
        ingestionStatus: "ready",
        contentHash: "a".repeat(64),
        pageCount: 2,
        createdAt: "2026-07-19T00:00:00Z",
      } satisfies ResearchSource],
      0,
    );

    expect(claim.supportStatus).toBe("supported");
    expect(claim.citations[0]).toMatchObject({
      sourceTitle: "Current paper title",
      page: "—",
      frozen: false,
      original: null,
    });
  });

  it("does not promote unknown support states to verified evidence", () => {
    const claim = presentWorkflowClaim(
      {
        statement: "Legacy claim",
        supportStatus: "probably-supported",
      },
      [],
      2,
    );

    expect(claim.supportStatus).toBe("unclassified");
    expect(claim.issues).toEqual([
      "unknown-support-status",
      "missing-evidence-list",
    ]);
  });

  it("classifies figures and unknown artifacts without inventing integrity", () => {
    const figure = presentArtifact({
      id: "figure-1",
      path: "outputs/result.svg",
      mimeType: "image/svg+xml",
      artifactType: "custom-plot",
      contentHash: "f".repeat(64),
      sizeBytes: 400,
    });
    const unknown = presentArtifact({
      id: "artifact-2",
      path: "outputs/result.bin",
      mimeType: "application/octet-stream",
      artifactType: "future-output",
    });

    expect(figure).toMatchObject({
      kind: "figure",
      previewMode: "image",
      integrityStatus: "hash-bound",
    });
    expect(unknown).toMatchObject({
      kind: "generic",
      previewMode: "none",
      integrityStatus: "unverified",
    });
  });

  it("never promotes malformed content hashes to verified integrity", () => {
    const artifact = presentArtifact({
      id: "artifact-invalid-hash",
      path: "outputs/result.csv",
      mimeType: "text/csv",
      artifactType: "dataset",
      contentHash: "not-a-sha256",
      sizeBytes: 20,
    });
    const profile = presentDatasetProfile({
      schemaVersion: "1",
      datasetSourceId: "dataset-1",
      filename: "study.csv",
      contentHash: "A".repeat(64),
      columns: [],
      sampling: {},
    });

    expect(artifact).toMatchObject({ contentHash: null, integrityStatus: "unverified" });
    expect(profile.contentHash).toBeNull();
    expect(profile.issues).toContain("missing-identity");
  });
});
