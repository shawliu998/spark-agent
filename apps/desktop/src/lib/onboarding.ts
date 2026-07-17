export type OnboardingStep = "welcome" | "model" | "project" | "complete";

const KEY = "spark.onboarding.v1";

export function readOnboardingStep(): OnboardingStep {
  if (typeof window === "undefined") return "welcome";
  const step = window.localStorage.getItem(KEY);
  return step === "model" || step === "project" || step === "complete" ? step : "welcome";
}

export function saveOnboardingStep(step: OnboardingStep): void {
  if (typeof window !== "undefined") window.localStorage.setItem(KEY, step);
}
