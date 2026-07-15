#!/usr/bin/env bash
# Fetch the pinned external skill packs into runtime/skills/external/
# (git-ignored; bundled into the installer as Tauri resources).
# Runs locally and in CI so the skills never live in this repo's git history.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=sidecar-integrity.sh
source "$ROOT/scripts/dev/sidecar-integrity.sh"

# ---- ai4s-skills: the default scientific pack ----
AI4S_SKILLS_COMMIT="${AI4S_SKILLS_COMMIT:-$(skills_pinned_commit ai4s-skills)}"
EXPECTED_SHA256="$(skills_archive_sha256 ai4s-skills "$AI4S_SKILLS_COMMIT")"
DEFAULT_OUT_DIR="$ROOT/runtime/skills/external/ai4s-skills"
if [[ -n "${AI4S_SKILLS_OUT_DIR:-}" ]]; then
  if [[ "${SPARK_AGENT_TEST_ALLOW_SKILLS_OUT_DIR:-}" != 1 ]]; then
    echo "AI4S_SKILLS_OUT_DIR is restricted to explicit test runs" >&2
    exit 1
  fi
  OUT_DIR="$AI4S_SKILLS_OUT_DIR"
else
  OUT_DIR="$DEFAULT_OUT_DIR"
fi
OUT_PARENT="$(dirname "$OUT_DIR")"
OUT_NAME="$(basename "$OUT_DIR")"

# Normalize any interrupted prior swap before downloading new content. The
# recovery path only restores the previous accepted tree or discards abandoned
# candidates; it never promotes stale staging.
recover_directory_transaction "$OUT_DIR"

URL="https://codeload.github.com/ai4s-research/ai4s-skills/tar.gz/${AI4S_SKILLS_COMMIT}"
TMP="$(mktemp -d)"
STAGING=''
cleanup() {
  rm -rf "$TMP" || true
  if [[ -n "$STAGING" ]]; then
    rm -rf "$STAGING" || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
echo "Downloading $URL"
curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fsSL \
  "$URL" -o "$TMP/skills.tar.gz"
verify_sha256 "$TMP/skills.tar.gz" "$EXPECTED_SHA256"
tar -xzf "$TMP/skills.tar.gz" -C "$TMP"

SRC="$TMP/ai4s-skills-$AI4S_SKILLS_COMMIT"
[ -d "$SRC/skills" ] || { echo "No skills/ directory in archive" >&2; exit 1; }
[ -f "$SRC/LICENSE" ] || { echo "No root LICENSE in ai4s-skills archive" >&2; exit 1; }

mkdir -p "$OUT_PARENT"
STAGING="$(mktemp -d "$OUT_PARENT/.${OUT_NAME}.staging.$$.XXXXXX")"
cp -R "$SRC/skills/." "$STAGING/"
diff -qr "$SRC/skills" "$STAGING" >/dev/null || {
  echo "Staged skills differ from the verified archive" >&2
  exit 1
}
cp "$SRC/LICENSE" "$STAGING/LICENSE"
cmp -s "$SRC/LICENSE" "$STAGING/LICENSE" || {
  echo "Staged skills LICENSE differs from the verified archive" >&2
  exit 1
}
printf '%s\n' "$AI4S_SKILLS_COMMIT" >"$STAGING/.commit"

install_directory_transactionally "$STAGING" "$OUT_DIR"
STAGING=''

echo "Placed ai4s-skills@${AI4S_SKILLS_COMMIT:0:7} in $OUT_DIR:"
ls "$OUT_DIR"
