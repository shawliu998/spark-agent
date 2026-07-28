# Third-party notices

Spark Agent is an independent derivative based on Open Science Desktop v0.1.9
(`b328b51`), used under the MIT License. Spark Agent is maintained and
distributed independently. The original copyright and license notice is
retained in `LICENSE`.

Major reused or bundled components include:

- Open Science Desktop — MIT; source: <https://github.com/ai4s-research/open-science>
- ai4s-skills — MIT; its license is copied into the bundled skill resource.
- OpenCode — MIT; source: <https://github.com/anomalyco/opencode>
- uv — Apache-2.0 or MIT; source: <https://github.com/astral-sh/uv>
- PaperQA2 — Apache-2.0; source: <https://github.com/Future-House/paper-qa>
- paper-search-mcp — MIT; Spark ships a hash-bound modified fork of 0.1.4 with
  its upstream source archive, patch, license, and provenance manifest under
  `services/science-core/vendor/paper-search-mcp/`; upstream source:
  <https://github.com/openags/paper-search-mcp>
- sgmllib3k — BSD License; Spark ships a deterministic pure-Python wheel built
  from the official PyPI sdist, with upstream metadata and provenance, under
  `services/science-core/vendor/sgmllib3k/`; upstream source:
  <https://pypi.org/project/sgmllib3k/>
- Jupyter nbclient — BSD-3-Clause; source: <https://github.com/jupyter/nbclient>

This notice lists major product-level integrations. Release artifacts include
deterministic SPDX 2.3 inventories of the complete locked Python
application-dependency graph selected for each Linux target, bound to the
corresponding image ID, architecture, and Docker archive digest. These
inventories do not claim to be operating-system or whole-container filesystem
SBOMs. Each dependency remains subject to its own license terms.
