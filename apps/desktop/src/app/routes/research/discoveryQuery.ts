const QUESTION_FRAME_WORDS = new Set([
  "a",
  "an",
  "are",
  "can",
  "could",
  "did",
  "do",
  "does",
  "how",
  "is",
  "the",
  "was",
  "were",
  "what",
  "when",
  "where",
  "which",
  "who",
  "why",
  "would",
]);

const TERM_NORMALIZATIONS = new Map([
  ["evaluate", "evaluation"],
  ["evaluated", "evaluation"],
  ["evaluates", "evaluation"],
  ["evaluating", "evaluation"],
  ["hallucinations", "hallucination"],
]);

/**
 * Converts a natural-language research question into a compact, deterministic
 * OpenAlex search expression. The result is persisted in the immutable
 * discovery specification and remains visible during plan approval.
 */
export function buildOpenAlexSearchQuery(question: string): string {
  const normalizedPhrases = question
    .replace(/\bin\s+scientific\s+research\b/giu, " ")
    .replace(/\blarge\s+language\s+models?\b/giu, " LLM ");
  const rawTerms = normalizedPhrases.match(/[\p{L}\p{N}]+(?:[-'][\p{L}\p{N}]+)*/gu) ?? [];
  const terms: string[] = [];
  const seen = new Set<string>();
  let asksHowEvaluated = false;

  for (const rawTerm of rawTerms) {
    const folded = rawTerm.toLocaleLowerCase();
    if (folded === "how") asksHowEvaluated = true;
    if (QUESTION_FRAME_WORDS.has(folded)) continue;
    const term = TERM_NORMALIZATIONS.get(folded) ?? rawTerm;
    const canonical = term.toLocaleLowerCase();
    if (seen.has(canonical)) continue;
    seen.add(canonical);
    terms.push(term);
  }

  if (asksHowEvaluated && seen.has("evaluation")) {
    if (!seen.has("methods")) terms.push("methods");
    if (!seen.has("benchmark")) terms.push("benchmark");
  }

  return terms.length >= 2
    ? terms.slice(0, 12).join(" ")
    : question.replace(/[?*]+/g, " ").replace(/\s+/g, " ").trim();
}
