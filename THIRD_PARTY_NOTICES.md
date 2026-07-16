# Third-party notices

Spark Agent is an independent derivative based on Open Science Desktop v0.1.9
(`b328b51`), used under the MIT License. Spark Agent is maintained and
distributed independently. The original copyright and license notice is
retained in `LICENSE`.

Major reused or bundled components include:

- Open Science Desktop — MIT; source: <https://github.com/ai4s-research/open-science>
- OpenScience research-agent design — Apache-2.0; source:
  <https://github.com/synthetic-sciences/openscience>, reference revision
  `e9844a49f1f4d93cbf5f88b8f4880c003adc6e61`. Spark's bundled Agent prompts are
  modified adaptations with Spark-specific wording. The eight Foundation skills
  are behavior-only Spark implementations; no upstream skill file is copied or
  substantially adapted. Atlas, wallet, managed-model, proprietary service, and
  mandatory cloud-GPU behaviors are excluded. A copy of the upstream license and
  notice is retained under `runtime/opencode-profile/`.
- ai4s-skills — MIT; its license is copied into the bundled skill resource.
- OpenCode — MIT; source: <https://github.com/anomalyco/opencode>
- uv — Apache-2.0 or MIT; source: <https://github.com/astral-sh/uv>
- PaperQA2 — Apache-2.0; source: <https://github.com/Future-House/paper-qa>
- Jupyter nbclient — BSD-3-Clause; source: <https://github.com/jupyter/nbclient>

This notice lists major product-level integrations. Package lockfiles and
container manifests provide the complete dependency graph. Each dependency
remains subject to its own license terms.

OpenScience attribution notice retained for the adapted Agent design:

> OpenScience
>
> Copyright 2026 Synthetic Sciences
>
> This product includes software developed at Synthetic Sciences.
>
> Licensed under the Apache License, Version 2.0.
