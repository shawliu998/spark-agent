#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../dev/sidecar-integrity.sh
source "$ROOT/scripts/dev/sidecar-integrity.sh"

fail() {
  printf 'Release asset integrity check failed: %s\n' "$1" >&2
  exit 1
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

[[ "${#SKILLS_ARCHIVE_MANIFEST[@]}" -eq 1 ]] || \
  fail "expected one skills archive manifest record"
IFS='|' read -r skills_pack skills_commit skills_digest skills_extra \
  <<<"${SKILLS_ARCHIVE_MANIFEST[0]}"
[[ "$skills_pack" == ai4s-skills && "$skills_commit" =~ ^[0-9a-f]{40}$ &&
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

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT

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
transaction_staging="$transaction_dir/.skills.staging"
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
  install_directory_transactionally "$transaction_staging" "$transaction_destination" \
    >/dev/null 2>&1
); then
  fail "injected skills transaction rename failure unexpectedly succeeded"
fi
cmp -s "$transaction_expected" "$transaction_destination/marker" || \
  fail "failed skills transaction did not restore the previous tree"
[[ -d "$transaction_staging" ]] || fail "failed skills transaction lost its staging tree"
if find "$transaction_dir" -maxdepth 1 \
  \( -name '.skills.backup.*' -o -name '.skills.install.lock' \) \
  -print -quit | grep -q .; then
  fail "rolled-back skills transaction left backup or lock state"
fi

transaction_lock="$transaction_dir/.skills.install.lock"
mkdir "$transaction_lock"
if install_directory_transactionally "$transaction_staging" "$transaction_destination" \
  >/dev/null 2>&1; then
  fail "skills transaction ignored an existing install lock"
fi
cmp -s "$transaction_expected" "$transaction_destination/marker" || \
  fail "lock contention changed the existing skills tree"
[[ -d "$transaction_staging" ]] || fail "lock contention changed the staging tree"
rmdir "$transaction_lock"

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
  "$ROOT/scripts/quality/check-release-assets.sh"

printf 'Release integrity policy passed for %d sidecars and %d skills archive.\n' \
  "${#SIDECAR_ASSET_MANIFEST[@]}" "${#SKILLS_ARCHIVE_MANIFEST[@]}"
