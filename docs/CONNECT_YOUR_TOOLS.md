# Connect your lab tools

Spark Agent is a workbench: it aggregates tools, it doesn't replace them.
Anything the agent can reach is an **MCP server** (Model Context Protocol) or a
**skill**. Both are pluggable — you don't touch app code to add one.

## One-click science connectors

Settings → **MCP servers** lists curated open-source connectors. For enabled
connectors, **Enable** provisions the server into a managed environment (bundled
`uv`, managed Python — your system is untouched) and registers it. Credential-free
connectors are available today; credential-bearing Materials Project and
FRED entries remain visible but security-gated and fail closed:

- **Literature search** (all fields) — arXiv, PubMed, Crossref, Semantic Scholar,
  bioRxiv/medRxiv ([paper-search-mcp](https://github.com/openags/paper-search-mcp)).
- **Biomedical databases** (biology) — PubMed, ClinicalTrials.gov, genomic variants
  ([biomcp](https://github.com/genomoncology/biomcp)).
- **Materials Project** (materials; security-gated) — properties, structures,
  phase diagrams
  ([mcp-materials-project](https://github.com/luffysolution-svg/mcp-materials-project); free MP API key).
- **FRED economic data** (economics; security-gated) — Federal Reserve time
  series ([fred-mcp](https://github.com/tosin2013/fred-mcp); free FRED API key).
- **Space weather** (physics) — solar wind, flares, Kp/Dst indices, radiation
  storms, aurora, from NOAA SWPC / NASA DONKI / USGS
  ([spaceweather-mcp](https://github.com/hoon1983/spaceweather-mcp); no key).
- **Weather & climate** (earth) — current & historical weather, air quality,
  timezones from Open-Meteo
  ([mcp-weather-server](https://github.com/isdaniel/mcp_weather_server); no key).
- **USGS water data** (earth) — streamflow, flood stages, peak events, sites
  ([usgs-mcp](https://github.com/mansurjisan/ocean-mcp); no key).

Literature and database results carry real identifiers (DOI / PMID / arXiv id),
so the `traceability-review` skill can audit them afterward.

## App-managed Jupyter

Settings can provision the app-managed Jupyter environment for local JupyterLab
and in-app Python-kernel use. This does **not** enable an agent MCP connection.
Agent Jupyter MCP is security-gated and no managed registration command is exposed until a
secretless broker and the target-integrity, per-call-approval, config-dependency,
hashed-atomic-install, and packaged-macOS-E2E gates below are complete.

Native reconciliation runs before OpenCode starts. It rotates any exposed legacy
`server.json` v1 token, stores the fresh replacement in the OS credential manager,
and rewrites `server.json` v2 with only `version` and `port`. This native boundary
runs for direct runtime starts even when the MCP entry is absent or user-owned. It
also scrubs legacy Spark-owned Jupyter MCP config
that embedded URL/token material; an unrelated custom MCP entry with the same name
is not silently overwritten. Renderer status contains no token, URL, or executable
command. A controlled ServerApp suppresses token-bearing server-info/browser-open
files, removes legacy copies from Spark's private runtime, and rotates the token if
one is found. Readiness verifies that the new child PID owns the loopback listener
without sending a token probe. JupyterLab receives its token through its child
environment instead of a process argument, and macOS opens the native-built URL
through NSWorkspace. The local Lab environment excludes the gated Agent MCP and
collaboration packages and does not auto-discover unrelated server extensions.

## Bring your own MCP server

Any MCP server works — internal ELN, LIMS, a database gateway, an instrument
bridge. In Settings → **MCP servers**, use the add form:

- **local** — a command the app launches and talks to over stdio. Example:
  `npx -y @playwright/mcp` (browser), or `uvx your-lab-mcp` for a Python server.
- **remote** — a URL the app connects to over HTTP. Example:
  `https://mcp.your-lab.internal/sse`.

The entry is written to the bundled OpenCode's config and applies immediately;
its live status (connected / failed) shows in the same list.

### Minimal local MCP server (Python)

```python
# lab_tools.py — run with: uvx --from fastmcp fastmcp run lab_tools.py
from fastmcp import FastMCP

mcp = FastMCP("lab-tools")

@mcp.tool()
def sample_metadata(sample_id: str) -> dict:
    """Look up a sample in the lab database."""
    return {"id": sample_id, "assay": "RNA-seq", "status": "passed_qc"}

if __name__ == "__main__":
    mcp.run()
```

Add it as a **local** server with the command that launches it. Restart-free.

## Bring your own skill

A skill is a folder with a `SKILL.md` (instructions the agent follows) plus any
scripts/templates it needs. Install one from the **Skills** page (paste a URL or
Markdown; the agent saves it under the workspace's `.opencode/skills/`). The
app also bundles first-party skills (e.g. `traceability-review`) and the
`ai4s-skills` pack.

## Safety

- Every server you add can make its own network calls and run its own code —
  review the source before enabling. The curated list is vetted; your own
  entries are your responsibility.
- Agent-initiated command execution, file deletion, dependency installs, and
  remote connections go through the approval flow. The pinned OpenCode loader
  can still install config-directory dependencies before tool approval; gating
  or disabling that network/write path remains release-blocking work.
- Simple provider API keys use the OS credential manager at rest and are supplied
  to the OpenCode sidecar at runtime; an approved local tool can still inherit
  those provider secrets.
- Spark-managed Materials Project/FRED key migration and private-broker
  infrastructure are implemented, but credential-bearing execution is disabled
  by default and fails closed. The legacy DYLD-sensitive Spark launcher is gone.
  Disabled native config uses only Apple platform-signed
  `/usr/bin/nc -U <private-socket>` as a stdio relay to a private Unix-domain
  socket. The staged Tauri broker authenticates relay UID/PID/executable/parent
  against the owned OpenCode PID/start-time/generation and validates the strict
  config and canonical target. This is staged defense in depth, not a delivered
  key-delivery guarantee or hard confinement; MP/FRED are not available to the
  runtime while the gate is closed. Custom/BYO MCP credential custody is outside
  this boundary.
- **P0 release gate:** make every downloaded credential-bearing target immutable
  and signed/verified, or execute it in isolation from same-UID mutation; require
  native approval for every broker call and close the OpenCode config-dependency
  installation path that currently bypasses tool approval.
- **P1 supply-chain gate:** enforce a fully hashed transitive lock and staged,
  atomic installation. Exact-pinned top-level packages, a cleared caller
  environment, disabled `uv` configuration, and official PyPI are useful but
  insufficient.
- **P1 validation gate:** pass packaged macOS E2E for migration, default denial,
  broker lineage/revocation, target verification, atomic install, and restart.
- OAuth records still use an owner-only app-private file. Spark's persistent copy
  of the app-managed Jupyter token is in the OS credential manager, but that is not
  complete custody: the child environment during startup and the browser token
  URL/history remain exposure surfaces, while same-UID listener replacement/
  introspection and execution-time isolation are open. Agent Jupyter MCP therefore
  remains unavailable.
- Broader execution-time secret isolation and hard confinement remain release-
  blocking work.
