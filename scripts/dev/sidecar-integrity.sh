#!/usr/bin/env bash

# Single reviewed manifest for every downloadable release input used by the
# desktop bundle. Fetch scripts and quality checks both consume these records;
# there is no second asset/checksum table to keep in sync.
SIDECAR_ASSET_MANIFEST=(
  'opencode|1.17.13|aarch64-apple-darwin|opencode-darwin-arm64.zip|dd016d3e26b347d675ab26c45d1e287545912d5c4c49fa0770b622d4a1367e23|5124dfe29f6b4552a71a00d1b60427229c58576cb3e2bff5c884cd7c12d66bdb'
  'opencode|1.17.13|x86_64-apple-darwin|opencode-darwin-x64.zip|0bf3d9d134097ca698b83f64c55db960d6d2d0c409069bf4cfd863e5de503b4a|04065dcf635fef5785453f7eb7a2ad225c772d24893e8f204bdac4a3abc482af'
  'opencode|1.17.13|aarch64-unknown-linux-gnu|opencode-linux-arm64.tar.gz|bbaccdd374aaab66cd97c7f8ad1c080aa393610fa5f80ee8dfc007f9500afaf9|97cec34266f1fb21752755c1539a9accc1b5a1b8b3d1642046db9c15f424da54'
  'opencode|1.17.13|x86_64-unknown-linux-gnu|opencode-linux-x64.tar.gz|157afa289d1a8d9372de0ce19ac726119b937a1f6b201808d46f06e4e59bb348|ae98d78e8b9a4f3ef4fd16920bfb3dedeab731c0586badff501f75b53ece3b6d'
  'opencode|1.17.13|aarch64-pc-windows-msvc|opencode-windows-arm64.zip|bafec2dd6b89055910284ba910d59605295866563ccdb3d035c0c4b887dd11e6|26408853adea4128d977c787c4a2d947649b4bfaa806b2e9c6c0436b6309f2f5'
  'opencode|1.17.13|x86_64-pc-windows-msvc|opencode-windows-x64.zip|18aa3df701a6eafcca201b5bcc63e086c96c8daa6ae2495cf718e12cb0ce3361|f8c45bae73a8f1e2088023fdd34dc2fe0a7f93f505f073e0703e4e1a19afe8ff'
  'uv|0.11.26|aarch64-apple-darwin|uv-aarch64-apple-darwin.tar.gz|8f7fbf1708399b921857bce71e1d60f0d3ccf52a30caebc1c1a2f175dce13ab6|c9300ed8425e2c85230259a172066a32b475bc56f7ebe907783b2459159ea554'
  'uv|0.11.26|x86_64-apple-darwin|uv-x86_64-apple-darwin.tar.gz|922b460202707dd5f4ccacbadbe7f6a546cc46e82a99bf50ca99a7977a78eddd|ef0df4073dd04f3827b40c55ecb9c99144598a4eec728dd109d76fd7bead0375'
  'uv|0.11.26|aarch64-unknown-linux-gnu|uv-aarch64-unknown-linux-gnu.tar.gz|befa1a59c91e96eb601b0fd9a97c03dd666f17baba644b2b4db9c59a767e387e|9a36adc1a125e969a6952ef69b8072960a532f45e3434b972250e61801861c5b'
  'uv|0.11.26|x86_64-unknown-linux-gnu|uv-x86_64-unknown-linux-gnu.tar.gz|6426a73c3837e6e2483ee344cbc00f36394d179afcba6183cb77437e67db4af0|29b90e884c384e1578ac37335521d807c192aa44d5a4a9b9f4690bb3850e179d'
  'uv|0.11.26|aarch64-pc-windows-msvc|uv-aarch64-pc-windows-msvc.zip|98246149741f558e25e45ecf2b0b20f34de0634269f2bf0dcb4012d4b6ba289a|f13a990b845aba00a30734c6c678e71b321148fdf8e28101033cce4d2b7452c5'
  'uv|0.11.26|x86_64-pc-windows-msvc|uv-x86_64-pc-windows-msvc.zip|4e1278ede866be6c0bf32d2f466cc6de7a9fb399ecf20c9ce2d186e52424be47|deeaa21aac3e3e40b3fa00788208aa9a319cefbb3c2aa598cf580565a82ebc34'
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
  local record record_tool record_version record_triple record_asset record_digest record_binary_digest
  local resolved=''

  for record in "${SIDECAR_ASSET_MANIFEST[@]}"; do
    IFS='|' read -r record_tool record_version record_triple record_asset record_digest record_binary_digest <<<"$record"
    if [[ "$record_tool" == "$requested_tool" &&
      "$record_version" == "$requested_version" &&
      "$record_triple" == "$requested_triple" ]]; then
      if [[ -n "$resolved" ]]; then
        printf 'Duplicate release entries for %s %s target %s\n' \
          "$requested_tool" "$requested_version" "$requested_triple" >&2
        return 1
      fi
      resolved="${record_asset}|${record_digest}|${record_binary_digest}"
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

verify_tree_manifest() {
  local tree="$1"
  local manifest="$2"
  local expected actual
  [[ -d "$tree" && ! -L "$tree" && -f "$manifest" && ! -L "$manifest" ]] || {
    printf 'Tree or manifest is missing or a symlink\n' >&2
    return 1
  }
  if find -H "$tree" -type l -print -quit | grep -q .; then
    printf 'Tree contains a symlink: %s\n' "$tree" >&2
    return 1
  fi
  expected="$(mktemp)" || return 1
  actual="$(mktemp)" || { rm -f "$expected"; return 1; }
  awk -F '|' 'NF != 2 || $1 !~ /^[0-9a-f]{64}$/ || $2 == "" || $2 ~ /^\// || index($2, "..") { exit 1 } { print }' "$manifest" | LC_ALL=C sort -u >"$expected" || { rm -f "$expected" "$actual"; return 1; }
  [[ "$(wc -l <"$expected" | tr -d ' ')" -eq "$(wc -l <"$manifest" | tr -d ' ')" ]] || { rm -f "$expected" "$actual"; return 1; }
  (cd "$tree" && find . -type f -print | LC_ALL=C sort | while IFS= read -r relative; do
    digest="$(verify_file_sha256 "$relative")" || exit 1
    printf '%s|%s\n' "$digest" "${relative#./}"
  done) | LC_ALL=C sort >"$actual" || return 1
  cmp -s "$expected" "$actual"
  local status=$?
  rm -f "$expected" "$actual"
  return "$status"
}

verify_file_sha256() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$file" | awk '{print $1}';
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$file" | awk '{print $1}';
  else return 1; fi
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

# Bash 3.2 has no BASHPID and $$ does not change in a `( ... )` subshell. Run a
# child directly (without command substitution) so its PPID is the shell that
# actually owns the transaction, and let it write that PID to the journal.
_transaction_write_process_owner() {
  local directory="$1"
  command sh -c 'printf "%s\n" "$PPID" >"$1/owner"' sh "$directory"
}

# Verify ownership without command substitution: on Bash 3.2, a directly
# invoked child's PPID is the calling transaction/recovery shell, while $$ is
# inherited by `( ... )` subshells and cannot identify that shell.
_transaction_owner_is_current_process() {
  local directory="$1"

  [[ -f "$directory/owner" && ! -L "$directory/owner" ]] || return 1
  command sh -c '
    IFS= read -r owner <"$1/owner" || exit 1
    [ -n "$owner" ] && [ "$owner" = "$PPID" ]
  ' sh "$directory"
}

_transaction_process_is_alive() {
  local pid="$1"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
  command kill -0 "$pid" >/dev/null 2>&1
}

_transaction_read_field() {
  local directory="$1"
  local field="$2"
  local path="$directory/$field"
  local value

  [[ -f "$path" && ! -L "$path" ]] || return 1
  value="$(command cat "$path")" || return 1
  [[ -n "$value" && "$value" != *$'\n'* ]] || return 1
  printf '%s\n' "$value"
}

_transaction_write_field() {
  local directory="$1"
  local field="$2"
  local value="$3"

  printf '%s\n' "$value" >"$directory/$field"
}

# A torn phase write during process termination could make an otherwise
# recoverable transaction ambiguous. Publish phase changes with a same-directory
# rename so process/SIGKILL recovery sees either the complete old value or the
# complete new value. This does not claim power-loss durability because no
# fsync is performed. The platform path bypasses directory-rename fault shims.
_transaction_write_phase() {
  local directory="$1"
  local phase="$2"
  local temporary

  temporary="$(mktemp "$directory/.phase.XXXXXX")" || return 1
  if ! printf '%s\n' "$phase" >"$temporary"; then
    command rm -f "$temporary"
    return 1
  fi
  if ! command /bin/mv -f "$temporary" "$directory/phase"; then
    command rm -f "$temporary"
    return 1
  fi
}

# Recover an interrupted directory swap before a new verified archive is
# downloaded. Recovery only rolls back to `backup/previous`; it never promotes
# the abandoned staging tree or a partially installed destination. Ambiguous or
# malformed state is retained for inspection and fails closed.
recover_directory_transaction() (
  set -Eeuo pipefail
  shopt -s nullglob

  local destination="$1"
  local parent name lock claim
  local owner recovery_owner schema staging_name backup_name previous_expected phase
  local staging backup_root previous candidate basename rest staging_owner suffix
  local claim_acquired=0
  local lock_entries

  parent="$(dirname "$destination")"
  name="$(basename "$destination")"
  lock="$parent/.${name}.install.lock"
  claim="$lock/recovery"

  cleanup_recovery_claim() {
    trap - EXIT INT TERM HUP
    if [[ "$claim_acquired" -eq 1 && -d "$claim" && ! -L "$claim" ]]; then
      command rm -rf "$claim" || true
    fi
  }
  trap cleanup_recovery_claim EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'exit 129' HUP

  if [[ -e "$lock" || -L "$lock" ]]; then
    if [[ ! -d "$lock" || -L "$lock" ]]; then
      printf 'Invalid transaction lock: %s\n' "$lock" >&2
      return 1
    fi

    if ! owner="$(_transaction_read_field "$lock" owner)"; then
      printf 'Incomplete transaction owner metadata in %s\n' "$lock" >&2
      return 1
    fi
    if _transaction_process_is_alive "$owner"; then
      printf 'Another installer holds %s (pid %s)\n' "$lock" "$owner" >&2
      return 1
    fi

    if [[ -e "$claim" || -L "$claim" ]]; then
      if [[ ! -d "$claim" || -L "$claim" ]] ||
        ! recovery_owner="$(_transaction_read_field "$claim" owner)"; then
        printf 'Invalid recovery claim in %s\n' "$claim" >&2
        return 1
      fi
      if _transaction_process_is_alive "$recovery_owner"; then
        printf 'Another recovery holds %s (pid %s)\n' \
          "$claim" "$recovery_owner" >&2
      else
        # Never replace a stale claim in place. Two recoverers could both read
        # its dead owner, then one could delete the other's newly created claim.
        printf 'Stale recovery claim requires manual cleanup: %s\n' \
          "$claim" >&2
      fi
      return 1
    fi
    command mkdir "$claim" || return 1
    claim_acquired=1
    _transaction_write_process_owner "$claim" || return 1
    if ! _transaction_owner_is_current_process "$claim" ||
      ! recovery_owner="$(_transaction_read_field "$claim" owner)" ||
      ! _transaction_process_is_alive "$recovery_owner"; then
      printf 'Failed to establish recovery ownership in %s\n' "$claim" >&2
      return 1
    fi

    # An untrappable kill can leave only the unpublished side of an atomic
    # phase update. It is never interpreted as state; the canonical phase file
    # remains authoritative.
    for candidate in "$lock"/.phase.*; do
      [[ -f "$candidate" && ! -L "$candidate" ]] || {
        printf 'Invalid transaction phase temporary: %s\n' "$candidate" >&2
        return 1
      }
      command rm -f "$candidate" || return 1
    done

    if ! schema="$(_transaction_read_field "$lock" schema)" || [[ "$schema" != 1 ]] ||
      ! staging_name="$(_transaction_read_field "$lock" staging)" ||
      ! backup_name="$(_transaction_read_field "$lock" backup)" ||
      ! previous_expected="$(_transaction_read_field "$lock" previous)" ||
      ! phase="$(_transaction_read_field "$lock" phase)"; then
      printf 'Incomplete transaction journal in %s\n' "$lock" >&2
      return 1
    fi
    [[ "$previous_expected" == 0 || "$previous_expected" == 1 ]] || {
      printf 'Invalid transaction previous-state marker in %s\n' "$lock" >&2
      return 1
    }
    case "$phase" in
      prepared | old-moved | new-installed | committed | cleanup | recovering | restored) ;;
      *)
        printf 'Invalid transaction phase in %s\n' "$lock" >&2
        return 1
        ;;
    esac
    case "$staging_name" in
      ".${name}.staging."*) ;;
      *)
        printf 'Invalid transaction staging name in %s\n' "$lock" >&2
        return 1
        ;;
    esac
    case "$backup_name" in
      ".${name}.backup."*) ;;
      *)
        printf 'Invalid transaction backup name in %s\n' "$lock" >&2
        return 1
        ;;
    esac
    [[ "$staging_name" != */* && "$backup_name" != */* ]] || {
      printf 'Transaction journal paths must be sibling basenames\n' >&2
      return 1
    }

    if ! lock_entries="$(find "$lock" -mindepth 1 -maxdepth 1 \
      ! -name owner ! -name schema ! -name staging ! -name backup \
      ! -name previous ! -name phase ! -name recovery -print -quit)"; then
      return 1
    fi
    [[ -z "$lock_entries" ]] || {
      printf 'Unexpected transaction journal content in %s\n' "$lock" >&2
      return 1
    }

    staging="$parent/$staging_name"
    backup_root="$parent/$backup_name"
    previous="$backup_root/previous"

    for candidate in "$parent/.${name}.backup."*; do
      [[ "$candidate" == "$backup_root" ]] || {
        printf 'Ambiguous transaction backups beside %s\n' "$destination" >&2
        return 1
      }
    done
    if [[ -e "$backup_root" || -L "$backup_root" ]]; then
      [[ -d "$backup_root" && ! -L "$backup_root" ]] || {
        printf 'Invalid transaction backup root: %s\n' "$backup_root" >&2
        return 1
      }
      if ! candidate="$(find "$backup_root" -mindepth 1 -maxdepth 1 \
        ! -name previous -print -quit)"; then
        return 1
      fi
      [[ -z "$candidate" ]] || {
        printf 'Unexpected content in transaction backup: %s\n' "$backup_root" >&2
        return 1
      }
    fi
    if [[ -e "$previous" || -L "$previous" ]]; then
      [[ -d "$previous" && ! -L "$previous" ]] || {
        printf 'Invalid previous directory in transaction backup\n' >&2
        return 1
      }
    fi
    if [[ -e "$destination" || -L "$destination" ]]; then
      [[ -d "$destination" && ! -L "$destination" ]] || {
        printf 'Invalid transaction destination: %s\n' "$destination" >&2
        return 1
      }
    fi
    if [[ -e "$staging" || -L "$staging" ]]; then
      [[ -d "$staging" && ! -L "$staging" ]] || {
        printf 'Invalid abandoned transaction staging: %s\n' "$staging" >&2
        return 1
      }
    fi

    if [[ "$phase" == committed || "$phase" == cleanup ]]; then
      # The verified candidate became authoritative before backup cleanup
      # began. The backup may now be only a fragment of the old tree, so it is
      # disposal-only and must never be used as a rollback source.
      [[ -d "$destination" && ! -L "$destination" ]] || {
        printf 'Committed transaction is missing its destination: %s\n' \
          "$destination" >&2
        return 1
      }
      [[ ! -e "$staging" && ! -L "$staging" ]] || {
        printf 'Committed transaction unexpectedly retains staging: %s\n' \
          "$staging" >&2
        return 1
      }
      if [[ "$previous_expected" == 0 && ( -e "$previous" || -L "$previous" ) ]]; then
        printf 'Fresh committed transaction unexpectedly has a previous tree\n' >&2
        return 1
      fi
      _transaction_write_phase "$lock" cleanup || return 1
      if [[ -e "$backup_root" || -L "$backup_root" ]]; then
        command rm -rf "$backup_root" || return 1
      fi
    elif [[ "$previous_expected" == 1 && "$phase" == restored &&
      ( -e "$previous" || -L "$previous" ) ]]; then
      printf 'Restored transaction unexpectedly retains its previous tree\n' >&2
      return 1
    elif [[ "$previous_expected" == 1 && -d "$previous" ]]; then
      _transaction_write_phase "$lock" recovering || return 1
      if [[ -d "$destination" ]]; then
        [[ ! -e "$staging" && ! -L "$staging" ]] || {
          printf 'Cannot quarantine destination; staging already exists: %s\n' \
            "$staging" >&2
          return 1
        }
        command mv "$destination" "$staging" || return 1
      fi
      [[ ! -e "$destination" && ! -L "$destination" ]] || return 1
      command mv "$previous" "$destination" || return 1
      _transaction_write_phase "$lock" restored || return 1
      if [[ -e "$staging" || -L "$staging" ]]; then
        command rm -rf "$staging" || return 1
      fi
    elif [[ "$previous_expected" == 1 && "$phase" == prepared &&
      -d "$destination" && ! -e "$previous" && ! -L "$previous" ]]; then
      # The process died before the first rename; the installed tree is still
      # the previously accepted one. Discard only the abandoned staging tree.
      if [[ -e "$staging" || -L "$staging" ]]; then
        command rm -rf "$staging" || return 1
      fi
    elif [[ "$previous_expected" == 1 &&
      ( "$phase" == recovering || "$phase" == restored ) &&
      -d "$destination" && -d "$staging" &&
      ! -e "$previous" && ! -L "$previous" ]]; then
      # Recovery was killed after restoring the backup but before discarding
      # the quarantined new tree.
      _transaction_write_phase "$lock" restored || return 1
      command rm -rf "$staging" || return 1
    elif [[ "$previous_expected" == 1 && "$phase" == restored &&
      -d "$destination" && ! -e "$staging" && ! -L "$staging" &&
      ! -e "$previous" && ! -L "$previous" ]]; then
      # Recovery already restored the accepted tree and removed the quarantined
      # candidate before the process stopped. Keep the restored destination and
      # finish only the empty backup/lock cleanup below.
      :
    elif [[ "$previous_expected" == 1 &&
      ( "$phase" == recovering || "$phase" == restored ) ]]; then
      # Other no-backup layouts cannot prove whether destination is the old tree
      # or the candidate. Preserve all evidence and require manual inspection.
      printf 'Ambiguous %s transaction state in %s\n' "$phase" "$lock" >&2
      return 1
    elif [[ "$previous_expected" == 1 && ! -e "$previous" && ! -L "$previous" ]]; then
      # The old backup was already removed but cleanup crashed before the lock
      # disappeared. Do not trust the installed candidate: discard it and let
      # this invocation perform a fresh verified download.
      if [[ -e "$destination" || -L "$destination" ]]; then
        [[ ! -e "$staging" && ! -L "$staging" ]] || {
          printf 'Ambiguous destination and staging without a backup\n' >&2
          return 1
        }
        command mv "$destination" "$staging" || return 1
      fi
      if [[ -e "$staging" || -L "$staging" ]]; then
        command rm -rf "$staging" || return 1
      fi
    elif [[ "$previous_expected" == 0 && ! -e "$previous" && ! -L "$previous" ]]; then
      # A fresh install had no rollback source. Never accept the abandoned
      # destination or staging; a new verified archive will replace it.
      if [[ -e "$destination" || -L "$destination" ]]; then
        [[ ! -e "$staging" && ! -L "$staging" ]] || {
          printf 'Ambiguous fresh-install destination and staging\n' >&2
          return 1
        }
        command mv "$destination" "$staging" || return 1
      fi
      if [[ -e "$staging" || -L "$staging" ]]; then
        command rm -rf "$staging" || return 1
      fi
    else
      printf 'Ambiguous interrupted transaction state in %s\n' "$lock" >&2
      return 1
    fi

    if [[ -d "$backup_root" ]]; then
      if ! candidate="$(find "$backup_root" -mindepth 1 -maxdepth 1 -print -quit)"; then
        return 1
      fi
      [[ -z "$candidate" ]] || {
        printf 'Transaction backup is not empty after recovery: %s\n' \
          "$backup_root" >&2
        return 1
      }
      command rmdir "$backup_root" || return 1
    fi
    command rm -rf "$lock" || return 1
    claim_acquired=0
  fi

  # Backups without a lock are never guessed at or deleted.
  for candidate in "$parent/.${name}.backup."*; do
    printf 'Orphan transaction backup requires manual recovery: %s\n' \
      "$candidate" >&2
    return 1
  done

  # Staging is never promoted. The PID embedded by fetch-skills lets a later
  # run remove only directories whose creating process is definitely gone.
  for candidate in "$parent/.${name}.staging."*; do
    [[ -d "$candidate" && ! -L "$candidate" ]] || {
      printf 'Invalid orphan transaction staging: %s\n' "$candidate" >&2
      return 1
    }
    basename="$(basename "$candidate")"
    rest="${basename#.${name}.staging.}"
    staging_owner="${rest%%.*}"
    suffix="${rest#*.}"
    [[ "$staging_owner" =~ ^[1-9][0-9]*$ && -n "$suffix" && "$suffix" != "$rest" ]] || {
      printf 'Unrecognized orphan transaction staging: %s\n' "$candidate" >&2
      return 1
    }
    if ! _transaction_process_is_alive "$staging_owner"; then
      command rm -rf "$candidate" || return 1
    fi
  done
)

# Install a fully prepared sibling directory while serializing competing
# installers. Before publication starts, the EXIT trap can restore the backup;
# once publication starts without a committed phase, it retains backup and lock
# so recovery can inspect the physical rename state. Failed cleanup fails closed.
install_directory_transactionally() (
  set -Eeuo pipefail

  local staging="$1"
  local destination="$2"
  local parent name lock staging_name backup_name previous_expected
  local backup_root=''
  local backup=''
  local lock_acquired=0
  local had_previous=0
  local installed=0
  local committed=0

  [[ -d "$staging" && ! -L "$staging" ]] || {
    printf 'Directory install source is not a real directory: %s\n' "$staging" >&2
    return 1
  }

  parent="$(dirname "$destination")"
  name="$(basename "$destination")"
  staging_name="$(basename "$staging")"
  if [[ "$(dirname "$staging")" != "$parent" ]]; then
    printf 'Directory staging path must be a sibling of %s\n' "$destination" >&2
    return 1
  fi
  case "$staging_name" in
    ".${name}.staging."*) ;;
    *)
      printf 'Directory staging name is not recoverable: %s\n' "$staging" >&2
      return 1
      ;;
  esac
  lock="$parent/.${name}.install.lock"
  if ! command mkdir -p "$parent"; then
    return 1
  fi

  cleanup_transaction() {
    local exit_code=$?
    local restore_failed=0
    trap - EXIT INT TERM HUP

    if [[ "$lock_acquired" -eq 1 ]]; then
      if [[ "$installed" -eq 1 && "$committed" -eq 0 ]]; then
        # Destination publication began, but no committed phase was published
        # before process termination. Retain all state so recovery can inspect
        # whether the atomic rename happened and safely roll back.
        restore_failed=1
        exit_code=1
        printf 'Install reached an uncommitted destination; transaction retained at %s\n' \
          "$lock" >&2
      elif [[ "$installed" -eq 0 && "$had_previous" -eq 1 ]]; then
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

      if [[ "$restore_failed" -eq 0 && -n "$backup_root" ]]; then
        if ! command rm -rf "$backup_root"; then
          restore_failed=1
          exit_code=1
          printf 'Failed to remove transaction backup %s\n' "$backup_root" >&2
        fi
      fi
      if [[ "$restore_failed" -eq 0 ]]; then
        command rm -rf "$lock" || exit_code=1
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
    previous_expected=1
  else
    previous_expected=0
  fi
  backup_name="$(basename "$backup_root")" || return 1
  _transaction_write_process_owner "$lock" || return 1
  _transaction_write_field "$lock" schema 1 || return 1
  _transaction_write_field "$lock" staging "$staging_name" || return 1
  _transaction_write_field "$lock" backup "$backup_name" || return 1
  _transaction_write_field "$lock" previous "$previous_expected" || return 1
  _transaction_write_phase "$lock" prepared || return 1

  if [[ "$previous_expected" == 1 ]]; then
    # Set the conservative state before the destructive rename. A catchable
    # signal may run the EXIT trap after mv succeeds but before the next shell
    # assignment executes.
    had_previous=1
    if ! command mv "$destination" "$backup"; then
      return 1
    fi
  fi
  _transaction_write_phase "$lock" old-moved || return 1
  # Revalidate immediately before publication in case the sibling staging root
  # was exchanged after the entry check.
  [[ -d "$staging" && ! -L "$staging" ]] || {
    printf 'Directory staging root changed before install: %s\n' "$staging" >&2
    return 1
  }
  # Likewise, once publication starts an uncommitted process exit must retain
  # the journal and let recovery decide which side of the rename is present.
  installed=1
  if ! command mv "$staging" "$destination"; then
    return 1
  fi
  _transaction_write_phase "$lock" committed || return 1
  committed=1
  _transaction_write_phase "$lock" cleanup || return 1
)
