#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../dev/sidecar-integrity.sh
source "$ROOT/scripts/dev/sidecar-integrity.sh"

fail() {
  printf 'Release asset integrity check failed: %s\n' "$1" >&2
  exit 1
}

check_packaged_skill_bytecode() {
  local skill_source="$1"
  local contaminant

  [[ -d "$skill_source" ]] || \
    fail "packaged skill source is missing: $skill_source"
  if ! contaminant="$(find -H "$skill_source" \
    -type d -name .git -prune -o \
    \( -type d -name __pycache__ -o \
    -type f \( -name '*.pyc' -o -name '*.pyo' \) \) \
    -print -quit)"; then
    fail "could not inspect packaged skill source: $skill_source"
  fi
  [[ -z "$contaminant" ]] || \
    fail "Python bytecode/cache would be packaged: $contaminant"
}

read_json_version() {
  node -e '
    const fs = require("node:fs");
    const value = JSON.parse(fs.readFileSync(process.argv[1], "utf8")).version;
    if (typeof value === "string") process.stdout.write(value);
  ' "$1" 2>/dev/null || true
}

check_desktop_release_version() {
  local source_root="$1"
  local ref_type="${2:-}"
  local ref_name="${3:-}"
  local root_version desktop_version tauri_version cargo_version

  root_version="$(read_json_version "$source_root/package.json")"
  desktop_version="$(read_json_version "$source_root/apps/desktop/package.json")"
  tauri_version="$(read_json_version "$source_root/apps/desktop/src-tauri/tauri.conf.json")"
  cargo_version="$(sed -n 's/^version[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' \
    "$source_root/apps/desktop/src-tauri/Cargo.toml")"

  [[ -n "$root_version" && -n "$desktop_version" && -n "$tauri_version" && \
    -n "$cargo_version" ]] || fail "could not read every desktop release version"
  [[ "$root_version" == "$desktop_version" && \
    "$root_version" == "$tauri_version" && \
    "$root_version" == "$cargo_version" ]] || \
    fail "desktop release versions differ: root=$root_version desktop=$desktop_version tauri=$tauri_version cargo=$cargo_version"

  if [[ "$ref_type" == tag ]]; then
    [[ "$ref_name" == "v$root_version" ]] || \
      fail "release tag $ref_name does not match desktop version v$root_version"
  fi
}

if [[ "${1:-}" == "--version-only" ]]; then
  [[ "$#" -eq 1 ]] || fail "--version-only accepts no additional arguments"
  check_desktop_release_version \
    "$ROOT" \
    "${GITHUB_REF_TYPE:-}" \
    "${GITHUB_REF_NAME:-}"
  printf 'Desktop release version policy passed.\n'
  exit 0
fi
[[ "$#" -eq 0 ]] || fail "unknown argument: $1"

write_transaction_journal_fixture() {
  local destination="$1"
  local owner="$2"
  local staging_name="$3"
  local backup_name="$4"
  local previous="$5"
  local phase="$6"
  local parent name lock

  parent="$(dirname "$destination")"
  name="$(basename "$destination")"
  lock="$parent/.${name}.install.lock"
  mkdir -p "$lock"
  printf '%s\n' "$owner" >"$lock/owner"
  printf '1\n' >"$lock/schema"
  printf '%s\n' "$staging_name" >"$lock/staging"
  printf '%s\n' "$backup_name" >"$lock/backup"
  printf '%s\n' "$previous" >"$lock/previous"
  printf '%s\n' "$phase" >"$lock/phase"
}

[[ "${#SIDECAR_ASSET_MANIFEST[@]}" -eq 12 ]] || \
  fail "expected 12 sidecar manifest records, found ${#SIDECAR_ASSET_MANIFEST[@]}"

seen_keys=$'\n'
opencode_triples=$'\n'
uv_triples=$'\n'
opencode_count=0
uv_count=0
for record in "${SIDECAR_ASSET_MANIFEST[@]}"; do
  IFS='|' read -r tool version triple asset digest extra <<<"$record"
  [[ -n "$tool" && -n "$version" && -n "$triple" && -n "$asset" &&
    -n "$digest" && -z "$extra" ]] || \
    fail "malformed sidecar manifest record"
  [[ "$triple" =~ ^[A-Za-z0-9_-]+$ ]] || fail "unsafe target triple: $triple"
  [[ "$asset" =~ ^[A-Za-z0-9._-]+$ ]] || fail "unsafe sidecar asset name: $asset"
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || fail "malformed sidecar digest for $tool $asset"

  key="${tool}|${version}|${triple}"
  [[ "$seen_keys" != *$'\n'"$key"$'\n'* ]] || fail "duplicate sidecar manifest key: $key"
  seen_keys+="${key}"$'\n'

  [[ "$(resolve_sidecar "$tool" "$version" "$triple")" == "${asset}|${digest}" ]] || \
    fail "sidecar lookup drift for $key"
  [[ "$(sidecar_pinned_version "$tool")" == "$version" ]] || \
    fail "sidecar version drift for $tool"

  case "$tool" in
    opencode)
      [[ "$opencode_triples" != *$'\n'"$triple"$'\n'* ]] || \
        fail "duplicate OpenCode target triple: $triple"
      opencode_triples+="${triple}"$'\n'
      opencode_count=$((opencode_count + 1))
      ;;
    uv)
      [[ "$uv_triples" != *$'\n'"$triple"$'\n'* ]] || \
        fail "duplicate uv target triple: $triple"
      uv_triples+="${triple}"$'\n'
      uv_count=$((uv_count + 1))
      ;;
    *) fail "unknown tool in sidecar manifest: $tool" ;;
  esac
done
[[ "$opencode_count" -eq 6 && "$uv_count" -eq 6 ]] || \
  fail "expected six OpenCode and six uv assets"

sdk_opencode_version="$(sed -n 's/^export const OPENCODE_VERSION = "\([^"]*\)";$/\1/p' \
  "$ROOT/packages/sdk/src/types.ts")"
[[ -n "$sdk_opencode_version" && \
  "$sdk_opencode_version" == "$(sidecar_pinned_version opencode)" ]] || \
  fail "SDK and release manifest OpenCode versions differ"

check_desktop_release_version \
  "$ROOT" \
  "${GITHUB_REF_TYPE:-}" \
  "${GITHUB_REF_NAME:-}"

IFS='|' read -r sample_tool sample_version sample_triple _ \
  <<<"${SIDECAR_ASSET_MANIFEST[0]}"
if resolve_sidecar "$sample_tool" 0.0.0 "$sample_triple" >/dev/null 2>&1; then
  fail "unknown sidecar versions must fail closed"
fi
if resolve_sidecar "$sample_tool" "$sample_version" unknown-target >/dev/null 2>&1; then
  fail "unknown sidecar target triples must fail closed"
fi
if resolve_sidecar unknown "$sample_version" "$sample_triple" >/dev/null 2>&1; then
  fail "unknown sidecar tools must fail closed"
fi
if sidecar_pinned_version unknown >/dev/null 2>&1; then
  fail "unknown sidecar tools must not have a pinned version"
fi

[[ "${#SKILLS_ARCHIVE_MANIFEST[@]}" -eq 2 ]] || \
  fail "expected two skills archive manifest records"
for skills_record in "${SKILLS_ARCHIVE_MANIFEST[@]}"; do
  IFS='|' read -r skills_pack skills_commit skills_digest skills_extra \
    <<<"$skills_record"
  [[ "$skills_pack" =~ ^[a-z0-9-]+$ && "$skills_commit" =~ ^[0-9a-f]{40}$ &&
    "$skills_digest" =~ ^[0-9a-f]{64}$ && -z "$skills_extra" ]] || \
    fail "malformed skills archive manifest record"
  [[ "$(skills_pinned_commit "$skills_pack")" == "$skills_commit" ]] || \
    fail "skills commit lookup drift"
  [[ "$(skills_archive_sha256 "$skills_pack" "$skills_commit")" == "$skills_digest" ]] || \
    fail "skills digest lookup drift"
  if skills_archive_sha256 "$skills_pack" 0000000000000000000000000000000000000000 \
    >/dev/null 2>&1; then
    fail "unknown skills commits must fail closed"
  fi
done

# Keep this list aligned with the skill resources in
# apps/desktop/src-tauri/tauri.conf.json. Restricting the roots avoids scanning
# repository metadata and unrelated directories that are not release inputs.
packaged_skill_sources=(
  "$ROOT/runtime/skills/core"
  "$ROOT/runtime/skills/external/ai4s-skills"
  "$ROOT/runtime/skills/external/kdense-scientific-agent-skills"
)
for skill_source in "${packaged_skill_sources[@]}"; do
  check_packaged_skill_bytecode "$skill_source"
done

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT

version_fixture="$test_root/release-version"
mkdir -p "$version_fixture/apps/desktop/src-tauri"
printf '{"version":"2.3.4"}\n' >"$version_fixture/package.json"
printf '{"version":"2.3.4"}\n' >"$version_fixture/apps/desktop/package.json"
printf '{"version":"2.3.4"}\n' >"$version_fixture/apps/desktop/src-tauri/tauri.conf.json"
printf '[package]\nversion = "2.3.4"\n' >"$version_fixture/apps/desktop/src-tauri/Cargo.toml"
check_desktop_release_version "$version_fixture" tag v2.3.4
if (check_desktop_release_version "$version_fixture" tag v2.3.5) >/dev/null 2>&1; then
  fail "desktop release version check accepted a mismatched tag"
fi
printf '{"version":"2.3.5"}\n' >"$version_fixture/apps/desktop/package.json"
if (check_desktop_release_version "$version_fixture" branch main) >/dev/null 2>&1; then
  fail "desktop release version check accepted version drift"
fi

bytecode_fixture="$test_root/packaged-skill-bytecode"
mkdir -p "$bytecode_fixture/clean/skill" "$bytecode_fixture/clean/.git/objects"
printf 'print("source")\n' >"$bytecode_fixture/clean/skill/helper.py"
printf 'ignored repository metadata\n' >"$bytecode_fixture/clean/.git/objects/ignored.pyc"
check_packaged_skill_bytecode "$bytecode_fixture/clean"

mkdir -p "$bytecode_fixture/not-packaged/__pycache__"
printf 'not a release input\n' >"$bytecode_fixture/not-packaged/__pycache__/ignored.pyc"
check_packaged_skill_bytecode "$bytecode_fixture/clean"

for contaminant in __pycache__ helper.pyc helper.pyo; do
  dirty_skill_source="$bytecode_fixture/dirty-$contaminant"
  mkdir -p "$dirty_skill_source"
  if [[ "$contaminant" == __pycache__ ]]; then
    mkdir "$dirty_skill_source/$contaminant"
  else
    printf 'compiled bytecode\n' >"$dirty_skill_source/$contaminant"
  fi
  if (check_packaged_skill_bytecode "$dirty_skill_source") >/dev/null 2>&1; then
    fail "packaged skill bytecode check accepted $contaminant"
  fi
done

unknown_target_bin="$test_root/unknown-target-bin"
mkdir -p "$unknown_target_bin"
# shellcheck disable=SC2016 # These literal lines generate a child script.
printf '%s\n' \
  '#!/usr/bin/env bash' \
  ': "${UNKNOWN_TARGET_CURL_MARKER:?}"' \
  ': >"$UNKNOWN_TARGET_CURL_MARKER"' \
  'exit 98' \
  >"$unknown_target_bin/curl"
chmod 755 "$unknown_target_bin/curl"
for fetch_script in fetch-opencode.sh fetch-uv.sh; do
  marker="$test_root/${fetch_script}.curl-invoked"
  if PATH="$unknown_target_bin:$PATH" UNKNOWN_TARGET_CURL_MARKER="$marker" \
    bash "$ROOT/scripts/dev/$fetch_script" unknown-target >/dev/null 2>&1; then
    fail "$fetch_script accepted an unknown target triple"
  fi
  [[ ! -e "$marker" ]] || fail "$fetch_script downloaded an unknown target triple"
done

skills_override_marker="$test_root/skills-override.curl-invoked"
if PATH="$unknown_target_bin:$PATH" \
  UNKNOWN_TARGET_CURL_MARKER="$skills_override_marker" \
  AI4S_SKILLS_OUT_DIR="$test_root/unapproved-skills-output" \
  bash "$ROOT/scripts/dev/fetch-skills.sh" >/dev/null 2>&1; then
  fail "fetch-skills accepted an unapproved output override"
fi
[[ ! -e "$skills_override_marker" ]] || \
  fail "fetch-skills downloaded before rejecting an unapproved output override"

fixture="$test_root/checksum-fixture"
printf 'abc' >"$fixture"
verify_sha256 \
  "$fixture" \
  'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad' \
  >/dev/null
if verify_sha256 "$fixture" invalid-digest >/dev/null 2>&1; then
  fail "malformed trusted digests must fail closed"
fi
printf 'tampered' >"$fixture"
if verify_sha256 \
  "$fixture" \
  'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad' \
  >/dev/null 2>&1; then
  fail "tampered content must be rejected"
fi

atomic_dir="$test_root/atomic"
mkdir -p "$atomic_dir"
atomic_source="$atomic_dir/source"
atomic_destination="$atomic_dir/sidecar"
printf 'replacement' >"$atomic_source"
install_sidecar_atomically "$atomic_source" "$atomic_destination"
cmp -s "$atomic_source" "$atomic_destination" || fail "atomic sidecar install changed content"
[[ -x "$atomic_destination" ]] || fail "atomic sidecar install did not preserve executability"

printf 'preserved' >"$atomic_destination"
printf 'preserved' >"$atomic_dir/expected"
fake_cp_dir="$test_root/fake-cp"
mkdir -p "$fake_cp_dir"
printf '%s\n' '#!/usr/bin/env bash' 'exit 72' >"$fake_cp_dir/cp"
chmod 755 "$fake_cp_dir/cp"
if PATH="$fake_cp_dir:$PATH" \
  install_sidecar_atomically "$atomic_source" "$atomic_destination" >/dev/null 2>&1; then
  fail "injected atomic copy failure unexpectedly succeeded"
fi
cmp -s "$atomic_dir/expected" "$atomic_destination" || \
  fail "failed atomic copy damaged the existing sidecar"
if find "$atomic_dir" -maxdepth 1 -name '.sidecar.install.*' -print -quit | grep -q .; then
  fail "failed atomic copy left a temporary file"
fi

fake_mv_dir="$test_root/fake-mv"
mkdir -p "$fake_mv_dir"
printf '%s\n' '#!/usr/bin/env bash' 'exit 73' >"$fake_mv_dir/mv"
chmod 755 "$fake_mv_dir/mv"
if PATH="$fake_mv_dir:$PATH" \
  install_sidecar_atomically "$atomic_source" "$atomic_destination" >/dev/null 2>&1; then
  fail "injected atomic rename failure unexpectedly succeeded"
fi
cmp -s "$atomic_dir/expected" "$atomic_destination" || \
  fail "failed atomic install damaged the existing sidecar"
if find "$atomic_dir" -maxdepth 1 -name '.sidecar.install.*' -print -quit | grep -q .; then
  fail "failed atomic install left a temporary file"
fi

transaction_dir="$test_root/transaction"
transaction_destination="$transaction_dir/skills"
transaction_staging="$transaction_dir/.skills.staging.$$.fault"
mkdir -p "$transaction_destination" "$transaction_staging"
printf 'previous tree' >"$transaction_destination/marker"
printf 'replacement tree' >"$transaction_staging/marker"
transaction_expected="$transaction_dir/expected"
printf 'previous tree' >"$transaction_expected"
transaction_fake_bin="$test_root/fake-transaction-mv"
mkdir -p "$transaction_fake_bin"
transaction_real_mv="$(command -v mv)"
# shellcheck disable=SC2016 # These literal lines generate a child script.
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'owner=$(cat "$TRANSACTION_LOCK/owner")' \
  '[[ "$owner" == "$PPID" ]] || exit 76' \
  'count=0' \
  'if [[ -f "$TRANSACTION_MV_COUNT" ]]; then read -r count <"$TRANSACTION_MV_COUNT"; fi' \
  'count=$((count + 1))' \
  'printf "%s\n" "$count" >"$TRANSACTION_MV_COUNT"' \
  'if [[ "$count" -eq "$TRANSACTION_MV_FAIL_AT" ]]; then exit 75; fi' \
  'exec "$TRANSACTION_REAL_MV" "$@"' \
  >"$transaction_fake_bin/mv"
chmod 755 "$transaction_fake_bin/mv"
if (
  export PATH="$transaction_fake_bin:$PATH"
  export TRANSACTION_MV_COUNT="$transaction_dir/mv-count"
  export TRANSACTION_MV_FAIL_AT=2
  export TRANSACTION_REAL_MV="$transaction_real_mv"
  export TRANSACTION_LOCK="$transaction_dir/.skills.install.lock"
  install_directory_transactionally "$transaction_staging" "$transaction_destination" \
    >/dev/null 2>&1
); then
  fail "injected skills transaction rename failure unexpectedly succeeded"
fi
[[ -f "$transaction_dir/mv-count" && "$(cat "$transaction_dir/mv-count")" == 2 ]] || \
  fail "transaction owner journal did not identify the active Bash 3.2 subshell"
[[ ! -e "$transaction_destination" && -d "$transaction_staging" && \
  -d "$transaction_dir/.skills.install.lock" ]] || \
  fail "failed publication did not retain recoverable transaction state"
recover_directory_transaction "$transaction_destination"
cmp -s "$transaction_expected" "$transaction_destination/marker" || \
  fail "failed skills transaction did not restore the previous tree"
[[ ! -e "$transaction_staging" ]] || \
  fail "failed skills transaction recovery retained abandoned staging"
if find "$transaction_dir" -maxdepth 1 \
  \( -name '.skills.backup.*' -o -name '.skills.install.lock' \) \
  -print -quit | grep -q .; then
  fail "rolled-back skills transaction left backup or lock state"
fi

transaction_lock="$transaction_dir/.skills.install.lock"
mkdir "$transaction_staging"
printf 'replacement tree' >"$transaction_staging/marker"
mkdir "$transaction_lock"
if install_directory_transactionally "$transaction_staging" "$transaction_destination" \
  >/dev/null 2>&1; then
  fail "skills transaction ignored an existing install lock"
fi
cmp -s "$transaction_expected" "$transaction_destination/marker" || \
  fail "lock contention changed the existing skills tree"
[[ -d "$transaction_staging" ]] || fail "lock contention changed the staging tree"
rmdir "$transaction_lock"

symlink_dir="$test_root/transaction-symlink-staging"
symlink_destination="$symlink_dir/skills"
symlink_target="$symlink_dir/candidate-target"
symlink_staging="$symlink_dir/.skills.staging.$$.symlink"
mkdir -p "$symlink_destination" "$symlink_target"
printf 'accepted tree' >"$symlink_destination/marker"
printf 'symlink candidate' >"$symlink_target/marker"
ln -s "$symlink_target" "$symlink_staging"
[[ -L "$symlink_staging" ]] || \
  fail "test environment could not create a real staging-root symlink"
if install_directory_transactionally "$symlink_staging" "$symlink_destination" \
  >/dev/null 2>&1; then
  fail "skills transaction accepted a staging-root symlink"
fi
[[ "$(cat "$symlink_destination/marker")" == 'accepted tree' ]] || \
  fail "staging-root symlink changed the existing destination"

swap_dir="$test_root/transaction-staging-swap"
swap_destination="$swap_dir/skills"
swap_staging="$swap_dir/.skills.staging.$$.swap"
swap_hidden="$swap_dir/.skills.staging.$$.hidden"
swap_target="$swap_dir/attacker-target"
swap_fake_bin="$swap_dir/fake-bin"
mkdir -p "$swap_destination" "$swap_staging" "$swap_target" "$swap_fake_bin"
printf 'accepted tree' >"$swap_destination/marker"
printf 'verified candidate' >"$swap_staging/marker"
printf 'symlink replacement' >"$swap_target/marker"
# Exchange the staging root for a directory symlink immediately after the old
# destination moves aside. The pre-publication recheck must reject the swap and
# restore the accepted tree.
# shellcheck disable=SC2016 # These literal lines generate a child script.
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'count=0' \
  'if [[ -f "$STAGING_SWAP_COUNT" ]]; then read -r count <"$STAGING_SWAP_COUNT"; fi' \
  'count=$((count + 1))' \
  'printf "%s\n" "$count" >"$STAGING_SWAP_COUNT"' \
  '"$TRANSACTION_REAL_MV" "$@"' \
  'if [[ "$count" -eq 1 ]]; then' \
  '  "$TRANSACTION_REAL_MV" "$STAGING_SWAP_SOURCE" "$STAGING_SWAP_HIDDEN"' \
  '  ln -s "$STAGING_SWAP_TARGET" "$STAGING_SWAP_SOURCE"' \
  'fi' \
  >"$swap_fake_bin/mv"
chmod 755 "$swap_fake_bin/mv"
if (
  export PATH="$swap_fake_bin:$PATH"
  export TRANSACTION_REAL_MV="$transaction_real_mv"
  export STAGING_SWAP_COUNT="$swap_dir/mv-count"
  export STAGING_SWAP_SOURCE="$swap_staging"
  export STAGING_SWAP_HIDDEN="$swap_hidden"
  export STAGING_SWAP_TARGET="$swap_target"
  install_directory_transactionally "$swap_staging" "$swap_destination" \
    >/dev/null 2>&1
); then
  fail "skills transaction published a staging root swapped to a symlink"
fi
[[ "$(cat "$swap_destination/marker")" == 'accepted tree' ]] || \
  fail "staging-root swap did not restore the accepted tree"
[[ -L "$swap_staging" && -d "$swap_hidden" ]] || \
  fail "staging-root swap fixture did not reach the pre-publication check"

term_fake_bin="$test_root/transaction-term-fake-bin"
mkdir -p "$term_fake_bin"
# Complete a selected directory rename and then deliver a catchable signal in
# the former post-mv/pre-assignment window.
# shellcheck disable=SC2016 # These literal lines generate a child script.
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'count=0' \
  'if [[ -f "$TERM_MV_COUNT" ]]; then read -r count <"$TERM_MV_COUNT"; fi' \
  'count=$((count + 1))' \
  'printf "%s\n" "$count" >"$TERM_MV_COUNT"' \
  '"$TRANSACTION_REAL_MV" "$@"' \
  'if [[ "$count" -eq "$TERM_AFTER_MV_AT" ]]; then kill -TERM "$PPID"; fi' \
  >"$term_fake_bin/mv"
chmod 755 "$term_fake_bin/mv"

term_old_dir="$test_root/transaction-term-after-old-move"
term_old_destination="$term_old_dir/skills"
term_old_staging="$term_old_dir/.skills.staging.$$.term-old"
mkdir -p "$term_old_destination" "$term_old_staging"
printf 'accepted previous tree' >"$term_old_destination/marker"
printf 'candidate tree' >"$term_old_staging/marker"
term_old_status=0
(
  export PATH="$term_fake_bin:$PATH"
  export TRANSACTION_REAL_MV="$transaction_real_mv"
  export TERM_MV_COUNT="$term_old_dir/mv-count"
  export TERM_AFTER_MV_AT=1
  install_directory_transactionally "$term_old_staging" "$term_old_destination" \
    >/dev/null 2>&1
) || term_old_status=$?
[[ "$term_old_status" -ne 0 && \
  "$(cat "$term_old_destination/marker")" == 'accepted previous tree' && \
  -d "$term_old_staging" ]] || \
  fail "TERM after the old-tree rename lost the accepted tree"
if find "$term_old_dir" -maxdepth 1 \
  \( -name '.skills.backup.*' -o -name '.skills.install.lock' \) \
  -print -quit | grep -q .; then
  fail "TERM after the old-tree rename left unnecessary transaction state"
fi

term_new_dir="$test_root/transaction-term-after-new-move"
term_new_destination="$term_new_dir/skills"
term_new_staging="$term_new_dir/.skills.staging.$$.term-new"
mkdir -p "$term_new_destination" "$term_new_staging"
printf 'accepted previous tree' >"$term_new_destination/marker"
printf 'uncommitted candidate' >"$term_new_staging/marker"
term_new_status=0
(
  export PATH="$term_fake_bin:$PATH"
  export TRANSACTION_REAL_MV="$transaction_real_mv"
  export TERM_MV_COUNT="$term_new_dir/mv-count"
  export TERM_AFTER_MV_AT=2
  install_directory_transactionally "$term_new_staging" "$term_new_destination" \
    >/dev/null 2>&1
) || term_new_status=$?
[[ "$term_new_status" -ne 0 && \
  "$(cat "$term_new_destination/marker")" == 'uncommitted candidate' && \
  -d "$term_new_dir/.skills.install.lock" ]] || \
  fail "TERM after candidate publication did not retain recoverable state"
recover_directory_transaction "$term_new_destination"
[[ "$(cat "$term_new_destination/marker")" == 'accepted previous tree' ]] || \
  fail "TERM after candidate publication did not roll back the candidate"
if find "$term_new_dir" -maxdepth 1 \
  \( -name '.skills.staging.*' -o -name '.skills.backup.*' -o \
  -name '.skills.install.lock' \) -print -quit | grep -q .; then
  fail "TERM after candidate publication recovery left transaction state"
fi

crash_dir="$test_root/transaction-sigkill"
crash_destination="$crash_dir/skills"
crash_staging="$crash_dir/.skills.staging.$$.sigkill"
crash_fake_bin="$crash_dir/fake-bin"
mkdir -p "$crash_destination" "$crash_staging" "$crash_fake_bin"
printf 'accepted previous tree' >"$crash_destination/marker"
printf 'abandoned candidate' >"$crash_staging/marker"
# The fake mv completes the first rename and then SIGKILLs the transaction
# subshell. EXIT traps cannot run, so this exercises process-crash journal
# recovery rather than the in-process rollback path. It does not simulate a
# power loss or claim fsync-backed durability.
# shellcheck disable=SC2016 # These literal lines generate a child script.
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  '"$TRANSACTION_REAL_MV" "$@"' \
  'kill -9 "$PPID"' \
  'exit 99' \
  >"$crash_fake_bin/mv"
chmod 755 "$crash_fake_bin/mv"
crash_status=0
(
  export PATH="$crash_fake_bin:$PATH"
  export TRANSACTION_REAL_MV="$transaction_real_mv"
  install_directory_transactionally "$crash_staging" "$crash_destination" \
    >/dev/null 2>&1
) || crash_status=$?
[[ "$crash_status" -ne 0 ]] || fail "SIGKILL transaction injection unexpectedly succeeded"
[[ ! -e "$crash_destination" && -d "$crash_dir/.skills.install.lock" ]] || \
  fail "SIGKILL transaction injection did not leave the expected journal state"
recover_directory_transaction "$crash_destination"
[[ "$(cat "$crash_destination/marker")" == 'accepted previous tree' ]] || \
  fail "SIGKILL recovery did not restore the previous tree"
if find "$crash_dir" -maxdepth 1 \
  \( -name '.skills.staging.*' -o -name '.skills.backup.*' -o \
  -name '.skills.install.lock' \) -print -quit | grep -q .; then
  fail "SIGKILL recovery left transaction state"
fi

cleanup_failure_dir="$test_root/transaction-cleanup-failure"
cleanup_failure_destination="$cleanup_failure_dir/skills"
cleanup_failure_staging="$cleanup_failure_dir/.skills.staging.$$.cleanup-failure"
cleanup_failure_fake_bin="$cleanup_failure_dir/fake-bin"
mkdir -p \
  "$cleanup_failure_destination" \
  "$cleanup_failure_staging" \
  "$cleanup_failure_fake_bin"
printf 'accepted previous tree' >"$cleanup_failure_destination/old-marker"
printf 'complete verified candidate' >"$cleanup_failure_staging/marker"
transaction_real_rm="$(command -v rm)"
# shellcheck disable=SC2016 # These literal lines generate a child script.
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'for path in "$@"; do' \
  '  case "$path" in' \
  '    */.skills.backup.*) exit 77 ;;' \
  '  esac' \
  'done' \
  'exec "$TRANSACTION_REAL_RM" "$@"' \
  >"$cleanup_failure_fake_bin/rm"
chmod 755 "$cleanup_failure_fake_bin/rm"
if (
  export PATH="$cleanup_failure_fake_bin:$PATH"
  export TRANSACTION_REAL_RM="$transaction_real_rm"
  install_directory_transactionally \
    "$cleanup_failure_staging" "$cleanup_failure_destination" \
    >/dev/null 2>&1
); then
  fail "injected transaction backup cleanup failure unexpectedly succeeded"
fi
cleanup_failure_lock="$cleanup_failure_dir/.skills.install.lock"
[[ -d "$cleanup_failure_lock" && \
  "$(cat "$cleanup_failure_lock/phase")" == cleanup ]] || \
  fail "backup cleanup failure removed its transaction lock"
if ! find "$cleanup_failure_dir" -maxdepth 1 -type d \
  -name '.skills.backup.*' -print -quit | grep -q .; then
  fail "backup cleanup failure did not retain its linked backup"
fi
[[ "$(cat "$cleanup_failure_destination/marker")" == \
  'complete verified candidate' ]] || \
  fail "backup cleanup failure changed the committed destination"
recover_directory_transaction "$cleanup_failure_destination"
if find "$cleanup_failure_dir" -maxdepth 1 \
  \( -name '.skills.backup.*' -o -name '.skills.install.lock' \) \
  -print -quit | grep -q .; then
  fail "backup cleanup retry left transaction state"
fi

cleanup_crash_dir="$test_root/transaction-cleanup-sigkill"
cleanup_crash_destination="$cleanup_crash_dir/skills"
cleanup_crash_staging="$cleanup_crash_dir/.skills.staging.$$.cleanup"
cleanup_crash_fake_bin="$cleanup_crash_dir/fake-bin"
mkdir -p \
  "$cleanup_crash_destination" \
  "$cleanup_crash_staging" \
  "$cleanup_crash_fake_bin"
printf 'old tree fragment retained' >"$cleanup_crash_destination/old-retained"
printf 'old tree fragment deleted' >"$cleanup_crash_destination/old-deleted"
printf 'complete verified candidate' >"$cleanup_crash_staging/marker"
# Interrupt recursive backup deletion after removing one old-tree file. The
# recorded cleanup phase must make the partial backup disposal-only: recovery
# keeps the complete committed destination and never restores the fragment.
# shellcheck disable=SC2016 # These literal lines generate a child script.
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'for path in "$@"; do' \
  '  case "$path" in' \
  '    */.skills.backup.*)' \
  '      "$TRANSACTION_REAL_RM" -f "$path/previous/old-deleted"' \
  '      kill -9 "$PPID"' \
  '      exit 99' \
  '      ;;' \
  '  esac' \
  'done' \
  'exec "$TRANSACTION_REAL_RM" "$@"' \
  >"$cleanup_crash_fake_bin/rm"
chmod 755 "$cleanup_crash_fake_bin/rm"
cleanup_crash_status=0
(
  export PATH="$cleanup_crash_fake_bin:$PATH"
  export TRANSACTION_REAL_RM="$transaction_real_rm"
  install_directory_transactionally \
    "$cleanup_crash_staging" "$cleanup_crash_destination" \
    >/dev/null 2>&1
) || cleanup_crash_status=$?
[[ "$cleanup_crash_status" -ne 0 ]] || \
  fail "backup-cleanup SIGKILL injection unexpectedly succeeded"
cleanup_crash_lock="$cleanup_crash_dir/.skills.install.lock"
[[ -d "$cleanup_crash_lock" && "$(cat "$cleanup_crash_lock/phase")" == cleanup ]] || \
  fail "backup-cleanup SIGKILL did not retain the cleanup phase"
cleanup_crash_backup="$(find "$cleanup_crash_dir" -maxdepth 1 \
  -type d -name '.skills.backup.*' -print -quit)"
[[ -n "$cleanup_crash_backup" && \
  -f "$cleanup_crash_backup/previous/old-retained" && \
  ! -e "$cleanup_crash_backup/previous/old-deleted" ]] || \
  fail "backup-cleanup SIGKILL did not leave the expected partial backup"
[[ "$(cat "$cleanup_crash_destination/marker")" == 'complete verified candidate' ]] || \
  fail "backup-cleanup SIGKILL changed the committed candidate"
recover_directory_transaction "$cleanup_crash_destination"
[[ "$(cat "$cleanup_crash_destination/marker")" == 'complete verified candidate' ]] || \
  fail "partial-backup recovery replaced the committed candidate"
[[ ! -e "$cleanup_crash_destination/old-retained" ]] || \
  fail "partial-backup recovery restored an old-tree fragment"
if find "$cleanup_crash_dir" -maxdepth 1 \
  \( -name '.skills.staging.*' -o -name '.skills.backup.*' -o \
  -name '.skills.install.lock' \) -print -quit | grep -q .; then
  fail "partial-backup recovery left transaction state"
fi

# Reconstruct the process-crash states left by an untrappable kill at each rename
# boundary. Recovery must roll back to `backup/previous`, never promote the
# abandoned new tree, and remove only journal-linked state.
dead_owner=2147483647

stale_claim_root="$test_root/recovery-stale-claim"
stale_claim_destination="$stale_claim_root/skills"
stale_claim_staging_name=".skills.staging.${dead_owner}.stale-claim"
stale_claim_backup_name=".skills.backup.stale-claim"
stale_claim_lock="$stale_claim_root/.skills.install.lock"
mkdir -p \
  "$stale_claim_root/$stale_claim_staging_name" \
  "$stale_claim_root/$stale_claim_backup_name/previous"
printf 'abandoned candidate' >"$stale_claim_root/$stale_claim_staging_name/marker"
printf 'accepted previous tree' \
  >"$stale_claim_root/$stale_claim_backup_name/previous/marker"
write_transaction_journal_fixture \
  "$stale_claim_destination" "$dead_owner" \
  "$stale_claim_staging_name" "$stale_claim_backup_name" 1 old-moved
mkdir "$stale_claim_lock/recovery"
printf '%s\n' "$dead_owner" >"$stale_claim_lock/recovery/owner"
if recover_directory_transaction "$stale_claim_destination" >/dev/null 2>&1; then
  fail "recovery stole an existing stale claim"
fi
[[ "$(cat "$stale_claim_lock/recovery/owner")" == "$dead_owner" && \
  -f "$stale_claim_root/$stale_claim_backup_name/previous/marker" && \
  -f "$stale_claim_root/$stale_claim_staging_name/marker" && \
  ! -e "$stale_claim_destination" ]] || \
  fail "stale recovery claim did not preserve fail-closed transaction state"

recovery_between="$test_root/recovery-between-renames"
recovery_between_destination="$recovery_between/skills"
recovery_between_staging_name=".skills.staging.${dead_owner}.between"
recovery_between_backup_name=".skills.backup.between"
mkdir -p \
  "$recovery_between/$recovery_between_staging_name" \
  "$recovery_between/$recovery_between_backup_name/previous"
printf 'abandoned new tree' >"$recovery_between/$recovery_between_staging_name/marker"
printf 'accepted previous tree' \
  >"$recovery_between/$recovery_between_backup_name/previous/marker"
write_transaction_journal_fixture \
  "$recovery_between_destination" "$dead_owner" \
  "$recovery_between_staging_name" "$recovery_between_backup_name" 1 old-moved
recover_directory_transaction "$recovery_between_destination"
[[ "$(cat "$recovery_between_destination/marker")" == 'accepted previous tree' ]] || \
  fail "between-renames recovery did not restore the previous tree"
if find "$recovery_between" -maxdepth 1 \
  \( -name '.skills.staging.*' -o -name '.skills.backup.*' -o \
  -name '.skills.install.lock' \) -print -quit | grep -q .; then
  fail "between-renames recovery left transaction state"
fi

recovery_after="$test_root/recovery-after-second-rename"
recovery_after_destination="$recovery_after/skills"
recovery_after_staging_name=".skills.staging.${dead_owner}.after"
recovery_after_backup_name=".skills.backup.after"
mkdir -p "$recovery_after_destination" "$recovery_after/$recovery_after_backup_name/previous"
printf 'installed candidate' >"$recovery_after_destination/marker"
printf 'accepted previous tree' >"$recovery_after/$recovery_after_backup_name/previous/marker"
write_transaction_journal_fixture \
  "$recovery_after_destination" "$dead_owner" \
  "$recovery_after_staging_name" "$recovery_after_backup_name" 1 new-installed
recover_directory_transaction "$recovery_after_destination"
[[ "$(cat "$recovery_after_destination/marker")" == 'accepted previous tree' ]] || \
  fail "post-rename recovery accepted the new candidate instead of rolling back"
if find "$recovery_after" -maxdepth 1 \
  \( -name '.skills.staging.*' -o -name '.skills.backup.*' -o \
  -name '.skills.install.lock' \) -print -quit | grep -q .; then
  fail "post-rename recovery left transaction state"
fi

recovery_fresh="$test_root/recovery-fresh-install"
recovery_fresh_destination="$recovery_fresh/skills"
recovery_fresh_staging_name=".skills.staging.${dead_owner}.fresh"
recovery_fresh_backup_name=".skills.backup.fresh"
mkdir -p "$recovery_fresh_destination" "$recovery_fresh/$recovery_fresh_backup_name"
printf 'untrusted fresh candidate' >"$recovery_fresh_destination/marker"
write_transaction_journal_fixture \
  "$recovery_fresh_destination" "$dead_owner" \
  "$recovery_fresh_staging_name" "$recovery_fresh_backup_name" 0 new-installed
recover_directory_transaction "$recovery_fresh_destination"
[[ ! -e "$recovery_fresh_destination" ]] || \
  fail "fresh-install recovery accepted an abandoned candidate"
if find "$recovery_fresh" -maxdepth 1 \
  \( -name '.skills.staging.*' -o -name '.skills.backup.*' -o \
  -name '.skills.install.lock' \) -print -quit | grep -q .; then
  fail "fresh-install recovery left transaction state"
fi

recovery_interrupted="$test_root/recovery-interrupted-recovery"
recovery_interrupted_destination="$recovery_interrupted/skills"
recovery_interrupted_staging_name=".skills.staging.${dead_owner}.quarantine"
recovery_interrupted_backup_name=".skills.backup.recovering"
mkdir -p \
  "$recovery_interrupted_destination" \
  "$recovery_interrupted/$recovery_interrupted_staging_name" \
  "$recovery_interrupted/$recovery_interrupted_backup_name"
printf 'accepted previous tree' >"$recovery_interrupted_destination/marker"
printf 'quarantined candidate' >"$recovery_interrupted/$recovery_interrupted_staging_name/marker"
write_transaction_journal_fixture \
  "$recovery_interrupted_destination" "$dead_owner" \
  "$recovery_interrupted_staging_name" "$recovery_interrupted_backup_name" 1 recovering
recover_directory_transaction "$recovery_interrupted_destination"
[[ "$(cat "$recovery_interrupted_destination/marker")" == 'accepted previous tree' ]] || \
  fail "resumed recovery changed the restored previous tree"
[[ ! -e "$recovery_interrupted/$recovery_interrupted_staging_name" ]] || \
  fail "resumed recovery retained the quarantined candidate"

recovery_restored="$test_root/recovery-restored-complete"
recovery_restored_destination="$recovery_restored/skills"
recovery_restored_staging_name=".skills.staging.${dead_owner}.restored"
recovery_restored_backup_name=".skills.backup.restored"
mkdir -p \
  "$recovery_restored_destination" \
  "$recovery_restored/$recovery_restored_backup_name"
printf 'accepted restored tree' >"$recovery_restored_destination/marker"
write_transaction_journal_fixture \
  "$recovery_restored_destination" "$dead_owner" \
  "$recovery_restored_staging_name" "$recovery_restored_backup_name" 1 restored
recover_directory_transaction "$recovery_restored_destination"
[[ "$(cat "$recovery_restored_destination/marker")" == \
  'accepted restored tree' ]] || \
  fail "idempotent restored recovery deleted the accepted tree"
if find "$recovery_restored" -maxdepth 1 \
  \( -name '.skills.backup.*' -o -name '.skills.install.lock' \) \
  -print -quit | grep -q .; then
  fail "idempotent restored recovery left transaction state"
fi

recovery_ambiguous="$test_root/recovery-ambiguous-recovering"
recovery_ambiguous_destination="$recovery_ambiguous/skills"
recovery_ambiguous_staging_name=".skills.staging.${dead_owner}.missing"
recovery_ambiguous_backup_name=".skills.backup.ambiguous"
mkdir -p \
  "$recovery_ambiguous_destination" \
  "$recovery_ambiguous/$recovery_ambiguous_backup_name"
printf 'physically ambiguous tree' >"$recovery_ambiguous_destination/marker"
write_transaction_journal_fixture \
  "$recovery_ambiguous_destination" "$dead_owner" \
  "$recovery_ambiguous_staging_name" "$recovery_ambiguous_backup_name" 1 recovering
if recover_directory_transaction "$recovery_ambiguous_destination" \
  >/dev/null 2>&1; then
  fail "recovery accepted an ambiguous recovering layout"
fi
[[ "$(cat "$recovery_ambiguous_destination/marker")" == \
  'physically ambiguous tree' && \
  -d "$recovery_ambiguous/.skills.install.lock" ]] || \
  fail "ambiguous recovering layout did not remain fail closed"

recovery_malformed="$test_root/recovery-malformed"
recovery_malformed_destination="$recovery_malformed/skills"
mkdir -p "$recovery_malformed_destination" "$recovery_malformed/.skills.backup.bad"
printf 'preserved tree' >"$recovery_malformed_destination/marker"
write_transaction_journal_fixture \
  "$recovery_malformed_destination" "$dead_owner" \
  '../../outside' '.skills.backup.bad' 1 old-moved
if recover_directory_transaction "$recovery_malformed_destination" >/dev/null 2>&1; then
  fail "recovery accepted path traversal in transaction metadata"
fi
[[ "$(cat "$recovery_malformed_destination/marker")" == 'preserved tree' ]] || \
  fail "malformed recovery metadata changed the destination"
[[ -d "$recovery_malformed/.skills.install.lock" ]] || \
  fail "malformed recovery metadata did not fail closed"

orphan_root="$test_root/recovery-orphan-staging"
orphan_destination="$orphan_root/skills"
dead_staging="$orphan_root/.skills.staging.${dead_owner}.orphan"
active_staging="$orphan_root/.skills.staging.$$.active"
mkdir -p "$dead_staging" "$active_staging"
recover_directory_transaction "$orphan_destination"
[[ ! -e "$dead_staging" ]] || fail "recovery retained dead orphan staging"
[[ -d "$active_staging" ]] || fail "recovery removed another live staging tree"
rm -rf "$active_staging"

# A fake downloader supplies tampered bytes. A fake tar records any attempted
# unpack. fetch-skills must reject the digest before tar runs or output changes.
fake_skills_bin="$test_root/fake-skills-bin"
mkdir -p "$fake_skills_bin"
# shellcheck disable=SC2016 # These literal lines generate a child script.
printf '%s\n' \
  '#!/usr/bin/env bash' \
  'set -euo pipefail' \
  'output=' \
  'while (($#)); do' \
  '  if [[ "$1" == -o ]]; then output="$2"; shift 2; else shift; fi' \
  'done' \
  ': "${SKILLS_TEST_ARCHIVE:?}" "${output:?}"' \
  'cp "$SKILLS_TEST_ARCHIVE" "$output"' \
  >"$fake_skills_bin/curl"
# shellcheck disable=SC2016 # These literal lines generate a child script.
printf '%s\n' \
  '#!/usr/bin/env bash' \
  ': "${SKILLS_TEST_TAR_MARKER:?}"' \
  ': >"$SKILLS_TEST_TAR_MARKER"' \
  'exit 99' \
  >"$fake_skills_bin/tar"
chmod 755 "$fake_skills_bin/curl" "$fake_skills_bin/tar"
printf 'tampered skills archive' >"$test_root/tampered-skills.tar.gz"
skills_output="$test_root/existing-skills"
mkdir -p "$skills_output"
printf 'preserved skills' >"$skills_output/sentinel"
printf 'preserved skills' >"$test_root/expected-skills"
if PATH="$fake_skills_bin:$PATH" \
  SKILLS_TEST_ARCHIVE="$test_root/tampered-skills.tar.gz" \
  SKILLS_TEST_TAR_MARKER="$test_root/tar-invoked" \
  SPARK_AGENT_TEST_ALLOW_SKILLS_OUT_DIR=1 \
  AI4S_SKILLS_OUT_DIR="$skills_output" \
  bash "$ROOT/scripts/dev/fetch-skills.sh" >/dev/null 2>&1; then
  fail "tampered skills archive unexpectedly succeeded"
fi
[[ ! -e "$test_root/tar-invoked" ]] || \
  fail "tampered skills archive reached unpacking"
cmp -s "$test_root/expected-skills" "$skills_output/sentinel" || \
  fail "tampered skills archive changed the existing output"

bash -n \
  "$ROOT/scripts/dev/sidecar-integrity.sh" \
  "$ROOT/scripts/dev/fetch-opencode.sh" \
  "$ROOT/scripts/dev/fetch-uv.sh" \
  "$ROOT/scripts/dev/fetch-skills.sh" \
  "$ROOT/scripts/dev/fetch-kdense-skills.sh" \
  "$ROOT/scripts/quality/check-release-assets.sh"

printf 'Release integrity policy passed for %d sidecars and %d skills archives.\n' \
  "${#SIDECAR_ASSET_MANIFEST[@]}" "${#SKILLS_ARCHIVE_MANIFEST[@]}"
