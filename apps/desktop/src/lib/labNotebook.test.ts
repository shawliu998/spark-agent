import { describe, expect, it } from "vitest";
import { labNotebookMarkdown, parseLabNotebook } from "./labNotebook";

const valid = JSON.stringify({
  version: 1,
  id: "finding-1",
  timestamp: "2026-07-17T06:00:00.000Z",
  type: "result",
  content: "The control mean was lower than the treatment mean.",
  sessionId: "ses_1",
  evidence: [{ path: "tables/summary.csv", label: "Summary table" }],
});

describe("parseLabNotebook", () => {
  it("keeps durable entries when a restart leaves a malformed final write", () => {
    const result = parseLabNotebook(`${valid}\n{"version":1`);
    expect(result.entries).toHaveLength(1);
    expect(result.warnings).toEqual(["Ignored an interrupted final notebook line."]);
  });

  it("skips malformed complete lines without losing later entries", () => {
    const result = parseLabNotebook(`${valid}\nnot json\n${valid}\n`);
    expect(result.entries).toHaveLength(2);
    expect(result.warnings).toEqual(["Ignored invalid notebook line 2."]);
  });

  it("exports readable Markdown from validated records", () => {
    const { entries } = parseLabNotebook(`${valid}\n`);
    expect(labNotebookMarkdown(entries)).toContain("## result · 2026-07-17T06:00:00.000Z");
    expect(labNotebookMarkdown(entries)).toContain("`tables/summary.csv`");
  });
});
