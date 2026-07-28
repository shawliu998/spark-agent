export type ResearchModelProtocol = "openai-compatible" | "anthropic";

export interface ResearchModelProviderPreset {
  id: string;
  name: string;
  protocol: ResearchModelProtocol;
  apiBase: string;
  modelSuggestions: readonly string[];
  defaultEmbeddingModel: string;
  embeddingSupport: "same-endpoint" | "not-available";
  requiresApiKey: boolean;
}

/**
 * Stable connection presets for Science Core. Model IDs remain editable because
 * provider catalogs change independently of Spark releases.
 */
export const RESEARCH_MODEL_PROVIDERS: readonly ResearchModelProviderPreset[] = [
  {
    id: "openai",
    name: "OpenAI",
    protocol: "openai-compatible",
    apiBase: "https://api.openai.com/v1",
    modelSuggestions: ["gpt-4.1-mini", "gpt-4.1"],
    defaultEmbeddingModel: "text-embedding-3-small",
    embeddingSupport: "same-endpoint",
    requiresApiKey: true,
  },
  {
    id: "anthropic",
    name: "Anthropic",
    protocol: "anthropic",
    apiBase: "https://api.anthropic.com/v1",
    modelSuggestions: ["claude-sonnet-4-5", "claude-haiku-4-5"],
    defaultEmbeddingModel: "",
    embeddingSupport: "not-available",
    requiresApiKey: true,
  },
  {
    id: "gemini",
    name: "Google Gemini",
    protocol: "openai-compatible",
    apiBase: "https://generativelanguage.googleapis.com/v1beta/openai",
    modelSuggestions: ["gemini-2.5-flash", "gemini-2.5-pro"],
    defaultEmbeddingModel: "gemini-embedding-001",
    embeddingSupport: "same-endpoint",
    requiresApiKey: true,
  },
  {
    id: "deepseek",
    name: "DeepSeek",
    protocol: "openai-compatible",
    apiBase: "https://api.deepseek.com",
    modelSuggestions: ["deepseek-chat", "deepseek-reasoner"],
    defaultEmbeddingModel: "",
    embeddingSupport: "not-available",
    requiresApiKey: true,
  },
  {
    id: "openrouter",
    name: "OpenRouter",
    protocol: "openai-compatible",
    apiBase: "https://openrouter.ai/api/v1",
    modelSuggestions: [],
    defaultEmbeddingModel: "",
    embeddingSupport: "not-available",
    requiresApiKey: true,
  },
  {
    id: "kimi",
    name: "Kimi / Moonshot",
    protocol: "openai-compatible",
    apiBase: "https://api.moonshot.cn/v1",
    modelSuggestions: ["kimi-k2.5", "moonshot-v1-8k"],
    defaultEmbeddingModel: "",
    embeddingSupport: "not-available",
    requiresApiKey: true,
  },
  {
    id: "qwen",
    name: "Qwen / DashScope",
    protocol: "openai-compatible",
    apiBase: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    modelSuggestions: ["qwen-plus", "qwen-turbo"],
    defaultEmbeddingModel: "text-embedding-v4",
    embeddingSupport: "same-endpoint",
    requiresApiKey: true,
  },
  {
    id: "mistral",
    name: "Mistral AI",
    protocol: "openai-compatible",
    apiBase: "https://api.mistral.ai/v1",
    modelSuggestions: ["mistral-small-latest", "mistral-large-latest"],
    defaultEmbeddingModel: "mistral-embed",
    embeddingSupport: "same-endpoint",
    requiresApiKey: true,
  },
  {
    id: "groq",
    name: "Groq",
    protocol: "openai-compatible",
    apiBase: "https://api.groq.com/openai/v1",
    modelSuggestions: [],
    defaultEmbeddingModel: "",
    embeddingSupport: "not-available",
    requiresApiKey: true,
  },
  {
    id: "xai",
    name: "xAI",
    protocol: "openai-compatible",
    apiBase: "https://api.x.ai/v1",
    modelSuggestions: ["grok-4", "grok-3-mini"],
    defaultEmbeddingModel: "",
    embeddingSupport: "not-available",
    requiresApiKey: true,
  },
  {
    id: "siliconflow",
    name: "SiliconFlow",
    protocol: "openai-compatible",
    apiBase: "https://api.siliconflow.com/v1",
    modelSuggestions: [],
    defaultEmbeddingModel: "BAAI/bge-m3",
    embeddingSupport: "same-endpoint",
    requiresApiKey: true,
  },
  {
    id: "minimax",
    name: "MiniMax",
    protocol: "openai-compatible",
    apiBase: "https://api.minimaxi.com/v1",
    modelSuggestions: [],
    defaultEmbeddingModel: "",
    embeddingSupport: "not-available",
    requiresApiKey: true,
  },
  {
    id: "ollama",
    name: "Ollama",
    protocol: "openai-compatible",
    apiBase: "http://127.0.0.1:11434/v1",
    modelSuggestions: [],
    defaultEmbeddingModel: "",
    embeddingSupport: "same-endpoint",
    requiresApiKey: false,
  },
  {
    id: "custom",
    name: "Custom endpoint",
    protocol: "openai-compatible",
    apiBase: "",
    modelSuggestions: [],
    defaultEmbeddingModel: "",
    embeddingSupport: "not-available",
    requiresApiKey: true,
  },
] as const;

export function researchModelProvider(
  providerId: string,
): ResearchModelProviderPreset {
  return (
    RESEARCH_MODEL_PROVIDERS.find((provider) => provider.id === providerId) ??
    RESEARCH_MODEL_PROVIDERS[RESEARCH_MODEL_PROVIDERS.length - 1]
  );
}
