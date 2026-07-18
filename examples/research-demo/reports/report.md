# Synthetic Research Demo

This report summarizes a fully synthetic dataset generated to exercise the Spark Agent research workflow.
All inputs, outputs, and conclusions in this project are synthetic and should not be interpreted as scientific evidence.

## Method

The analysis script reads `data/raw/synthetic_observations.csv`
and computes deterministic per-condition summary statistics plus a simple least-squares slope.

## Results

| condition | n | mean | min | max | slope/day |
| --- | ---: | ---: | ---: | ---: | ---: |
| baseline | 8 | 10.000 | 10.000 | 10.000 | 0.000 |
| increasing | 8 | 9.750 | 8.000 | 11.500 | 0.500 |
| decreasing | 8 | 10.600 | 9.200 | 12.000 | -0.400 |

## Interpretation

The numbered lines in the figure map to the conditions below: 1 = baseline, 2 = increasing, 3 = decreasing.
The baseline series is flat by construction, the increasing series rises by 0.5 units per day, and the decreasing series falls by 0.4 units per day.
Those patterns demonstrate file generation, plotting, and report writing, not a real scientific effect.

## Artifacts

- Figure: `figures/synthetic_condition_trends.png`
- Summary CSV: `reports/summary.csv`
- Notebook: `notebooks/analysis.ipynb`
- Bibliography: `references/references.bib` (intentionally empty because no real literature identifiers were used)
