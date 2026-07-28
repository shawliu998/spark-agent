import { describe, expect, it } from "vitest";
import {
  RESEARCH_MODEL_PROVIDERS,
  researchModelProvider,
} from "./researchModelProviders";

describe("Research model provider registry", () => {
  it("covers the supported providers with unique stable IDs", () => {
    expect(RESEARCH_MODEL_PROVIDERS.map(({ id }) => id)).toEqual([
      "openai",
      "anthropic",
      "gemini",
      "deepseek",
      "openrouter",
      "kimi",
      "qwen",
      "mistral",
      "groq",
      "xai",
      "siliconflow",
      "minimax",
      "ollama",
      "custom",
    ]);
    expect(new Set(RESEARCH_MODEL_PROVIDERS.map(({ id }) => id)).size).toBe(
      RESEARCH_MODEL_PROVIDERS.length,
    );
  });

  it("uses only the two implemented protocol families", () => {
    expect(
      new Set(RESEARCH_MODEL_PROVIDERS.map(({ protocol }) => protocol)),
    ).toEqual(new Set(["openai-compatible", "anthropic"]));
    expect(
      RESEARCH_MODEL_PROVIDERS.filter(
        ({ protocol }) => protocol === "anthropic",
      ).map(({ id }) => id),
    ).toEqual(["anthropic"]);
    expect(
      RESEARCH_MODEL_PROVIDERS.filter(
        ({ requiresApiKey }) => !requiresApiKey,
      ).map(({ id }) => id),
    ).toEqual(["ollama"]);
  });

  it("allows insecure HTTP only for the loopback Ollama preset", () => {
    for (const provider of RESEARCH_MODEL_PROVIDERS) {
      if (!provider.apiBase) continue;
      if (provider.id === "ollama") {
        expect(provider.apiBase).toMatch(/^http:\/\/127\.0\.0\.1:/);
      } else {
        expect(provider.apiBase).toMatch(/^https:\/\//);
      }
    }
  });

  it("falls back to the editable custom provider", () => {
    expect(researchModelProvider("future-provider").id).toBe("custom");
  });
});
