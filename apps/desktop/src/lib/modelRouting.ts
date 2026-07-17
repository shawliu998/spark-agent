import type { ProviderInfo } from "@ai4s/sdk";

export type ModelRoutingMode = "auto" | "manual";
/** Legacy tiers remain readable in persisted manual task-plan journals. New
 * parent turns always use `selected` and never derive a tier from prompt text. */
export type TaskComplexity = "selected" | "quick" | "standard" | "deep";

export interface ModelRouteDecision {
  tier: TaskComplexity;
  model: string | null;
  matchedPreference: string | null;
}

/**
 * Keep the parent turn on the model the user selected. `auto` is retained only
 * as a persisted-settings migration alias; it no longer classifies a prompt or
 * matches provider/model names. Native child agents decide their own configured
 * model at the OpenCode boundary.
 */
export function routeModelForTask(
  _text: string,
  providers: ProviderInfo[],
  defaultModel: string | null,
): ModelRouteDecision {
  const reportedModels = providers.flatMap((provider) =>
    provider.models.map((model) => `${provider.id}/${model.id}`),
  );
  const model = defaultModel ?? reportedModels[0] ?? null;
  return { tier: "selected", model, matchedPreference: null };
}
