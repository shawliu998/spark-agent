#!/usr/bin/env bash
# Fetch the pinned OpenCode binary and place it as a Tauri sidecar
# (apps/desktop/src-tauri/binaries/opencode-<target-triple>).
# Runs per-platform locally and in CI so the binary never lives in git.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=sidecar-integrity.sh
source "$ROOT/scripts/dev/sidecar-integrity.sh"
OPENCODE_VERSION="${OPENCODE_VERSION:-$(sidecar_pinned_version opencode)}"
OUT_DIR="$ROOT/apps/desktop/src-tauri/binaries"

# Resolve the Rust target triple (arg 1 overrides; else host), then obtain the
# archive and digest together from the single release manifest.
TRIPLE="${1:-$(rustc -Vv | sed -n 's/host: //p')}"
RESOLVED_SIDECAR="$(resolve_sidecar opencode "$OPENCODE_VERSION" "$TRIPLE")"
IFS='|' read -r ASSET EXPECTED_SHA256 EXPECTED_BINARY_SHA256 <<<"$RESOLVED_SIDECAR"

URL="https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/${ASSET}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "Downloading $URL"
curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fsSL "$URL" -o "$TMP/$ASSET"
verify_sha256 "$TMP/$ASSET" "$EXPECTED_SHA256"
case "$ASSET" in
  *.tar.gz) tar -xzf "$TMP/$ASSET" -C "$TMP" ;;
  *)
    if command -v unzip >/dev/null 2>&1; then
      unzip -oq "$TMP/$ASSET" -d "$TMP"
    else
      tar -xf "$TMP/$ASSET" -C "$TMP"   # bsdtar (macOS/Windows) extracts zip
    fi
    ;;
esac

# The archive contains an `opencode` (or opencode.exe) binary.
if [ -f "$TMP/opencode.exe" ]; then
  BIN="$TMP/opencode.exe"
  DESTINATION="$OUT_DIR/opencode-$TRIPLE.exe"
else
  BIN="$(find "$TMP" -type f -name opencode -print -quit)"
  [ -n "$BIN" ] || { echo "No opencode binary in archive" >&2; exit 1; }
  DESTINATION="$OUT_DIR/opencode-$TRIPLE"
fi
verify_sha256 "$BIN" "$EXPECTED_BINARY_SHA256"
install_sidecar_atomically "$BIN" "$DESTINATION"
echo "Placed sidecar for $TRIPLE in $OUT_DIR"
