import { describe, expect, it } from "vitest";
import i18n, { NAMESPACES } from "./index";

describe("i18n instance", () => {
  it("initializes with English and the full namespace set", () => {
    expect(i18n.language).toBe("en");
    expect(NAMESPACES).toContain("common");
    expect(NAMESPACES.length).toBe(8);
  });

  it("resolves a seeded key", () => {
    expect(i18n.t("common:actions.save")).toBe("Save");
  });

  it("ships the General Research and Verified workflow positioning", () => {
    expect(i18n.t("session:researchControls.mode.general")).toBe("General Research");
    expect(i18n.t("pages:research.workflow.typeLiterature")).toBe(
      "Verified Literature Synthesis",
    );
    expect(i18n.t("pages:research.workflow.typeDataset")).toBe("Verified Dataset Analysis");
  });

  it("falls back to English for a not-yet-shipped language", async () => {
    await i18n.changeLanguage("pt-BR");
    // pt-BR is registered but not shipped → no bundled resources → falls back to en.
    expect(i18n.t("common:actions.save")).toBe("Save");
    await i18n.changeLanguage("en");
  });
});
