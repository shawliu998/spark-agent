import type { AgentLoopJsonValue } from "@spark/research-domain";

export function formatAgentLoopValue(value: AgentLoopJsonValue): string {
  if (value === null) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}
