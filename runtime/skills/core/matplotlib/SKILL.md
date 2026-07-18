---
name: matplotlib
description: Use when creating or revising static scientific figures with matplotlib or seaborn. Produces reproducible, accessible, publication-ready plots with correct encodings, units, uncertainty, captions, and saved artifacts, and verifies the rendered output before reporting it.
---

# Matplotlib scientific figures

Make a figure answer a scientific question. Choose the chart and encoding before
styling it, then use the bundled `publication-figures` skill for Spark's shared
palette and matplotlib style.

## Build reproducibly

Create figures in a script or notebook cell that reads traceable data and writes
to a deterministic workspace path. Fix seeds for jitter, bootstrap intervals, or
sampling. Use explicit figure/axes objects and close figures in batch jobs.

```python
from pathlib import Path
import matplotlib.pyplot as plt

out = Path("artifacts/figures")
out.mkdir(parents=True, exist_ok=True)
fig, ax = plt.subplots(figsize=(6.5, 4.0), constrained_layout=True)
# Plot real data here.
ax.set(xlabel="Time (s)", ylabel="Response (a.u.)")
fig.savefig(out / "response.png", dpi=300, bbox_inches="tight")
fig.savefig(out / "response.svg", bbox_inches="tight")
plt.close(fig)
```

## Scientific encoding

- Use position and length for quantitative comparisons before area or color.
- Show distributions or individual observations when aggregation would hide
  sample size, skew, or heterogeneity.
- Define every uncertainty band or error bar (SD, SE, CI, credible interval, or
  bootstrap interval) and state `n` and the sampling unit.
- Use logarithmic axes only with a valid domain and label them clearly. Do not
  connect unordered categories or interpolate across missing observations.
- Avoid dual axes, rainbow maps, 3-D decoration, truncated axes that exaggerate
  differences, and color as the sole carrier of meaning.
- For heatmaps and images, include a labeled colorbar, meaningful normalization,
  scale bar or spatial units, and an accessible colormap.
- Use consistent category colors across figures and direct labels where they
  reduce legend lookup.

## Verify

Open the saved output and check clipping, font size, contrast, labels, units,
legend order, panel letters, raster resolution, and agreement with the source
data. Write a self-contained caption describing the population, encoding,
uncertainty, and statistical annotations. Return the script and figure paths;
never claim a figure was generated until the files exist.
