---
name: exploratory-data-analysis
description: Use when first examining a scientific dataset or when results require diagnostic exploration. Builds a bounded, reproducible data profile covering schema, quality, distributions, missingness, relationships, groups, anomalies, and candidate follow-up analyses without presenting exploration as confirmation.
---

# Exploratory data analysis

Explore the data before selecting a confirmatory method. EDA discovers structure
and problems; it does not turn patterns found after looking into preregistered
hypotheses.

## Start safely

1. Locate the source file and read its documentation, license, collection method,
   unit of observation, time range, population, and known exclusions.
2. For a potentially large file, use the `large-file` skill before reading it.
   Inspect schema and bounded samples instead of loading unknown data wholesale.
3. Preserve the source. Write cleaned or derived data to a new path and record
   every transformation in code.
4. Identify identifiers, outcomes, predictors, groups, timestamps, units,
   categorical levels, missing-value encodings, censoring, and repeated measures.

## Profile

Compute row/column counts, dtypes, ranges, quantiles, unique counts, missingness,
duplicates, impossible values, and group/sample balance. Check whether units and
coordinate systems are consistent. Inspect distributions with robust summaries,
not only means and standard deviations.

Visualize univariate distributions, missingness, pairwise relationships, group
differences, and time or spatial structure as appropriate. Use transformations
only with a scientific rationale and show the original scale too. Flag outliers;
do not delete them without a documented rule and sensitivity analysis.

Look for leakage, duplicate subjects, batch/site effects, temporal drift,
selection effects, label imbalance, collinearity, and non-independence. Compare
patterns across meaningful strata. Do not mine every pairwise association and
report only the attractive ones.

## Deliverables

Create a reproducible profiling script or notebook plus:

- a data dictionary with inferred and documented meanings;
- a quality report with counts and concrete examples;
- a small set of diagnostic figures with units and captions;
- candidate questions and methods labeled as exploratory;
- decisions that require domain knowledge or user confirmation.

End with what the data can support, what it cannot support, and the smallest
next analysis. Fix random seeds for sampled plots or stochastic profiling.
