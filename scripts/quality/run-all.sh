#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHECK="$ROOT/scripts/quality/run-check.sh"

run_step() {
  local label="$1"
  shift

  printf '\n######################################################################\n'
  printf 'QUALITY STEP: %s\n' "$label"
  printf '######################################################################\n'
  if "$@"; then
    printf 'QUALITY PASS: %s\n' "$label"
  else
    local status=$?
    printf 'QUALITY FAILED: %s (exit %d)\n' "$label" "$status" >&2
    return "$status"
  fi
}

run_step "desktop lint" bash "$CHECK" desktop lint
run_step "desktop typecheck" bash "$CHECK" desktop typecheck
run_step "desktop tests" bash "$CHECK" desktop test
run_step "Rust formatting" bash "$CHECK" rust fmt
run_step "Rust lint" bash "$CHECK" rust lint
run_step "Rust tests" bash "$CHECK" rust test
run_step "science-core lint" bash "$CHECK" core lint
run_step "science-core typecheck" bash "$CHECK" core typecheck
run_step "science-core tests" bash "$CHECK" core test
run_step "science-runtime lint" bash "$CHECK" runtime lint
run_step "science-runtime typecheck" bash "$CHECK" runtime typecheck
run_step "science-runtime tests" bash "$CHECK" runtime test
run_step "Docker integration smoke" bash "$ROOT/scripts/quality/docker-smoke.sh"

printf '\nAll quality checks passed.\n'
