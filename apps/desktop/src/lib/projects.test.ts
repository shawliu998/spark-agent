import { describe, expect, it } from "vitest";
import { projectRoute, RESEARCH_TEMPLATES } from "./projects";

describe("project templates", () => {
  it("offer General Research folder templates without verified workflow types", () => {
    expect(RESEARCH_TEMPLATES.map((template) => template.id)).toEqual([
      "blank", "literature-review", "dataset-analysis", "papers-and-data", "reproduce-result", "research-report",
    ]);
    expect(JSON.stringify(RESEARCH_TEMPLATES)).not.toMatch(/science-core|verified/i);
  });

  it("resumes a recent project's last session when metadata provides one", () => {
    expect(projectRoute({ lastSessionId: "ses_previous" })).toBe("/live/ses_previous");
    expect(projectRoute({ lastSessionId: undefined })).toBe("/live");
  });
});
