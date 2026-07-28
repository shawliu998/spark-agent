#!/usr/bin/env bash
# Fetch the pinned uv binary as a Tauri sidecar
# (apps/desktop/src-tauri/binaries/uv-<target-triple>). uv provisions the
# isolated Jupyter environment for the Jupyter MCP integration on demand.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=sidecar-integrity.sh
source "$ROOT/scripts/dev/sidecar-integrity.sh"
UV_VERSION="${UV_VERSION:-$(sidecar_pinned_version uv)}"
OUT_DIR="$ROOT/apps/desktop/src-tauri/binaries"

TRIPLE="${1:-$(rustc -Vv | sed -n 's/host: //p')}"
RESOLVED_SIDECAR="$(resolve_sidecar uv "$UV_VERSION" "$TRIPLE")"
IFS='|' read -r ASSET EXPECTED_SHA256 EXPECTED_BINARY_SHA256 <<<"$RESOLVED_SIDECAR"

URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${ASSET}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "Downloading $URL"
curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fsSL "$URL" -o "$TMP/$ASSET"
verify_sha256 "$TMP/$ASSET" "$EXPECTED_SHA256"
case "$ASSET" in
  *.tar.gz) tar -xzf "$TMP/$ASSET" -C "$TMP" ;;
  *.zip) unzip -oq "$TMP/$ASSET" -d "$TMP" ;;
esac

if [ -f "$TMP/uv.exe" ] || find "$TMP" -type f -name uv.exe -print -quit | grep -q .; then
  BIN="$(find "$TMP" -type f -name uv.exe -print -quit)"
  DESTINATION="$OUT_DIR/uv-$TRIPLE.exe"
else
  BIN="$(find "$TMP" -type f -name uv -print -quit)"
  [ -n "$BIN" ] || { echo "No uv binary in archive" >&2; exit 1; }
  DESTINATION="$OUT_DIR/uv-$TRIPLE"
fi
verify_sha256 "$BIN" "$EXPECTED_BINARY_SHA256"
install_sidecar_atomically "$BIN" "$DESTINATION"
echo "Placed uv sidecar for $TRIPLE in $OUT_DIR"
