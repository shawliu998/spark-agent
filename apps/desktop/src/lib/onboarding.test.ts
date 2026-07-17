import { beforeEach, describe, expect, it } from "vitest";
import { readOnboardingStep, saveOnboardingStep } from "./onboarding";

describe("onboarding state", () => {
  beforeEach(() => window.localStorage.clear());

  it("starts at welcome and persists the completed path", () => {
    expect(readOnboardingStep()).toBe("welcome");
    saveOnboardingStep("model");
    expect(readOnboardingStep()).toBe("model");
    saveOnboardingStep("complete");
    expect(readOnboardingStep()).toBe("complete");
  });

  it("ignores malformed persisted values", () => {
    window.localStorage.setItem("spark.onboarding.v1", "verified-workflow");
    expect(readOnboardingStep()).toBe("welcome");
  });
});
