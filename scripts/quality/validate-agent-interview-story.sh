#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CORE="$ROOT/services/science-core"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

on_error() {
  local exit_code=$?
  printf '\n<== FAIL: Spark Agent interview story (exit %s)\n' "$exit_code" >&2
  exit "$exit_code"
}
trap on_error ERR

printf '==> Spark Agent interview story\n'
printf '    Claim boundary: validates a deterministic product story; it does not measure scientific quality or general autonomous planning.\n'

printf '\n--> Public data provenance\n'
"$PYTHON_BIN" \
  "$ROOT/examples/climate-trends/scripts/prepare_analysis_csv.py" \
  --check

printf '\n--> Durable Research Agent loop\n'
(
  cd "$CORE"
  NO_PROXY="*" no_proxy="*" "$PYTHON_BIN" -m pytest -q \
    "tests/test_discovery_api.py::test_combined_discovery_golden_e2e_persists_agent_choice_stop_and_restart" \
    "tests/test_discovery_api.py::test_discovery_get_is_bounded_text_only_and_reloads_latest_attempt" \
    "tests/test_agent_runs.py::test_autonomous_local_literature_completes_through_durable_agent_loop"
)

printf '\n--> Product interaction contract\n'
(
  cd "$ROOT"
  pnpm --filter @ai4s/desktop exec vitest run \
    "src/app/routes/ResearchPage.test.tsx" \
    "src/app/routes/research/CompetitiveResearchWorkspace.test.tsx" \
    "src/app/routes/research/agent-loop/AgentLoopUi.test.tsx" \
    "src/app/routes/research/agentLoopContract.test.ts"
)

trap - ERR
printf '\n<== PASS: deterministic backend and UI contract suites for the interview story (not one packaged end-to-end run)\n'
