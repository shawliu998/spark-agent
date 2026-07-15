#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$ROOT/scripts/dev/sidecar-integrity.sh"

records=(
  "opencode $PINNED_OPENCODE_VERSION opencode-darwin-arm64.zip"
  "opencode $PINNED_OPENCODE_VERSION opencode-darwin-x64.zip"
  "opencode $PINNED_OPENCODE_VERSION opencode-linux-arm64.tar.gz"
  "opencode $PINNED_OPENCODE_VERSION opencode-linux-x64.tar.gz"
  "opencode $PINNED_OPENCODE_VERSION opencode-windows-arm64.zip"
  "opencode $PINNED_OPENCODE_VERSION opencode-windows-x64.zip"
  "uv $PINNED_UV_VERSION uv-aarch64-apple-darwin.tar.gz"
  "uv $PINNED_UV_VERSION uv-x86_64-apple-darwin.tar.gz"
  "uv $PINNED_UV_VERSION uv-aarch64-unknown-linux-gnu.tar.gz"
  "uv $PINNED_UV_VERSION uv-x86_64-unknown-linux-gnu.tar.gz"
  "uv $PINNED_UV_VERSION uv-aarch64-pc-windows-msvc.zip"
  "uv $PINNED_UV_VERSION uv-x86_64-pc-windows-msvc.zip"
)

for record in "${records[@]}"; do
  read -r tool version asset <<<"$record"
  digest="$(sidecar_sha256 "$tool" "$version" "$asset")"
  if [[ ! "$digest" =~ ^[0-9a-f]{64}$ ]]; then
    printf 'Malformed sidecar digest for %s\n' "$record" >&2
    exit 1
  fi
done

if sidecar_sha256 opencode 0.0.0 opencode-darwin-arm64.zip >/dev/null 2>&1; then
  printf 'Unknown sidecar versions must fail closed\n' >&2
  exit 1
fi
if sidecar_sha256 uv 0.0.0 uv-aarch64-apple-darwin.tar.gz >/dev/null 2>&1; then
  printf 'Unknown uv versions must fail closed\n' >&2
  exit 1
fi
if sidecar_sha256 opencode "$PINNED_OPENCODE_VERSION" unknown.zip >/dev/null 2>&1; then
  printf 'Unknown sidecar assets must fail closed\n' >&2
  exit 1
fi
if sidecar_sha256 unknown "$PINNED_OPENCODE_VERSION" opencode-darwin-arm64.zip \
  >/dev/null 2>&1; then
  printf 'Unknown sidecar tools must fail closed\n' >&2
  exit 1
fi

fixture="$(mktemp)"
trap 'rm -f "$fixture"' EXIT
printf 'abc' >"$fixture"
verify_sha256 \
  "$fixture" \
  'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad' \
  >/dev/null
if verify_sha256 "$fixture" invalid-digest >/dev/null 2>&1; then
  printf 'Malformed trusted digests must fail closed\n' >&2
  exit 1
fi
printf 'tampered' >"$fixture"
if verify_sha256 \
  "$fixture" \
  'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad' \
  >/dev/null 2>&1; then
  printf 'Tampered sidecar content must be rejected\n' >&2
  exit 1
fi

bash -n \
  "$ROOT/scripts/dev/sidecar-integrity.sh" \
  "$ROOT/scripts/dev/fetch-opencode.sh" \
  "$ROOT/scripts/dev/fetch-uv.sh"

printf 'Release sidecar integrity policy passed for %d assets.\n' "${#records[@]}"
