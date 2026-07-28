import { describe, expect, it } from "vitest";
import { researchResources } from "./researchResources";

function leafKeys(value: unknown, prefix = ""): string[] {
  if (value === null || typeof value !== "object") return [prefix];
  return Object.entries(value as Record<string, unknown>).flatMap(([key, child]) =>
    leafKeys(child, prefix ? `${prefix}.${key}` : key),
  );
}

describe("research locale resources", () => {
  const englishKeys = leafKeys(researchResources.en).sort();

  it.each(Object.entries(researchResources))(
    "%s has the complete core research key set",
    (_locale, resources) => {
      expect(leafKeys(resources).sort()).toEqual(englishKeys);
    },
  );

  it.each(["zh-Hans", "ja", "es", "de", "fr", "ko"] as const)(
    "%s localizes the visible research shell",
    (locale) => {
      expect(researchResources[locale].libraryTitle).not.toBe(
        researchResources.en.libraryTitle,
      );
      expect(researchResources[locale].empty.sourcesTitle).not.toBe(
        researchResources.en.empty.sourcesTitle,
      );
    },
  );

  it("localizes the new literature workspace chrome in Simplified Chinese and keeps English fallback elsewhere", () => {
    expect(researchResources["zh-Hans"].literature.surfaces.papers).toBe("论文");
    expect(researchResources["zh-Hans"].literature.datasetSurfaces.notebook).toBe("笔记本");
    expect(researchResources["zh-Hans"].showArchived).toBe("显示已归档");
    expect(researchResources["zh-Hans"].renameProject).toBe("重命名");
    expect(researchResources["zh-Hans"].workflow.typeLiterature).toBe("文献综合");
    expect(researchResources["zh-Hans"].workflow.generationMode.local).toBe("本地确定性");
    expect(researchResources.ja.literature.sortAz).toBe(researchResources.en.literature.sortAz);
  });
});
