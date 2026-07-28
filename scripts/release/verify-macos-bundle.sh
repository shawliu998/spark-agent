#!/usr/bin/env bash
# Verify the macOS release inputs survived Tauri bundling unchanged. This is a
# release gate, not a best-effort diagnostic: a missing, wrong-target, or
# substituted runtime input must fail the build.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../dev/sidecar-integrity.sh
source "$ROOT/scripts/dev/sidecar-integrity.sh"
# shellcheck source=macos-release-lib.sh
source "$ROOT/scripts/release/macos-release-lib.sh"
INPUT_ROOT="$ROOT"
if [[ -n "${SPARK_AGENT_TEST_INPUT_ROOT:-}" ]]; then
  [[ "${SPARK_AGENT_TEST_ALLOW_INPUT_ROOT:-}" == 1 ]] || {
    printf 'macOS bundle verification failed: test input override is restricted\n' >&2
    exit 1
  }
  INPUT_ROOT="$SPARK_AGENT_TEST_INPUT_ROOT"
fi

fail() {
  printf 'macOS bundle verification failed: %s\n' "$1" >&2
  exit 1
}

usage() {
  cat >&2 <<'EOF'
Usage: scripts/release/verify-macos-bundle.sh --app <Spark Agent.app> --target <target-triple> --science-runtime-bundle <artifact-root>

The verifier is intentionally offline. It requires the fetched, target-specific
source sidecars under apps/desktop/src-tauri/binaries and compares their SHA-256
digests to the sidecars inside the supplied app bundle. The independent Science
runtime artifact root must contain exactly runtime/ and science-core-sbom/.
EOF
}

sha256() {
  local file="$1"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$file" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$file" | awk '{print $1}'
  else
    fail "no SHA-256 implementation is available"
  fi
}

canonical_path() {
  local path="$1"
  (cd -P "$(dirname "$path")" && printf '%s/%s\n' "$(pwd -P)" "$(basename "$path")")
}

require_regular_file_within() {
  local file="$1"
  local parent="$2"
  local label="$3"
  local resolved_file resolved_parent

  [[ -f "$file" && ! -L "$file" ]] || fail "$label is missing, not regular, or a symlink: $file"
  resolved_file="$(canonical_path "$file")"
  resolved_parent="$(canonical_path "$parent")"
  case "$resolved_file" in
    "$resolved_parent"/*) ;;
    *) fail "$label escapes its trusted directory: $file" ;;
  esac
}

require_target_architecture() {
  local binary="$1"
  local expected_arch="$2"
  local arches

  [[ -f "$binary" && -x "$binary" ]] || fail "missing executable: $binary"
  if command -v lipo >/dev/null 2>&1; then
    arches="$(lipo -archs "$binary" 2>/dev/null || true)"
  else
    arches="$(file -b "$binary" 2>/dev/null || true)"
  fi
  case " $arches " in
    *" $expected_arch "*|*" $expected_arch,"*|*"($expected_arch)"*) ;;
    *) fail "wrong architecture for $binary (expected $expected_arch; found: $arches)" ;;
  esac
}

bundle_executable_name() {
  local info_plist="$1"
  if command -v plutil >/dev/null 2>&1; then
    plutil -extract CFBundleExecutable raw -o - "$info_plist" 2>/dev/null || true
  fi
}

app_path=''
target=''
science_runtime_bundle=''
while (($#)); do
  case "$1" in
    --app)
      [[ $# -ge 2 ]] || fail "--app requires a value"
      app_path="$2"
      shift 2
      ;;
    --target)
      [[ $# -ge 2 ]] || fail "--target requires a value"
      target="$2"
      shift 2
      ;;
    --science-runtime-bundle)
      [[ $# -ge 2 ]] || fail "--science-runtime-bundle requires a value"
      science_runtime_bundle="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[[ -n "$app_path" && -n "$target" && -n "$science_runtime_bundle" ]] || {
  usage
  exit 64
}
[[ "$target" == aarch64-apple-darwin || "$target" == x86_64-apple-darwin ]] || \
  fail "unsupported macOS target: $target"
validate_science_runtime_bundle "$ROOT" "$science_runtime_bundle" "$target" || \
  fail "independent Science runtime bundle failed strict validation"
science_runtime_bundle="$(cd -P "$science_runtime_bundle" && pwd -P)"
[[ -d "$app_path" && ! -L "$app_path" ]] || fail "app bundle is missing or a symlink: $app_path"
if find -H "$app_path" -type l -print -quit | grep -q .; then
  fail "app bundle contains a symlink"
fi

case "$target" in
  aarch64-apple-darwin) expected_arch='arm64' ;;
  x86_64-apple-darwin) expected_arch='x86_64' ;;
esac

contents="$app_path/Contents"
info_plist="$contents/Info.plist"
[[ -f "$info_plist" ]] || fail "app Info.plist is missing"
bundle_metadata=()
while IFS= read -r metadata_line; do bundle_metadata+=("$metadata_line"); done < <(
  node -e '
    const c = require(process.argv[1]);
    console.log(c.identifier); console.log(c.productName); console.log(c.version); console.log(c.bundle.macOS.minimumSystemVersion);
  ' "$ROOT/apps/desktop/src-tauri/tauri.conf.json"
)
expected_identifier="${bundle_metadata[0]:-}"
expected_name="${bundle_metadata[1]:-}"
expected_version="${bundle_metadata[2]:-}"
expected_minimum_macos="${bundle_metadata[3]:-}"
[[ -n "$expected_identifier" && -n "$expected_name" && -n "$expected_version" && -n "$expected_minimum_macos" ]] || fail "could not derive bundle identity from tauri.conf.json"
[[ "$(plutil -extract CFBundleIdentifier raw -o - "$info_plist")" == "$expected_identifier" ]] || fail "unexpected bundle identifier"
[[ "$(plutil -extract CFBundleName raw -o - "$info_plist")" == "$expected_name" ]] || fail "unexpected bundle name"
[[ "$(plutil -extract CFBundleShortVersionString raw -o - "$info_plist")" == "$expected_version" ]] || fail "unexpected bundle version"
[[ "$(plutil -extract CFBundleVersion raw -o - "$info_plist")" == "$expected_version" ]] || fail "unexpected bundle build"
[[ "$(plutil -extract LSMinimumSystemVersion raw -o - "$info_plist")" == "$expected_minimum_macos" ]] || fail "unexpected minimum macOS version"
main_name="$(bundle_executable_name "$info_plist")"
[[ -n "$main_name" && "$main_name" != */* ]] || fail "invalid CFBundleExecutable"
expected_main_name="$(sed -n '/^\[package\]$/,/^\[/{s/^name[[:space:]]*=[[:space:]]*"\([^"]*\)"$/\1/p;}' \
  "$ROOT/apps/desktop/src-tauri/Cargo.toml" | head -n 1)"
[[ -n "$expected_main_name" && "$main_name" == "$expected_main_name" ]] || \
  fail "unexpected CFBundleExecutable: $main_name"
require_target_architecture "$contents/MacOS/$main_name" "$expected_arch"

source_bin_dir="$INPUT_ROOT/apps/desktop/src-tauri/binaries"
[[ -d "$source_bin_dir" && ! -L "$source_bin_dir" ]] || fail "trusted sidecar directory is missing or a symlink"
for sidecar in opencode uv; do
  source_sidecar="$source_bin_dir/$sidecar-$target"
  bundled_sidecar="$contents/MacOS/$sidecar"
  expected_binary="$(resolve_sidecar "$sidecar" "$(sidecar_pinned_version "$sidecar")" "$target" | awk -F'|' '{print $3}')"
  require_regular_file_within "$source_sidecar" "$source_bin_dir" "$sidecar source sidecar"
  verify_sha256 "$source_sidecar" "$expected_binary" || fail "$sidecar source sidecar does not match tracked digest"
  verify_sha256 "$bundled_sidecar" "$expected_binary" || fail "$sidecar bundled sidecar does not match tracked digest"
  require_target_architecture "$source_sidecar" "$expected_arch"
  require_target_architecture "$bundled_sidecar" "$expected_arch"
  [[ "$(sha256 "$source_sidecar")" == "$(sha256 "$bundled_sidecar")" ]] || \
    fail "$sidecar differs from its validated source sidecar"
done

verify_tree_manifest "$INPUT_ROOT/runtime/skills/external/ai4s-skills" "$ROOT/runtime/skills/ai4s-skills.manifest" || fail "external source skills tree differs from manifest"
verify_tree_manifest "$INPUT_ROOT/runtime/skills/core" "$ROOT/runtime/skills/core.manifest" || fail "core source skills tree differs from manifest"

for resource_dir in "$contents/Resources/skills" "$contents/Resources/skills-core"; do
  [[ -d "$resource_dir" && ! -L "$resource_dir" ]] || fail "missing skill resource: $resource_dir"
  find "$resource_dir" -type f -name SKILL.md -print -quit | grep -q . || \
    fail "skill resource contains no SKILL.md: $resource_dir"
done
verify_tree_manifest "$contents/Resources/skills" "$ROOT/runtime/skills/ai4s-skills.manifest" || fail "bundled external skills tree differs from manifest"
verify_tree_manifest "$contents/Resources/skills-core" "$ROOT/runtime/skills/core.manifest" || fail "bundled core skills tree differs from manifest"
verify_bundled_science_resources \
  "$ROOT" \
  "$science_runtime_bundle" \
  "$contents/Resources" \
  "$target" || fail "bundled Science runtime differs from its independent input"

printf 'macOS bundle verified: %s (%s)\n' "$app_path" "$target"
