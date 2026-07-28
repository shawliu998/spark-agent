#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CORE="$ROOT/services/science-core"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$ROOT/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

TESTS=(
  "tests/test_discovery_api.py::test_combined_discovery_golden_e2e_persists_agent_choice_stop_and_restart"
  "tests/test_discovery_handler.py::test_discovery_selection_uses_coverage_recovers_and_stops_without_expansion"
  "tests/test_discovery_handler.py::test_worker_recovery_marks_pending_discovery_outcome_unknown_without_replay"
  "tests/test_discovery_adapter.py::test_candidates_are_locally_reranked_by_query_coverage"
  "tests/test_evidence_coverage.py"
)

on_error() {
  local exit_code=$?
  printf '\n<== FAIL: Agent v1.3 Discovery evaluation (pytest exit %s)\n' "$exit_code" >&2
  exit "$exit_code"
}
trap on_error ERR

printf '==> Agent v1.3 Discovery evaluation\n'
printf '    Mode: offline, deterministic repository fixtures\n'
printf '    Claim boundary: validates the approved dual-source loop, adaptive selection, recovery, reranking, and structural evidence coverage; it does not measure scientific quality.\n'
printf '    Python: %s\n' "$PYTHON_BIN"
printf '    Test scope:\n'
printf '      - %s\n' "${TESTS[@]}"

(
  cd "$CORE"
  NO_PROXY="*" no_proxy="*" "$PYTHON_BIN" -m pytest -q "${TESTS[@]}"
)

trap - ERR
printf '\n<== PASS: Agent v1.3 Discovery evaluation (%s fixed test targets)\n' "${#TESTS[@]}"
