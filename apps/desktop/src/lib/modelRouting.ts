import type { ProviderInfo } from "@ai4s/sdk";

export type ModelRoutingMode = "auto" | "manual";
export type TaskComplexity = "quick" | "standard" | "deep";

export interface ModelRouteDecision {
  tier: TaskComplexity;
  model: string | null;
  matchedPreference: string | null;
}

const DEEP_SIGNALS = [
  "规划",
  "验收",
  "架构",
  "安全",
  "迁移",
  "复杂",
  "审查",
  "根因",
  "跨模块",
  "端到端",
  "发布",
  "plan",
  "acceptance",
  "architecture",
  "security",
  "migration",
  "complex",
  "review",
  "root cause",
  "cross-module",
  "end-to-end",
  "release",
];

const STANDARD_SIGNALS = [
  "实现",
  "修改",
  "重构",
  "测试",
  "分析",
  "调试",
  "修复",
  "构建",
  "implement",
  "change",
  "refactor",
  "test",
  "analyze",
  "debug",
  "fix",
  "build",
];

const MODEL_PREFERENCES: Record<TaskComplexity, string[]> = {
  quick: ["luna", "kimi-k3", "kimi k3", "moonshot", "terra", "mini", "flash", "haiku", "free"],
  standard: ["terra", "kimi-k3", "kimi k3", "moonshot", "luna", "codex", "sonnet"],
  deep: ["gpt-5.6-sol", "codex sol", " sol", "opus", "reasoning", "deepseek-r1", "o3"],
};

export function classifyTaskComplexity(text: string): TaskComplexity {
  const normalized = text.trim().toLowerCase();
  if (DEEP_SIGNALS.some((signal) => normalized.includes(signal)) || normalized.length >= 900) {
    return "deep";
  }
  if (
    STANDARD_SIGNALS.some((signal) => normalized.includes(signal)) ||
    normalized.length >= 180 ||
    normalized.split("\n").filter(Boolean).length >= 5
  ) {
    return "standard";
  }
  return "quick";
}

/** Pick only a model OpenCode actually reported. The configured default is a
 * valid fallback while provider discovery is incomplete, so routing never
 * invents a provider/model identifier. */
export function routeModelForTask(
  text: string,
  providers: ProviderInfo[],
  defaultModel: string | null,
): ModelRouteDecision {
  const tier = classifyTaskComplexity(text);
  const models = providers.flatMap((provider) =>
    provider.models.map((model) => ({
      value: `${provider.id}/${model.id}`,
      search: `${provider.id} ${provider.name} ${model.id} ${model.name}`.toLowerCase(),
    })),
  );
  for (const preference of MODEL_PREFERENCES[tier]) {
    const match = models.find((model) => model.search.includes(preference));
    if (match) return { tier, model: match.value, matchedPreference: preference.trim() };
  }
  if (defaultModel) return { tier, model: defaultModel, matchedPreference: null };
  return { tier, model: models[0]?.value ?? null, matchedPreference: null };
}
