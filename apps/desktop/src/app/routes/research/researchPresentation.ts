import type {
  ClaimSupportStatus,
  DatasetProfile,
  ResearchSource,
  WorkflowAnalysisArtifact,
  WorkflowClaim,
  WorkflowEvidenceRelationship,
} from "@spark/research-domain";
import type { ParsedTable } from "@/lib/csv";

export type PresentationIssue =
  | "schema-version"
  | "missing-identity"
  | "missing-columns"
  | "column-count-mismatch"
  | "missing-sampling";

export interface DatasetColumnPresentation {
  key: string;
  name: string;
  inferredType: string;
  missingCount: number | null;
  uniqueCount: number | null;
  potentialDate: boolean;
  potentialId: boolean;
  mixedType: boolean;
}

export interface DatasetWarningPresentation {
  key: string;
  code: string;
  message: string;
  columnName: string | null;
}

export interface DatasetSamplingPresentation {
  method: string | null;
  rowsRead: number | null;
  rowsProfiled: number | null;
  maxSampleRows: number | null;
  seed: number | null;
}

export interface DatasetProfilePresentation {
  schemaVersion: string | null;
  datasetSourceId: string | null;
  filename: string | null;
  contentHash: string | null;
  fileSizeBytes: number | null;
  encoding: string | null;
  delimiter: string | null;
  rowCount: number | null;
  columnCount: number | null;
  columns: DatasetColumnPresentation[];
  sampling: DatasetSamplingPresentation;
  warnings: DatasetWarningPresentation[];
  issues: PresentationIssue[];
}

export type EvidenceRelationshipPresentation =
  | "supporting"
  | "contradicting"
  | "unclassified";

export interface CitationPresentation {
  key: string;
  evidenceId: string | null;
  sourceId: string | null;
  sourceTitle: string;
  page: string;
  text: string;
  relationship: EvidenceRelationshipPresentation;
  verified: boolean;
  frozen: boolean;
  quoteHash: string | null;
  sourceContentHash: string | null;
  sourcePageManifestHash: string | null;
  confidence: number | null;
  original: WorkflowEvidenceRelationship | null;
}

export interface ClaimPresentation {
  key: string;
  id: string | null;
  statement: string;
  supportStatus: ClaimSupportStatus | "unclassified";
  confidence: number | null;
  citations: CitationPresentation[];
  issues: Array<"missing-statement" | "unknown-support-status" | "missing-evidence-list">;
}

export type ArtifactPresentationKind =
  | "figure"
  | "table"
  | "notebook"
  | "environment"
  | "log"
  | "structured-data"
  | "generic";

export interface ArtifactPresentation {
  key: string;
  id: string | null;
  name: string;
  path: string | null;
  mimeType: string | null;
  artifactType: string | null;
  kind: ArtifactPresentationKind;
  previewMode: "image" | "table" | "text" | "none";
  contentHash: string | null;
  sizeBytes: number | null;
  integrityStatus: "hash-bound" | "unverified";
  original: WorkflowAnalysisArtifact | null;
}

/**
 * Converts current, legacy, or partially unavailable profile data into a stable
 * display model. This never invents scientific values and never mutates the
 * persisted profile or its provenance fields.
 */
export function presentDatasetProfile(
  profile: DatasetProfile | unknown,
): DatasetProfilePresentation {
  const record = asRecord(profile);
  const schemaVersion = asString(record?.schemaVersion);
  const datasetSourceId = asString(record?.datasetSourceId);
  const filename = asString(record?.filename);
  const contentHash = asSha256(record?.contentHash);
  const fileSizeBytes = asNonNegativeNumber(record?.fileSizeBytes);
  const encoding = asString(record?.encoding);
  const delimiter = asString(record?.delimiter);
  const rowCount = asNonNegativeInteger(record?.rowCount);
  const declaredColumnCount = asNonNegativeInteger(record?.columnCount);
  const rawColumns = Array.isArray(record?.columns) ? record.columns : [];
  const columns = rawColumns.map((column, index) => presentColumn(column, index));
  const rawWarnings = Array.isArray(record?.warnings) ? record.warnings : [];
  const warnings = rawWarnings.map((warning, index) => presentWarning(warning, index));
  const sampling = presentSampling(record?.sampling);

  const issues: PresentationIssue[] = [];
  if (schemaVersion !== "1") issues.push("schema-version");
  if (!datasetSourceId || !contentHash) issues.push("missing-identity");
  if (!Array.isArray(record?.columns)) issues.push("missing-columns");
  if (
    declaredColumnCount !== null &&
    declaredColumnCount !== columns.length
  ) {
    issues.push("column-count-mismatch");
  }
  if (!asRecord(record?.sampling)) issues.push("missing-sampling");

  return {
    schemaVersion,
    datasetSourceId,
    filename,
    contentHash,
    fileSizeBytes,
    encoding,
    delimiter,
    rowCount,
    columnCount: declaredColumnCount ?? (columns.length > 0 ? columns.length : null),
    columns,
    sampling,
    warnings,
    issues,
  };
}

/** Keeps arbitrary CSV/TSV previews rectangular without discarding cells. */
export function presentTable(table: ParsedTable | unknown): ParsedTable {
  const record = asRecord(table);
  const rawColumns = Array.isArray(record?.columns) ? record.columns : [];
  const rawRows = Array.isArray(record?.rows) ? record.rows : [];
  const rows = rawRows.map((row) =>
    Array.isArray(row) ? row.map((cell) => displayScalar(cell)) : [displayScalar(row)],
  );
  const suppliedColumns = rawColumns.map((column) => displayScalar(column));
  const width = Math.max(suppliedColumns.length, ...rows.map((row) => row.length), 0);
  const columns = Array.from({ length: width }, (_, index) =>
    suppliedColumns[index]?.trim()
      ? suppliedColumns[index]
      : `Column ${index + 1}`,
  );
  const normalizedRows = rows.map((row) => [
    ...row,
    ...Array(Math.max(0, width - row.length)).fill(""),
  ]);

  return {
    columns,
    rows: normalizedRows,
    truncated: record?.truncated === true,
  };
}

export function presentWorkflowClaim(
  claim: WorkflowClaim | unknown,
  sources: readonly ResearchSource[],
  index: number,
): ClaimPresentation {
  const record = asRecord(claim);
  const id = asString(record?.id);
  const statement = asString(record?.statement);
  const rawStatus = asString(record?.supportStatus);
  const supportStatus = isClaimSupportStatus(rawStatus)
    ? rawStatus
    : "unclassified";
  const rawEvidence = Array.isArray(record?.evidence) ? record.evidence : [];
  const issues: ClaimPresentation["issues"] = [];
  if (!statement) issues.push("missing-statement");
  if (!isClaimSupportStatus(rawStatus)) issues.push("unknown-support-status");
  if (!Array.isArray(record?.evidence)) issues.push("missing-evidence-list");

  return {
    key: id ?? `claim-${index}`,
    id,
    statement: statement ?? "Claim text was not provided.",
    supportStatus,
    confidence: asProbability(record?.confidence),
    citations: rawEvidence.map((evidence, evidenceIndex) =>
      presentCitation(evidence, sources, evidenceIndex),
    ),
    issues,
  };
}

export function presentArtifact(
  artifact: WorkflowAnalysisArtifact | unknown,
  index = 0,
): ArtifactPresentation {
  const record = asRecord(artifact);
  const id = asString(record?.id);
  const path = asString(record?.path);
  const mimeType = asString(record?.mimeType);
  const artifactType = asString(record?.artifactType);
  const contentHash = asSha256(record?.contentHash);
  const kind = classifyArtifact(path, mimeType, artifactType);
  const original = isWorkflowArtifact(record)
    ? (artifact as WorkflowAnalysisArtifact)
    : null;

  return {
    key: id ?? path ?? `artifact-${index}`,
    id,
    name: path ? fileName(path) : `Artifact ${index + 1}`,
    path,
    mimeType,
    artifactType,
    kind,
    previewMode:
      kind === "figure"
        ? "image"
        : kind === "table"
          ? "table"
          : kind === "environment" || kind === "log"
            ? "text"
            : "none",
    contentHash,
    sizeBytes: asNonNegativeNumber(record?.sizeBytes),
    integrityStatus: contentHash ? "hash-bound" : "unverified",
    original,
  };
}

export function displayValue(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "number" && !Number.isFinite(value)) return fallback;
  return displayScalar(value);
}

function presentColumn(value: unknown, index: number): DatasetColumnPresentation {
  const record = asRecord(value);
  const declaredIndex = asNonNegativeInteger(record?.index) ?? index;
  return {
    key: `${declaredIndex}:${asString(record?.name) ?? index}`,
    name: asString(record?.name) ?? `Column ${index + 1}`,
    inferredType: asString(record?.inferredType) ?? "unknown",
    missingCount: asNonNegativeInteger(record?.missingCount),
    uniqueCount: asNonNegativeInteger(record?.uniqueCount),
    potentialDate: record?.potentialDate === true,
    potentialId: record?.potentialId === true,
    mixedType: record?.mixedType === true,
  };
}

function presentCitation(
  value: unknown,
  sources: readonly ResearchSource[],
  index: number,
): CitationPresentation {
  const record = asRecord(value);
  const evidenceId = asString(record?.evidenceId);
  const sourceId = asString(record?.sourceId);
  const sourceTitle =
    asString(record?.sourceTitle) ??
    sources.find((source) => source.id === sourceId)?.title ??
    "Unknown source";
  const pageIndex = asNonNegativeInteger(record?.pageIndex);
  const pageLabel = asString(record?.pageLabel);
  const relationship =
    record?.relationship === "supporting" || record?.relationship === "contradicting"
      ? record.relationship
      : "unclassified";
  const sourceContentHash = asString(record?.sourceContentHash);
  const sourcePageManifestHash = asString(record?.sourcePageManifestHash);
  const frozen =
    asString(record?.sourceTitle) !== null &&
    sourceContentHash !== null &&
    sourcePageManifestHash !== null;
  const original = isWorkflowEvidence(record)
    ? (value as WorkflowEvidenceRelationship)
    : null;

  return {
    key: evidenceId ?? `evidence-${index}`,
    evidenceId,
    sourceId,
    sourceTitle,
    page: pageLabel ?? (pageIndex === null ? "—" : String(pageIndex + 1)),
    text: asString(record?.text) ?? "Evidence text was not provided.",
    relationship,
    verified: record?.verified === true,
    frozen,
    quoteHash: asString(record?.quoteHash),
    sourceContentHash,
    sourcePageManifestHash,
    confidence: asProbability(record?.confidence),
    original,
  };
}

function presentWarning(value: unknown, index: number): DatasetWarningPresentation {
  const record = asRecord(value);
  const code = asString(record?.code) ?? "unclassified-warning";
  const columnName = asString(record?.columnName);
  return {
    key: `${code}:${columnName ?? "dataset"}:${index}`,
    code,
    message: asString(record?.message) ?? "No warning details were provided.",
    columnName,
  };
}

function presentSampling(value: unknown): DatasetSamplingPresentation {
  const record = asRecord(value);
  return {
    method: asString(record?.method),
    rowsRead: asNonNegativeInteger(record?.rowsRead),
    rowsProfiled: asNonNegativeInteger(record?.rowsProfiled),
    maxSampleRows: asNonNegativeInteger(record?.maxSampleRows),
    seed: asNonNegativeInteger(record?.seed),
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function asSha256(value: unknown): string | null {
  const candidate = asString(value);
  return candidate && /^[0-9a-f]{64}$/.test(candidate) ? candidate : null;
}

function asNonNegativeNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : null;
}

function asNonNegativeInteger(value: unknown): number | null {
  const number = asNonNegativeNumber(value);
  return number !== null && Number.isInteger(number) ? number : null;
}

function asProbability(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1
    ? value
    : null;
}

const CLAIM_SUPPORT_STATUSES = new Set<ClaimSupportStatus>([
  "supported",
  "partially-supported",
  "contradicted",
  "insufficient-evidence",
  "pending-review",
  "not-applicable",
]);

function isClaimSupportStatus(value: string | null): value is ClaimSupportStatus {
  return value !== null && CLAIM_SUPPORT_STATUSES.has(value as ClaimSupportStatus);
}

function isWorkflowEvidence(
  record: Record<string, unknown> | null,
): record is Record<string, unknown> {
  return Boolean(
    record &&
      asString(record.evidenceId) &&
      asString(record.sourceId) &&
      asNonNegativeInteger(record.pageIndex) !== null &&
      asString(record.text) &&
      asString(record.quoteHash) &&
      typeof record.verified === "boolean" &&
      (record.relationship === "supporting" || record.relationship === "contradicting"),
  );
}

function isWorkflowArtifact(
  record: Record<string, unknown> | null,
): record is Record<string, unknown> {
  return Boolean(
    record &&
      asString(record.id) &&
      asString(record.path) &&
      asString(record.mimeType) &&
      asString(record.artifactType),
  );
}

function classifyArtifact(
  path: string | null,
  mimeType: string | null,
  artifactType: string | null,
): ArtifactPresentationKind {
  const normalizedPath = path?.toLowerCase() ?? "";
  const normalizedMime = mimeType?.toLowerCase() ?? "";
  const normalizedType = artifactType?.toLowerCase() ?? "";
  if (normalizedType === "figure" || normalizedMime.startsWith("image/")) return "figure";
  if (
    normalizedType === "dataset" ||
    normalizedPath.endsWith(".csv") ||
    normalizedPath.endsWith(".tsv") ||
    normalizedMime === "text/csv" ||
    normalizedMime === "application/csv" ||
    normalizedMime === "text/tab-separated-values"
  ) {
    return "table";
  }
  if (normalizedType === "notebook-executed" || normalizedPath.endsWith(".ipynb")) {
    return "notebook";
  }
  if (normalizedType === "environment") return "environment";
  if (["stdout", "stderr", "log"].includes(normalizedType)) return "log";
  if (normalizedType === "structured-data" || normalizedMime === "application/json") {
    return "structured-data";
  }
  return "generic";
}

function fileName(path: string): string {
  const segments = path.split("/").filter(Boolean);
  return segments[segments.length - 1] ?? path;
}

function displayScalar(value: unknown): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
