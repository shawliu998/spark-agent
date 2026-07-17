import { describe, expect, it } from "vitest";
import { RESEARCH_TEMPLATES } from "./projects";

describe("project templates", () => {
  it("offer General Research folder templates without verified workflow types", () => {
    expect(RESEARCH_TEMPLATES.map((template) => template.id)).toEqual([
      "blank", "literature-review", "data-analysis", "computational-study",
    ]);
    expect(JSON.stringify(RESEARCH_TEMPLATES)).not.toMatch(/science-core|verified/i);
  });
});
