#!/usr/bin/env bash
# Fetch the reviewed K-Dense subset. This only copies verified source files;
# it never runs upstream scripts or installs dependencies.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=sidecar-integrity.sh
source "$ROOT/scripts/dev/sidecar-integrity.sh"

PACK="kdense-scientific-agent-skills"
COMMIT="${KDENSE_SKILLS_COMMIT:-$(skills_pinned_commit "$PACK")}"
EXPECTED_SHA256="$(skills_archive_sha256 "$PACK" "$COMMIT")"
MANIFEST="$ROOT/runtime/skills/kdense-curated-manifest.json"
DEFAULT_OUT_DIR="$ROOT/runtime/skills/external/kdense-scientific-agent-skills"
if [[ -n "${KDENSE_SKILLS_OUT_DIR:-}" ]]; then
  if [[ "${SPARK_AGENT_TEST_ALLOW_SKILLS_OUT_DIR:-}" != 1 ]]; then
    echo "KDENSE_SKILLS_OUT_DIR is restricted to explicit test runs" >&2
    exit 1
  fi
  OUT_DIR="$KDENSE_SKILLS_OUT_DIR"
else
  OUT_DIR="$DEFAULT_OUT_DIR"
fi

python3 "$ROOT/scripts/dev/validate_kdense_skills.py" --manifest "$MANIFEST"
recover_directory_transaction "$OUT_DIR"
TMP="$(mktemp -d)"
STAGING=''
cleanup() {
  rm -rf "$TMP" || true
  [[ -z "$STAGING" ]] || rm -rf "$STAGING" || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

URL="https://codeload.github.com/K-Dense-AI/scientific-agent-skills/tar.gz/${COMMIT}"
echo "Downloading curated K-Dense source $URL"
curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fsSL "$URL" -o "$TMP/skills.tar.gz"
verify_sha256 "$TMP/skills.tar.gz" "$EXPECTED_SHA256"

OUT_PARENT="$(dirname "$OUT_DIR")"
OUT_NAME="$(basename "$OUT_DIR")"
mkdir -p "$OUT_PARENT"
STAGING="$(mktemp -d "$OUT_PARENT/.${OUT_NAME}.staging.$$.XXXXXX")"
rm -rf "$STAGING"
python3 "$ROOT/scripts/dev/extract_kdense_skills.py" "$TMP/skills.tar.gz" "$MANIFEST" "$STAGING"
python3 "$ROOT/scripts/dev/validate_kdense_skills.py" --manifest "$MANIFEST" --pack "$STAGING"
install_directory_transactionally "$STAGING" "$OUT_DIR"
STAGING=''
echo "Placed 30 curated K-Dense skills at $OUT_DIR"
