# Synthetic Research Demo

This demo is a deterministic, fully synthetic research project. It exists to
exercise the Spark Agent workflow and artifact discovery path without making any
real scientific claims.

## Prebuilt output

The checked-in outputs in this workspace were generated from
`scripts/analysis.py` over `data/raw/synthetic_observations.csv` and then
validated with `pnpm demo:research`.

Prebuilt files:

- `figures/synthetic_condition_trends.png`
- `reports/summary.csv`
- `reports/report.md`
- `references/references.bib`
- `notebooks/analysis.ipynb`

## Live Agent output

When a live Agent opens this project, it can rerun the same analysis script,
inspect the raw synthetic data, and regenerate the artifacts in place.

The live turn should be interpreted as a reproducible demo run, not as new
evidence about a real system. All conclusions in this project remain synthetic
and deterministic.

## Validation

Use:

```bash
pnpm demo:research
```

The command reruns the analysis and checks:

- project structure
- summary CSV consistency
- PNG validity
- Markdown report presence
- artifact discoverability
