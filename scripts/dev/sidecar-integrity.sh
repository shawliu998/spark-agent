#!/usr/bin/env bash

# Single reviewed manifest for every downloadable release input used by the
# desktop bundle. Fetch scripts and quality checks both consume these records;
# there is no second asset/checksum table to keep in sync.
SIDECAR_ASSET_MANIFEST=(
  'opencode|1.17.13|aarch64-apple-darwin|opencode-darwin-arm64.zip|dd016d3e26b347d675ab26c45d1e287545912d5c4c49fa0770b622d4a1367e23'
  'opencode|1.17.13|x86_64-apple-darwin|opencode-darwin-x64.zip|0bf3d9d134097ca698b83f64c55db960d6d2d0c409069bf4cfd863e5de503b4a'
  'opencode|1.17.13|aarch64-unknown-linux-gnu|opencode-linux-arm64.tar.gz|bbaccdd374aaab66cd97c7f8ad1c080aa393610fa5f80ee8dfc007f9500afaf9'
  'opencode|1.17.13|x86_64-unknown-linux-gnu|opencode-linux-x64.tar.gz|157afa289d1a8d9372de0ce19ac726119b937a1f6b201808d46f06e4e59bb348'
  'opencode|1.17.13|aarch64-pc-windows-msvc|opencode-windows-arm64.zip|bafec2dd6b89055910284ba910d59605295866563ccdb3d035c0c4b887dd11e6'
  'opencode|1.17.13|x86_64-pc-windows-msvc|opencode-windows-x64.zip|18aa3df701a6eafcca201b5bcc63e086c96c8daa6ae2495cf718e12cb0ce3361'
  'uv|0.11.26|aarch64-apple-darwin|uv-aarch64-apple-darwin.tar.gz|8f7fbf1708399b921857bce71e1d60f0d3ccf52a30caebc1c1a2f175dce13ab6'
  'uv|0.11.26|x86_64-apple-darwin|uv-x86_64-apple-darwin.tar.gz|922b460202707dd5f4ccacbadbe7f6a546cc46e82a99bf50ca99a7977a78eddd'
  'uv|0.11.26|aarch64-unknown-linux-gnu|uv-aarch64-unknown-linux-gnu.tar.gz|befa1a59c91e96eb601b0fd9a97c03dd666f17baba644b2b4db9c59a767e387e'
  'uv|0.11.26|x86_64-unknown-linux-gnu|uv-x86_64-unknown-linux-gnu.tar.gz|6426a73c3837e6e2483ee344cbc00f36394d179afcba6183cb77437e67db4af0'
  'uv|0.11.26|aarch64-pc-windows-msvc|uv-aarch64-pc-windows-msvc.zip|98246149741f558e25e45ecf2b0b20f34de0634269f2bf0dcb4012d4b6ba289a'
  'uv|0.11.26|x86_64-pc-windows-msvc|uv-x86_64-pc-windows-msvc.zip|4e1278ede866be6c0bf32d2f466cc6de7a9fb399ecf20c9ce2d186e52424be47'
)

SKILLS_ARCHIVE_MANIFEST=(
  'ai4s-skills|8fa2ab0523082c135598909b227ed8feb48263ad|cbe373236b85e952762c5cbae7d72eb72c1e11077865c71b9ac59a084dd9a408'
)

sidecar_pinned_version() {
  local requested_tool="$1"
  local record record_tool record_version _
  local pinned_version=''

  for record in "${SIDECAR_ASSET_MANIFEST[@]}"; do
    IFS='|' read -r record_tool record_version _ <<<"$record"
    if [[ "$record_tool" == "$requested_tool" ]]; then
      if [[ -n "$pinned_version" && "$pinned_version" != "$record_version" ]]; then
        printf 'Multiple pinned versions found for %s\n' "$requested_tool" >&2
        return 1
      fi
      pinned_version="$record_version"
    fi
  done

  if [[ -z "$pinned_version" ]]; then
    printf 'No pinned version found for %s\n' "$requested_tool" >&2
    return 1
  fi
  printf '%s\n' "$pinned_version"
}

resolve_sidecar() {
  local requested_tool="$1"
  local requested_version="$2"
  local requested_triple="$3"
  local record record_tool record_version record_triple record_asset record_digest
  local resolved=''

  for record in "${SIDECAR_ASSET_MANIFEST[@]}"; do
    IFS='|' read -r record_tool record_version record_triple record_asset record_digest <<<"$record"
    if [[ "$record_tool" == "$requested_tool" &&
      "$record_version" == "$requested_version" &&
      "$record_triple" == "$requested_triple" ]]; then
      if [[ -n "$resolved" ]]; then
        printf 'Duplicate release entries for %s %s target %s\n' \
          "$requested_tool" "$requested_version" "$requested_triple" >&2
        return 1
      fi
      resolved="${record_asset}|${record_digest}"
    fi
  done

  if [[ -z "$resolved" ]]; then
    printf 'No trusted release asset for %s %s target %s\n' \
      "$requested_tool" "$requested_version" "$requested_triple" >&2
    return 1
  fi
  printf '%s\n' "$resolved"
}

skills_pinned_commit() {
  local requested_pack="$1"
  local record record_pack record_commit _
  local pinned_commit=''

  for record in "${SKILLS_ARCHIVE_MANIFEST[@]}"; do
    IFS='|' read -r record_pack record_commit _ <<<"$record"
    if [[ "$record_pack" == "$requested_pack" ]]; then
      if [[ -n "$pinned_commit" && "$pinned_commit" != "$record_commit" ]]; then
        printf 'Multiple pinned commits found for %s\n' "$requested_pack" >&2
        return 1
      fi
      pinned_commit="$record_commit"
    fi
  done

  if [[ -z "$pinned_commit" ]]; then
    printf 'No pinned commit found for %s\n' "$requested_pack" >&2
    return 1
  fi
  printf '%s\n' "$pinned_commit"
}

skills_archive_sha256() {
  local requested_pack="$1"
  local requested_commit="$2"
  local record record_pack record_commit record_digest
  local digest=''

  for record in "${SKILLS_ARCHIVE_MANIFEST[@]}"; do
    IFS='|' read -r record_pack record_commit record_digest <<<"$record"
    if [[ "$record_pack" == "$requested_pack" && "$record_commit" == "$requested_commit" ]]; then
      if [[ -n "$digest" ]]; then
        printf 'Duplicate trusted archive entries for %s at %s\n' \
          "$requested_pack" "$requested_commit" >&2
        return 1
      fi
      digest="$record_digest"
    fi
  done

  if [[ -z "$digest" ]]; then
    printf 'No trusted SHA-256 for %s at %s\n' \
      "$requested_pack" "$requested_commit" >&2
    return 1
  fi
  printf '%s\n' "$digest"
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

# Copy into a temporary file beside the destination, then replace with a
# same-filesystem rename. Any copy/chmod/rename failure removes only the
# temporary file and leaves an existing sidecar untouched.
install_sidecar_atomically() {
  local source="$1"
  local destination="$2"
  local destination_dir destination_name temporary

  if [[ ! -f "$source" ]]; then
    printf 'Sidecar install source is not a file: %s\n' "$source" >&2
    return 1
  fi

  destination_dir="$(dirname "$destination")"
  destination_name="$(basename "$destination")"
  if ! command mkdir -p "$destination_dir"; then
    return 1
  fi
  temporary="$(mktemp "$destination_dir/.${destination_name}.install.XXXXXX")" || return 1

  if ! command cp "$source" "$temporary"; then
    command rm -f "$temporary"
    return 1
  fi
  if ! command cmp -s "$source" "$temporary"; then
    command rm -f "$temporary"
    return 1
  fi
  if ! command chmod 755 "$temporary"; then
    command rm -f "$temporary"
    return 1
  fi
  if ! command mv -f "$temporary" "$destination"; then
    command rm -f "$temporary"
    return 1
  fi
}

# Install a fully prepared sibling directory while serializing competing
# installers. If the final rename fails or the process is interrupted after the
# old tree moves aside, the EXIT trap restores the backup before releasing the
# lock. A failed restore deliberately leaves both backup and lock for recovery.
install_directory_transactionally() (
  set -Eeuo pipefail

  local staging="$1"
  local destination="$2"
  local parent name lock
  local backup_root=''
  local backup=''
  local lock_acquired=0
  local had_previous=0
  local installed=0

  [[ -d "$staging" ]] || {
    printf 'Directory install source is not a directory: %s\n' "$staging" >&2
    return 1
  }

  parent="$(dirname "$destination")"
  name="$(basename "$destination")"
  if [[ "$(dirname "$staging")" != "$parent" ]]; then
    printf 'Directory staging path must be a sibling of %s\n' "$destination" >&2
    return 1
  fi
  lock="$parent/.${name}.install.lock"
  if ! command mkdir -p "$parent"; then
    return 1
  fi

  cleanup_transaction() {
    local exit_code=$?
    local restore_failed=0
    trap - EXIT INT TERM HUP

    if [[ "$lock_acquired" -eq 1 ]]; then
      if [[ "$installed" -eq 0 && "$had_previous" -eq 1 ]]; then
        if [[ ! -e "$destination" && ! -L "$destination" ]]; then
          if ! command mv "$backup" "$destination"; then
            restore_failed=1
            exit_code=1
            printf 'Failed to restore %s; backup retained at %s\n' \
              "$destination" "$backup" >&2
          fi
        else
          restore_failed=1
          exit_code=1
          printf 'Refusing to overwrite %s while restoring backup %s\n' \
            "$destination" "$backup" >&2
        fi
      fi

      if [[ "$restore_failed" -eq 0 ]]; then
        if [[ -n "$backup_root" ]]; then
          command rm -rf "$backup_root" || exit_code=1
        fi
        command rmdir "$lock" || exit_code=1
      else
        printf 'Install lock retained for manual recovery: %s\n' "$lock" >&2
      fi
    fi
    exit "$exit_code"
  }

  trap cleanup_transaction EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP

  if ! command mkdir "$lock"; then
    printf 'Another installer holds %s\n' "$lock" >&2
    return 1
  fi
  lock_acquired=1
  backup_root="$(mktemp -d "$parent/.${name}.backup.XXXXXX")" || return 1
  backup="$backup_root/previous"

  if [[ -e "$destination" || -L "$destination" ]]; then
    if ! command mv "$destination" "$backup"; then
      return 1
    fi
    had_previous=1
  fi
  if ! command mv "$staging" "$destination"; then
    return 1
  fi
  installed=1
)
