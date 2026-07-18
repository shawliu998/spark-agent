export const LAB_NOTEBOOK_PATH = ".spark/lab-notebook.jsonl";

export const labNotebookTypes = [
  "hypothesis",
  "method",
  "observation",
  "result",
  "decision",
  "limitation",
] as const;

export type LabNotebookType = (typeof labNotebookTypes)[number];

export interface LabNotebookEvidence {
  path: string;
  label?: string;
}

/** A small, append-only record that remains useful without any runtime state. */
export interface LabNotebookEntry {
  version: 1;
  id: string;
  timestamp: string;
  type: LabNotebookType;
  content: string;
  sessionId?: string;
  evidence?: LabNotebookEvidence[];
}

export interface LabNotebookParseResult {
  entries: LabNotebookEntry[];
  /** Invalid complete lines are reported; an interrupted final write is ignored. */
  warnings: string[];
}

const typeSet = new Set<string>(labNotebookTypes);
const idPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

function asNonEmptyString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

export function parseLabNotebookEntry(value: unknown): LabNotebookEntry | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const id = asNonEmptyString(record.id);
  const timestamp = asNonEmptyString(record.timestamp);
  const content = asNonEmptyString(record.content);
  if (
    record.version !== 1 ||
    !id ||
    !idPattern.test(id) ||
    !timestamp ||
    Number.isNaN(Date.parse(timestamp)) ||
    !content ||
    typeof record.type !== "string" ||
    !typeSet.has(record.type)
  ) {
    return null;
  }
  if (record.sessionId !== undefined && !asNonEmptyString(record.sessionId)) return null;
  if (record.evidence !== undefined && !Array.isArray(record.evidence)) return null;
  const evidence = record.evidence?.map((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return null;
    const candidate = item as Record<string, unknown>;
    const path = asNonEmptyString(candidate.path);
    const label = candidate.label === undefined ? undefined : asNonEmptyString(candidate.label);
    return path && (candidate.label === undefined || label) ? { path, ...(label ? { label } : {}) } : null;
  });
  if (evidence?.some((item) => item === null)) return null;
  return {
    version: 1,
    id,
    timestamp,
    type: record.type as LabNotebookType,
    content,
    ...(record.sessionId ? { sessionId: record.sessionId as string } : {}),
    ...(evidence?.length ? { evidence: evidence as LabNotebookEvidence[] } : {}),
  };
}

/** Parse every durable JSONL line and tolerate a crash-truncated final line. */
export function parseLabNotebook(source: string): LabNotebookParseResult {
  const lines = source.split(/\r?\n/);
  const lastNonEmpty = lines.reduce((last, line, index) => (line.trim() ? index : last), -1);
  const entries: LabNotebookEntry[] = [];
  const warnings: string[] = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (!line) continue;
    try {
      const entry = parseLabNotebookEntry(JSON.parse(line));
      if (!entry) throw new Error("does not match lab-notebook v1");
      entries.push(entry);
    } catch {
      if (index === lastNonEmpty && !source.endsWith("\n")) {
        warnings.push("Ignored an interrupted final notebook line.");
      } else {
        warnings.push(`Ignored invalid notebook line ${index + 1}.`);
      }
    }
  }
  return { entries, warnings };
}

export function labNotebookMarkdown(entries: LabNotebookEntry[]): string {
  return entries
    .map((entry) => {
      const evidence = entry.evidence?.map((item) => `- ${item.label ?? item.path}: \`${item.path}\``).join("\n");
      return [
        `## ${entry.type} · ${entry.timestamp}`,
        entry.sessionId ? `Session: \`${entry.sessionId}\`` : "",
        "",
        entry.content,
        evidence ? `\nEvidence:\n${evidence}` : "",
      ].filter(Boolean).join("\n");
    })
    .join("\n\n");
}
