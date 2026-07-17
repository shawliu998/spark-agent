import { describe, expect, it } from "vitest";
import { routeModelForTask } from "./modelRouting";

const providers = [
  {
    id: "openai",
    name: "OpenAI",
    models: [
      { id: "gpt-5.6-luna", name: "Codex Luna" },
      { id: "gpt-5.6-terra", name: "Codex Terra" },
      { id: "gpt-5.6-sol", name: "Codex Sol" },
    ],
  },
  {
    id: "moonshot",
    name: "Moonshot",
    models: [{ id: "kimi-k3", name: "Kimi K3" }],
  },
];

describe("selected-model routing", () => {
  it("keeps the selected model for every prompt without inspecting its text", () => {
    expect(routeModelForTask("Summarize this note", providers, "moonshot/kimi-k3")).toEqual({
      tier: "selected",
      model: "moonshot/kimi-k3",
      matchedPreference: null,
    });
    expect(routeModelForTask("做架构规划和验收", providers, "moonshot/kimi-k3")).toEqual({
      tier: "selected",
      model: "moonshot/kimi-k3",
      matchedPreference: null,
    });
  });

  it("falls back to the configured model without inventing catalog entries", () => {
    expect(routeModelForTask("规划一下", [], "lab/local-model")).toEqual({
      tier: "selected",
      model: "lab/local-model",
      matchedPreference: null,
    });
  });

  it("uses only a runtime-reported model when no selection exists", () => {
    expect(routeModelForTask("anything", providers, null)).toEqual({
      tier: "selected",
      model: "openai/gpt-5.6-luna",
      matchedPreference: null,
    });
  });
});
