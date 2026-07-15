#!/usr/bin/env bash

# Trusted SHA-256 digests for every sidecar target supported by the fetch
# scripts. A version upgrade must update this table in the same reviewed change;
# unknown versions and assets fail closed before any download is unpacked.
PINNED_OPENCODE_VERSION='1.17.13'
PINNED_UV_VERSION='0.11.26'

sidecar_sha256() {
  local tool="$1"
  local version="$2"
  local asset="$3"

  case "${tool}:${version}:${asset}" in
    opencode:1.17.13:opencode-darwin-arm64.zip)
      printf '%s\n' 'dd016d3e26b347d675ab26c45d1e287545912d5c4c49fa0770b622d4a1367e23'
      ;;
    opencode:1.17.13:opencode-darwin-x64.zip)
      printf '%s\n' '0bf3d9d134097ca698b83f64c55db960d6d2d0c409069bf4cfd863e5de503b4a'
      ;;
    opencode:1.17.13:opencode-linux-arm64.tar.gz)
      printf '%s\n' 'bbaccdd374aaab66cd97c7f8ad1c080aa393610fa5f80ee8dfc007f9500afaf9'
      ;;
    opencode:1.17.13:opencode-linux-x64.tar.gz)
      printf '%s\n' '157afa289d1a8d9372de0ce19ac726119b937a1f6b201808d46f06e4e59bb348'
      ;;
    opencode:1.17.13:opencode-windows-arm64.zip)
      printf '%s\n' 'bafec2dd6b89055910284ba910d59605295866563ccdb3d035c0c4b887dd11e6'
      ;;
    opencode:1.17.13:opencode-windows-x64.zip)
      printf '%s\n' '18aa3df701a6eafcca201b5bcc63e086c96c8daa6ae2495cf718e12cb0ce3361'
      ;;
    uv:0.11.26:uv-aarch64-apple-darwin.tar.gz)
      printf '%s\n' '8f7fbf1708399b921857bce71e1d60f0d3ccf52a30caebc1c1a2f175dce13ab6'
      ;;
    uv:0.11.26:uv-x86_64-apple-darwin.tar.gz)
      printf '%s\n' '922b460202707dd5f4ccacbadbe7f6a546cc46e82a99bf50ca99a7977a78eddd'
      ;;
    uv:0.11.26:uv-aarch64-unknown-linux-gnu.tar.gz)
      printf '%s\n' 'befa1a59c91e96eb601b0fd9a97c03dd666f17baba644b2b4db9c59a767e387e'
      ;;
    uv:0.11.26:uv-x86_64-unknown-linux-gnu.tar.gz)
      printf '%s\n' '6426a73c3837e6e2483ee344cbc00f36394d179afcba6183cb77437e67db4af0'
      ;;
    uv:0.11.26:uv-aarch64-pc-windows-msvc.zip)
      printf '%s\n' '98246149741f558e25e45ecf2b0b20f34de0634269f2bf0dcb4012d4b6ba289a'
      ;;
    uv:0.11.26:uv-x86_64-pc-windows-msvc.zip)
      printf '%s\n' '4e1278ede866be6c0bf32d2f466cc6de7a9fb399ecf20c9ce2d186e52424be47'
      ;;
    *)
      printf 'No trusted SHA-256 for %s %s asset %s\n' "$tool" "$version" "$asset" >&2
      return 1
      ;;
  esac
}

verify_sha256() {
  local file="$1"
  local expected="$2"
  local actual

  if [[ ! "$expected" =~ ^[0-9a-f]{64}$ ]]; then
    printf 'Invalid trusted SHA-256 for %s\n' "$file" >&2
    return 1
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$file" | awk '{print $1}')"
  elif command -v shasum >/dev/null 2>&1; then
    actual="$(shasum -a 256 "$file" | awk '{print $1}')"
  elif command -v openssl >/dev/null 2>&1; then
    actual="$(openssl dgst -sha256 "$file" | awk '{print $NF}')"
  else
    printf 'No SHA-256 implementation is available\n' >&2
    return 1
  fi

  if [[ "$actual" != "$expected" ]]; then
    printf 'SHA-256 mismatch for %s\n' "$file" >&2
    printf '  expected: %s\n' "$expected" >&2
    printf '  actual:   %s\n' "$actual" >&2
    return 1
  fi
  printf 'Verified SHA-256 for %s\n' "$(basename "$file")"
}
