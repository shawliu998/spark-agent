#!/usr/bin/env bash
# The only supported local macOS release build entry point. It fetches the
# pinned runtime inputs (unless explicitly offline), builds in a fresh target
# directory, and verifies the resulting app bundle before returning success.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DESKTOP_DIR="$ROOT/apps/desktop"
# shellcheck source=macos-release-lib.sh
source "$ROOT/scripts/release/macos-release-lib.sh"

fail() {
  printf 'macOS release build failed: %s\n' "$1" >&2
  exit 1
}

usage() {
  cat >&2 <<'EOF'
Usage: scripts/release/build-macos.sh --science-runtime-bundle <artifact-root> [--target <target-triple>] [--offline] [--verify-only --app <Spark Agent.app>]

Default mode downloads the pinned OpenCode, uv, and ai4s-skills release inputs,
then builds a fresh macOS app and DMG. --offline never fetches: it requires
already-validated local inputs and asks pnpm/Cargo to operate offline. --verify-only
is fully offline and only verifies an already-built app against those local inputs.
Every mode requires an independently supplied Science runtime artifact root that
contains exactly runtime/ and science-core-sbom/.
EOF
}

[[ -z "${TAURI_CONFIG:-}" ]] || fail "TAURI_CONFIG is forbidden for release builds"

target=''
offline=0
verify_only=0
app_path=''
science_runtime_bundle=''
while (($#)); do
  case "$1" in
    --target)
      [[ $# -ge 2 ]] || fail "--target requires a value"
      target="$2"
      shift 2
      ;;
    --offline)
      offline=1
      shift
      ;;
    --verify-only)
      verify_only=1
      shift
      ;;
    --app)
      [[ $# -ge 2 ]] || fail "--app requires a value"
      app_path="$2"
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

if [[ -z "$target" ]]; then
  command -v rustc >/dev/null 2>&1 || \
    fail "--target is required when rustc is unavailable"
  target="$(rustc -Vv | sed -n 's/^host: //p')"
  [[ -n "$target" ]] || fail "could not detect the host Rust target"
fi
[[ "$target" == aarch64-apple-darwin || "$target" == x86_64-apple-darwin ]] || \
  fail "unsupported macOS target: $target"
[[ -n "$science_runtime_bundle" ]] || fail "--science-runtime-bundle is required"
validate_science_runtime_bundle "$ROOT" "$science_runtime_bundle" "$target" || \
  fail "independent Science runtime bundle failed strict validation"
science_runtime_bundle="$(cd -P "$science_runtime_bundle" && pwd -P)"

if ((verify_only)); then
  [[ -n "$app_path" ]] || fail "--verify-only requires --app"
  exec bash "$ROOT/scripts/release/verify-macos-bundle.sh" \
    --app "$app_path" \
    --target "$target" \
    --science-runtime-bundle "$science_runtime_bundle"
fi
[[ -z "$app_path" ]] || fail "--app is only valid with --verify-only"

if ((offline)); then
  [[ -x "$DESKTOP_DIR/src-tauri/binaries/opencode-$target" ]] || \
    fail "offline build requires opencode-$target"
  [[ -x "$DESKTOP_DIR/src-tauri/binaries/uv-$target" ]] || \
    fail "offline build requires uv-$target"
  [[ -f "$ROOT/runtime/skills/external/ai4s-skills/.commit" ]] || \
    fail "offline build requires the pinned ai4s-skills pack"
else
  bash "$ROOT/scripts/dev/fetch-opencode.sh" "$target"
  bash "$ROOT/scripts/dev/fetch-uv.sh" "$target"
  bash "$ROOT/scripts/dev/fetch-skills.sh"
fi
SPARK_AGENT_RELEASE_TARGET="$target" \
  bash "$ROOT/scripts/quality/check-release-assets.sh"

release_parent="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
[[ -d "$release_parent" && ! -L "$release_parent" ]] || fail "release temporary parent is unsafe"
release_parent="$(cd -P "$release_parent" && pwd -P)"
cargo_target_dir="$(mktemp -d "$release_parent/spark-macos-${target}.XXXXXX")"
[[ -d "$cargo_target_dir" && ! -L "$cargo_target_dir" ]] || \
  fail "could not create a fresh release target directory"
case "$cargo_target_dir" in "$release_parent"/*) ;; *) fail "unsafe Cargo target directory" ;; esac
artifact_dir=''
artifact_complete=0
mount_point=''
mounted=0
config_dir=''
cleanup_release_build() {
  if ((mounted)) && [[ -n "$mount_point" ]]; then hdiutil detach "$mount_point" >/dev/null 2>&1 || true; fi
  [[ -n "$mount_point" && -d "$mount_point" ]] && rmdir "$mount_point" >/dev/null 2>&1 || true
  if [[ "$artifact_complete" != 1 && -n "$artifact_dir" && -d "$artifact_dir" ]]; then rm -rf "$artifact_dir"; fi
  [[ -n "$config_dir" && -d "$config_dir" ]] && rm -rf "$config_dir"
  rm -rf "$cargo_target_dir"
}
trap cleanup_release_build EXIT
config_dir="$(mktemp -d "$release_parent/spark-macos-config.${target}.XXXXXX")"
chmod 0700 "$config_dir"
[[ -d "$config_dir" && ! -L "$config_dir" ]] || \
  fail "could not create a private Tauri config directory"
config_dir="$(cd -P "$config_dir" && pwd -P)"
case "$config_dir" in "$release_parent"/*) ;; *) fail "unsafe Tauri config directory" ;; esac
tauri_overlay="$config_dir/science-runtime.json"
generate_science_tauri_overlay \
  "$ROOT" \
  "$science_runtime_bundle" \
  "$target" \
  "$tauri_overlay" || fail "could not generate the controlled Tauri resource overlay"

if ((offline)); then
  CARGO_TARGET_DIR="$cargo_target_dir" CARGO_NET_OFFLINE=true \
    pnpm --offline --dir "$ROOT" --filter @ai4s/desktop tauri build \
      --config "$tauri_overlay" --target "$target" --bundles app,dmg
else
  CARGO_TARGET_DIR="$cargo_target_dir" \
    pnpm --dir "$ROOT" --filter @ai4s/desktop tauri build \
      --config "$tauri_overlay" --target "$target" --bundles app,dmg
fi

app_path="$cargo_target_dir/$target/release/bundle/macos/Spark Agent.app"
[[ -d "$app_path" ]] || fail "Tauri did not produce the expected app bundle: $app_path"
bash "$ROOT/scripts/release/verify-macos-bundle.sh" \
  --app "$app_path" \
  --target "$target" \
  --science-runtime-bundle "$science_runtime_bundle"

dmg_path="$(select_single_dmg "$cargo_target_dir/$target/release/bundle")" || \
  fail "Tauri did not produce one trustworthy DMG"
artifact_parent="$ROOT/dist/release/macos"
[[ -d "$artifact_parent" || ! -e "$artifact_parent" ]] || fail "release artifact parent is unsafe"
mkdir -p "$artifact_parent"
[[ -d "$artifact_parent" && ! -L "$artifact_parent" ]] || fail "release artifact parent is unsafe after creation"
artifact_parent="$(cd -P "$artifact_parent" && pwd -P)"
case "$artifact_parent" in "$ROOT"/dist/release/macos) ;; *) fail "release artifact parent escapes dist/release" ;; esac
artifact_dir="$artifact_parent/$target"
[[ ! -e "$artifact_dir" ]] || fail "refusing to overwrite existing release artifacts: $artifact_dir"
mkdir "$artifact_dir"
[[ -d "$artifact_dir" && ! -L "$artifact_dir" ]] || fail "release artifact directory is unsafe"
artifact_dir="$(cd -P "$artifact_dir" && pwd -P)"
case "$artifact_dir" in "$artifact_parent"/*) ;; *) fail "release artifact directory escapes dist/release" ;; esac
ditto "$app_path" "$artifact_dir/Spark Agent.app"
cp "$dmg_path" "$artifact_dir/$(basename "$dmg_path")"
final_app_path="$artifact_dir/Spark Agent.app"
final_dmg_path="$artifact_dir/$(basename "$dmg_path")"
[[ -d "$final_app_path" && -f "$final_dmg_path" ]] || fail "could not copy final release artifacts"
dmg_bytes="$(release_file_size "$final_dmg_path")" || fail "could not measure the final DMG"
((dmg_bytes < 2 * 1024 * 1024 * 1024)) || fail "final DMG must be smaller than 2 GiB"
bash "$ROOT/scripts/release/verify-macos-bundle.sh" \
  --app "$final_app_path" \
  --target "$target" \
  --science-runtime-bundle "$science_runtime_bundle"
mount_point="$(mktemp -d "$release_parent/spark-macos-mount.XXXXXX")"
if ! hdiutil attach -readonly -nobrowse -mountpoint "$mount_point" "$final_dmg_path" >/dev/null; then
  fail "could not mount the final DMG read-only"
fi
mounted=1
if ! bash "$ROOT/scripts/release/verify-macos-bundle.sh" \
  --app "$mount_point/Spark Agent.app" \
  --target "$target" \
  --science-runtime-bundle "$science_runtime_bundle"; then
  fail "DMG contents failed bundle verification"
fi
hdiutil detach "$mount_point" >/dev/null || fail "could not detach verification DMG"
mounted=0
rmdir "$mount_point" || fail "could not remove verification mount point"
runtime_report="$(release_content_tree_v1 "$science_runtime_bundle/runtime")" || \
  fail "could not report Science runtime digest"
sbom_report="$(release_content_tree_v1 "$science_runtime_bundle/science-core-sbom")" || \
  fail "could not report Science SBOM digest"
app_report="$(release_content_tree_v1 "$final_app_path")" || fail "could not report app digest"
dmg_sha256="$(release_file_sha256 "$final_dmg_path")" || fail "could not report DMG digest"
runtime_bytes="${runtime_report%%|*}"
runtime_content_tree_sha256="${runtime_report#*|}"
sbom_bytes="${sbom_report%%|*}"
sbom_content_tree_sha256="${sbom_report#*|}"
app_bytes="${app_report%%|*}"
app_content_tree_sha256="${app_report#*|}"
artifact_complete=1

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'app_path=%s\n' "$final_app_path" >>"$GITHUB_OUTPUT"
  printf 'dmg_path=%s\n' "$final_dmg_path" >>"$GITHUB_OUTPUT"
  printf 'artifact_dir=%s\n' "$artifact_dir" >>"$GITHUB_OUTPUT"
fi
printf 'macOS release build passed: content_tree_algorithm=v1-relative-path-size-file-sha256 runtime_bytes=%s runtime_content_tree_sha256=%s sbom_bytes=%s sbom_content_tree_sha256=%s app_bytes=%s app_content_tree_sha256=%s dmg_bytes=%s dmg_sha256=%s app=%s dmg=%s\n' \
  "$runtime_bytes" \
  "$runtime_content_tree_sha256" \
  "$sbom_bytes" \
  "$sbom_content_tree_sha256" \
  "$app_bytes" \
  "$app_content_tree_sha256" \
  "$dmg_bytes" \
  "$dmg_sha256" \
  "$final_app_path" \
  "$final_dmg_path"
