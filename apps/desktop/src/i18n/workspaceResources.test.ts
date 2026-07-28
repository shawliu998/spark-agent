import { describe, expect, it } from "vitest";
import { workspaceResources } from "./workspaceResources";

function leafKeys(value: unknown, prefix = ""): string[] {
  if (value === null || typeof value !== "object") return [prefix];
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    leafKeys(child, prefix ? `${prefix}.${key}` : key),
  );
}

describe("workspace locale resources", () => {
  const englishKeys = leafKeys(workspaceResources.en).sort();

  it.each(Object.entries(workspaceResources))(
    "%s has the complete analysis and artifact-continuity key set",
    (_locale, resources) => {
      expect(leafKeys(resources).sort()).toEqual(englishKeys);
    },
  );

  it("localizes the visible analysis and continuity shells in Simplified Chinese", () => {
    expect(workspaceResources["zh-Hans"].analysis.title).toBe("数据分析");
    expect(workspaceResources["zh-Hans"].artifactContinuity.notebooksTitle).toBe(
      "项目笔记本",
    );
  });
});
