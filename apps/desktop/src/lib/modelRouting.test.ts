import { describe, expect, it } from "vitest";
import { classifyTaskComplexity, routeModelForTask } from "./modelRouting";

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

describe("task-aware model routing", () => {
  it("classifies quick, implementation, and planning work", () => {
    expect(classifyTaskComplexity("Rename this heading")).toBe("quick");
    expect(classifyTaskComplexity("实现一个文件导入按钮并补测试")).toBe("standard");
    expect(classifyTaskComplexity("规划整体架构并做最终验收")).toBe("deep");
  });

  it("routes quick work to Luna, implementation to Terra, and planning to Sol", () => {
    expect(routeModelForTask("Summarize this note", providers, null)).toMatchObject({
      tier: "quick",
      model: "openai/gpt-5.6-luna",
    });
    expect(routeModelForTask("实现导入和测试", providers, null)).toMatchObject({
      tier: "standard",
      model: "openai/gpt-5.6-terra",
    });
    expect(routeModelForTask("做架构规划和验收", providers, null)).toMatchObject({
      tier: "deep",
      model: "openai/gpt-5.6-sol",
    });
  });

  it("falls back to the configured model without inventing catalog entries", () => {
    expect(routeModelForTask("规划一下", [], "lab/local-model")).toEqual({
      tier: "deep",
      model: "lab/local-model",
      matchedPreference: null,
    });
  });

  it("degrades deep work to an available standard model before a quick default", () => {
    const withoutSol = [
      {
        id: "openai",
        name: "OpenAI",
        models: [
          { id: "gpt-5.6-luna", name: "Codex Luna" },
          { id: "gpt-5.6-terra", name: "Codex Terra" },
        ],
      },
    ];
    expect(routeModelForTask("做架构规划和验收", withoutSol, "openai/gpt-5.6-luna")).toMatchObject({
      tier: "deep",
      model: "openai/gpt-5.6-terra",
      matchedPreference: "terra",
    });
  });
});
