import { describe, expect, it } from "vitest";
import { buildOpenAlexSearchQuery } from "./discoveryQuery";

describe("buildOpenAlexSearchQuery", () => {
  it("compresses question framing while preserving the evaluation intent", () => {
    expect(buildOpenAlexSearchQuery(
      "How are large language model hallucinations evaluated in scientific research?",
    )).toBe("LLM hallucination evaluation methods benchmark");
  });

  it("keeps domain-bearing research terms and avoids duplicate method terms", () => {
    expect(buildOpenAlexSearchQuery("Which methods evaluate research agents?"))
      .toBe("methods evaluation research agents");
  });

  it("falls back to the cleaned question when compression is too short", () => {
    expect(buildOpenAlexSearchQuery("Why?")).toBe("Why");
  });
});
