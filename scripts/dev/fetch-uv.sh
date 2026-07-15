#!/usr/bin/env bash
# Fetch the pinned uv binary as a Tauri sidecar
# (apps/desktop/src-tauri/binaries/uv-<target-triple>). uv provisions the
# isolated Jupyter environment for the Jupyter MCP integration on demand.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT/scripts/dev/sidecar-integrity.sh"
UV_VERSION="${UV_VERSION:-$PINNED_UV_VERSION}"
OUT_DIR="$ROOT/apps/desktop/src-tauri/binaries"

TRIPLE="${1:-$(rustc -Vv | sed -n 's/host: //p')}"

case "$TRIPLE" in
  aarch64-apple-darwin | x86_64-apple-darwin) ASSET="uv-$TRIPLE.tar.gz" ;;
  x86_64-unknown-linux-gnu | aarch64-unknown-linux-gnu) ASSET="uv-$TRIPLE.tar.gz" ;;
  x86_64-pc-windows-msvc | aarch64-pc-windows-msvc) ASSET="uv-$TRIPLE.zip" ;;
  *) echo "Unsupported triple: $TRIPLE" >&2; exit 1 ;;
esac

URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${ASSET}"
EXPECTED_SHA256="$(sidecar_sha256 uv "$UV_VERSION" "$ASSET")"
mkdir -p "$OUT_DIR"
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
  cp "$BIN" "$OUT_DIR/uv-$TRIPLE.exe"
else
  BIN="$(find "$TMP" -type f -name uv -print -quit)"
  [ -n "$BIN" ] || { echo "No uv binary in archive" >&2; exit 1; }
  cp "$BIN" "$OUT_DIR/uv-$TRIPLE"
  chmod +x "$OUT_DIR/uv-$TRIPLE"
fi
echo "Placed uv sidecar for $TRIPLE in $OUT_DIR"
