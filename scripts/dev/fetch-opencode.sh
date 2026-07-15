#!/usr/bin/env bash
# Fetch the pinned OpenCode binary and place it as a Tauri sidecar
# (apps/desktop/src-tauri/binaries/opencode-<target-triple>).
# Runs per-platform locally and in CI so the binary never lives in git.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/scripts/dev/sidecar-integrity.sh"
OPENCODE_VERSION="${OPENCODE_VERSION:-$PINNED_OPENCODE_VERSION}"
OUT_DIR="$ROOT/apps/desktop/src-tauri/binaries"

# Resolve the Rust target triple (arg 1 overrides; else host).
TRIPLE="${1:-$(rustc -Vv | sed -n 's/host: //p')}"

case "$TRIPLE" in
  aarch64-apple-darwin)         ASSET="opencode-darwin-arm64.zip" ;;
  x86_64-apple-darwin)          ASSET="opencode-darwin-x64.zip" ;;
  x86_64-pc-windows-msvc)       ASSET="opencode-windows-x64.zip" ;;
  aarch64-pc-windows-msvc)      ASSET="opencode-windows-arm64.zip" ;;
  x86_64-unknown-linux-gnu)     ASSET="opencode-linux-x64.tar.gz" ;;
  aarch64-unknown-linux-gnu)    ASSET="opencode-linux-arm64.tar.gz" ;;
  *) echo "Unsupported triple: $TRIPLE" >&2; exit 1 ;;
esac

URL="https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/${ASSET}"
EXPECTED_SHA256="$(sidecar_sha256 opencode "$OPENCODE_VERSION" "$ASSET")"
mkdir -p "$OUT_DIR"
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
  cp "$TMP/opencode.exe" "$OUT_DIR/opencode-$TRIPLE.exe"
else
  BIN="$(find "$TMP" -type f -name opencode -print -quit)"
  [ -n "$BIN" ] || { echo "No opencode binary in archive" >&2; exit 1; }
  cp "$BIN" "$OUT_DIR/opencode-$TRIPLE"
  chmod +x "$OUT_DIR/opencode-$TRIPLE"
fi
echo "Placed sidecar for $TRIPLE in $OUT_DIR"
