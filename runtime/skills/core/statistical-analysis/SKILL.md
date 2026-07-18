---
name: statistical-analysis
description: Use for designing, executing, checking, or reporting statistical analyses. Selects methods from the sampling unit, design, outcome, dependence, and estimand; verifies assumptions; reports effect sizes and uncertainty; handles multiplicity and sensitivity; and avoids unsupported causal claims.
---

# Statistical analysis

Choose statistics from the research design and estimand, not from whichever test
returns the smallest p-value.

## Design first

State the unit of observation, population, sampling process, exposure or
intervention, outcome, comparison, estimand, repeated or clustered structure,
time order, censoring, missing-data mechanism, and planned subgroup analyses.
Distinguish exploratory from confirmatory work. If a preregistration or analysis
plan exists, read it before examining outcomes.

## Plan

1. Describe data quality and exclusions with counts.
2. Select a model/test appropriate to outcome type, dependence, design, and
   distribution. Prefer an interpretable baseline before a complex model.
3. Define effect-size scale, confidence or credible interval, alpha or decision
   rule, multiplicity family, diagnostics, and sensitivity analyses.
4. Justify sample size or report precision/power limitations. Never use observed
   post-hoc power as evidence for a null result.
5. Identify assumptions and specify what to do if they fail before choosing from
   alternatives.

## Execute and diagnose

Use a reproducible script with fixed seeds. Verify coding, reference levels,
units, missingness handling, and design matrix. Inspect residuals, influence,
linearity, variance, dependence, convergence, identifiability, and model fit as
applicable. For clustered, paired, longitudinal, survival, spatial, or repeated
data, model the dependence explicitly.

Correct or control multiplicity for the declared family of claims. Run robust
alternatives or sensitivity analyses when plausible choices affect conclusions.
Do not remove outliers solely because they weaken significance.

## Report

Report sample size, exclusions, estimate, effect size, uncertainty interval,
test/model statistic, degrees of freedom where applicable, exact p-value, model
and software, diagnostics, multiplicity handling, and limitations. A p-value is
not an effect size or the probability that a hypothesis is true. A non-significant
result is not proof of equivalence.

Use associational language unless randomization or a defensible identification
strategy supports causal interpretation. Run `stats-integrity` and relevant
domain checks before finalizing claims.
