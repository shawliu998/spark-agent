#!/usr/bin/env bash
# Fetch the pinned ripgrep binary as a Tauri sidecar. OpenCode uses `rg` when
# loading skills; bundling it prevents an implicit network download at runtime.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=sidecar-integrity.sh
source "$ROOT/scripts/dev/sidecar-integrity.sh"
RIPGREP_VERSION="${RIPGREP_VERSION:-$(sidecar_pinned_version ripgrep)}"
OUT_DIR="$ROOT/apps/desktop/src-tauri/binaries"

TRIPLE="${1:-$(rustc -Vv | sed -n 's/host: //p')}"
RESOLVED_SIDECAR="$(resolve_sidecar ripgrep "$RIPGREP_VERSION" "$TRIPLE")"
IFS='|' read -r ASSET EXPECTED_SHA256 <<<"$RESOLVED_SIDECAR"

URL="https://github.com/BurntSushi/ripgrep/releases/download/${RIPGREP_VERSION}/${ASSET}"
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
      tar -xf "$TMP/$ASSET" -C "$TMP"
    fi
    ;;
esac

if find "$TMP" -type f -name rg.exe -print -quit | grep -q .; then
  BIN="$(find "$TMP" -type f -name rg.exe -print -quit)"
  DESTINATION="$OUT_DIR/rg-$TRIPLE.exe"
else
  BIN="$(find "$TMP" -type f -name rg -print -quit)"
  [ -n "$BIN" ] || { echo "No rg binary in archive" >&2; exit 1; }
  DESTINATION="$OUT_DIR/rg-$TRIPLE"
fi
install_sidecar_atomically "$BIN" "$DESTINATION"
echo "Placed ripgrep sidecar for $TRIPLE in $OUT_DIR"
