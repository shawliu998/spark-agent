#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIVE=false
if [[ "${1:-}" == "--live" ]]; then
  LIVE=true
elif [[ $# -gt 0 ]]; then
  printf 'usage: %s [--live]\n' "$0" >&2
  exit 2
fi

run_step() {
  local label="$1"
  shift

  printf '\n==> %s\n' "$label"
  if "$@"; then
    printf '<== PASS: %s\n' "$label"
  else
    local status=$?
    printf '<== FAIL: %s (exit %d)\n' "$label" "$status" >&2
    return "$status"
  fi
}

run_step "Foundation profile contract" \
  python3 -B "$ROOT/runtime/opencode-profile/tests/test_foundation_profile.py"
run_step "Foundation SDK, General-mode boundary, controls, artifacts, and desktop-store remount integration" \
  pnpm --dir "$ROOT" --filter @ai4s/desktop exec vitest run \
    src/test/opencode-client.node.test.ts \
    src/app/routes/GeneralResearchBoundary.test.ts \
    src/app/routes/FoundationDesktopRecovery.integration.test.tsx \
    src/components/thread/ResearchSessionControls.test.tsx \
    src/lib/workspaceArtifacts.test.ts \
    src/components/thread/WorkspaceArtifactShelf.test.tsx

if [[ "$LIVE" == true ]]; then
  run_step "Foundation live OpenCode research-agent/tool slice" \
    python3 -B "$ROOT/tests/foundation/live_opencode_smoke.py"
else
  printf '\nLive sidecar smoke skipped by the portable gate; run pnpm test:foundation:live explicitly.\n'
fi

printf '\nEvidence boundary: the portable UI test restarts the desktop store and remounts the page in jsdom; --live restarts the real OpenCode sidecar. Neither is packaged macOS process automation.\n'
printf '\nFoundation validation passed.\n'
