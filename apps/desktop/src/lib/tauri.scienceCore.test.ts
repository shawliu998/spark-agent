import { describe, expect, it } from "vitest";
import {
  isTauri,
  retryScienceCore,
  saveScienceModelConfig,
  scienceModelConfig,
  scienceCoreConnection,
  scienceCoreStatus,
  stopScienceCore,
} from "./tauri";

describe("Science Core plain-browser bridge", () => {
  it("is an explicit no-op without loading Tauri IPC", async () => {
    expect(isTauri).toBe(false);
    await expect(scienceCoreStatus()).resolves.toBeNull();
    await expect(scienceCoreConnection()).resolves.toBeNull();
    await expect(retryScienceCore()).resolves.toBeNull();
    await expect(stopScienceCore()).resolves.toBeNull();
    await expect(scienceModelConfig()).resolves.toBeNull();
    await expect(
      saveScienceModelConfig({
        providerId: "custom",
        protocol: "openai-compatible",
        apiBase: "https://api.example.com/v1",
        llmModel: "research-model",
        embeddingModel: "",
        clearCredential: false,
      }),
    ).resolves.toBeNull();
  });
});
