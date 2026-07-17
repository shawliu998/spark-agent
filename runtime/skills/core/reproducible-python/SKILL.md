---
name: reproducible-python
description: Use to build a project-local Python analysis with deterministic inputs, explicit dependencies, captured commands, and inspectable artifacts.
---

# Reproducible Python

Keep source data unchanged and write `scripts/analysis.py` or a notebook under
the project. Prefer the managed environment, fixed seeds, relative project
paths, and small dependency sets. Run the program from a clean working
directory, retain stderr and failed attempts when useful, and verify every
reported table, figure, and report exists after execution.
