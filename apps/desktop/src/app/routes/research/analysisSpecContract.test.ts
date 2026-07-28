import { describe, expect, expectTypeOf, it } from "vitest";
import type {
  AnalysisOperation,
  AnalysisSpec,
  CompiledAnalysis,
  ScientificClarificationProposal,
  StructuredAnalysisResult,
  UnsupportedAnalysis,
} from "@spark/research-domain";

const DATASET_HASH = "a".repeat(64);
const PROFILE_HASH = "b".repeat(64);

const analysisSpec = {
  schemaVersion: "1",
  objective: "Compare the primary outcome between treatment groups.",
  datasetSourceId: "dataset-1",
  datasetContentHash: DATASET_HASH,
  datasetProfileHash: PROFILE_HASH,
  operation: {
    type: "two-group-comparison",
    outcomeColumn: "score",
    groupColumn: "arm",
    groups: ["control", "treatment"],
    method: "welch-t-test",
    effectSize: "hedges-g",
    checkAssumptions: true,
    plot: "boxplot",
  },
  missingValuePolicy: "drop-per-operation",
  confidenceLevel: 0.95,
  randomSeed: 42,
  assumptions: ["Observations are independent."],
  limitations: [],
} satisfies AnalysisSpec;

const clarification = {
  reason: "The outcome column is ambiguous.",
  requests: [
    {
      type: "outcome-column",
      question: "Which column contains the primary outcome?",
      options: [
        {
          value: "score",
          label: "Score",
          description: null,
        },
      ],
    },
  ],
} satisfies ScientificClarificationProposal;

const unsupported = {
  capability: "survival-analysis",
  explanation: "Survival analysis is not supported by this compiler version.",
  supportedAlternatives: ["descriptive", "two-group-comparison"],
} satisfies UnsupportedAnalysis;

const compiled = {
  compilerVersion: "analysis-spec-compiler-v1",
  specSha256: "c".repeat(64),
  code: "print('approved analysis')",
  codeSha256: "d".repeat(64),
  expectedOutputs: ["structured-analysis-result"],
  runtimePolicyId: "analysis-runtime-v1",
} satisfies CompiledAnalysis;

const structuredResult = {
  schemaVersion: "1",
  objective: analysisSpec.objective,
  operationType: "two-group-comparison",
  datasetSourceId: analysisSpec.datasetSourceId,
  datasetContentHash: DATASET_HASH,
  datasetProfileHash: PROFILE_HASH,
  requestedMethod: "welch-t-test",
  resolvedMethod: "welch-t-test",
  methodSelectionReason: "The explicit Welch t-test request was supported.",
  sampleSummary: {
    totalRows: 20,
    analyzedRows: 18,
    missingRows: 2,
  },
  result: {
    type: "two-group-comparison",
    groupColumn: "arm",
    outcomeColumn: "score",
    groups: ["control", "treatment"],
    sampleSizes: { control: 9, treatment: 9 },
    missingCounts: { control: 1, treatment: 1 },
    descriptiveStatistics: {
      control: { mean: 10.2, std: 1.1 },
      treatment: { mean: 12.4, std: 1.3 },
    },
    testStatistic: -3.88,
    pValue: 0.0013,
    effectSizeName: "hedges-g",
    effectSize: 1.72,
    confidenceInterval: [0.74, 2.69],
  },
  warnings: [],
  limitations: ["The sample is small."],
} satisfies StructuredAnalysisResult;

describe("analysis spec domain contract", () => {
  it("keeps the analysis operation discriminant and method literals", () => {
    expectTypeOf(analysisSpec.operation).toMatchTypeOf<AnalysisOperation>();
    expect(analysisSpec.operation).toMatchObject({
      type: "two-group-comparison",
      method: "welch-t-test",
      effectSize: "hedges-g",
    });
  });

  it("mirrors clarification, unsupported, and compiler camelCase payloads", () => {
    expect(clarification.requests[0]?.type).toBe("outcome-column");
    expect(clarification.requests[0]?.options[0]?.description).toBeNull();
    expect(unsupported.supportedAlternatives).toContain("descriptive");
    expect(compiled.runtimePolicyId).toBe("analysis-runtime-v1");
  });

  it("binds structured result method, effect size, and operation literals", () => {
    expectTypeOf(structuredResult).toMatchTypeOf<StructuredAnalysisResult>();
    expect(structuredResult.operationType).toBe(structuredResult.result.type);
    expect(structuredResult.resolvedMethod).toBe("welch-t-test");
    expect(structuredResult.result.effectSizeName).toBe("hedges-g");
  });
});
