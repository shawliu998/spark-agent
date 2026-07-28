#!/usr/bin/env bash
# Isolated negative fixtures for the macOS bundle verifier. It never mutates
# tracked inputs; the verifier's test input override is explicitly gated.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../release/macos-release-lib.sh
source "$ROOT/scripts/release/macos-release-lib.sh"
TMP_ROOT="${TEST_ROOT:-$(mktemp -d "${TMPDIR:-/tmp}/spark-release-gate.XXXXXX")}"
[[ -d "$TMP_ROOT" && ! -L "$TMP_ROOT" ]] || exit 1
cleanup() { [[ -n "${TEST_ROOT:-}" ]] || rm -rf "$TMP_ROOT"; }
trap cleanup EXIT
count=0
expect_fail() { "$@" >/dev/null 2>&1 && exit 1; count=$((count + 1)); }
verify() {
  SPARK_AGENT_TEST_ALLOW_INPUT_ROOT=1 \
    SPARK_AGENT_TEST_INPUT_ROOT="$TMP_ROOT/input" \
    bash "$ROOT/scripts/release/verify-macos-bundle.sh" \
      --app "$TMP_ROOT/bundle/Spark Agent.app" \
      --target aarch64-apple-darwin \
      --science-runtime-bundle "$TMP_ROOT/science-runtime-bundle"
}
prepare_science_baseline() {
  local architecture="$1"
  local destination="$2"
  python3 - "$ROOT" "$destination" "$architecture" <<'PY'
from pathlib import Path
import runpy
import sys

root = Path(sys.argv[1]).resolve(strict=True)
destination = Path(sys.argv[2])
architecture = sys.argv[3]
module = runpy.run_path(root / "scripts/release/science-sbom.py")
destination.mkdir(mode=0o700)
runtime = module["make_runtime_fixture"](destination, architecture)
module["generate"](
    root,
    runtime,
    destination / "science-core-sbom",
    architecture,
)
PY
}
prepare_science_baseline arm64 "$TMP_ROOT/science-baseline-arm64"
prepare_science_baseline amd64 "$TMP_ROOT/science-baseline-amd64"
bundle_executable() {
  plutil -extract CFBundleExecutable raw -o - "$TMP_ROOT/bundle/Spark Agent.app/Contents/Info.plist"
}
make_fixture() {
  rm -rf \
    "$TMP_ROOT/input" \
    "$TMP_ROOT/bundle" \
    "$TMP_ROOT/science-runtime-bundle" \
    "$TMP_ROOT/science-runtime-real" \
    "$TMP_ROOT/science-resource-real" \
    "$TMP_ROOT/science-hardlink-original"
  mkdir -p "$TMP_ROOT/input/apps/desktop/src-tauri" "$TMP_ROOT/input/runtime/skills/external" "$TMP_ROOT/input/runtime/skills"
  # APFS clone copies keep this 46-assertion suite fast while preserving
  # independent mutation fixtures; fall back to ordinary copies elsewhere.
  cp -cR "$ROOT/apps/desktop/src-tauri/binaries" "$TMP_ROOT/input/apps/desktop/src-tauri/" 2>/dev/null || cp -R "$ROOT/apps/desktop/src-tauri/binaries" "$TMP_ROOT/input/apps/desktop/src-tauri/"
  cp -cR "$ROOT/runtime/skills/external/ai4s-skills" "$TMP_ROOT/input/runtime/skills/external/" 2>/dev/null || cp -R "$ROOT/runtime/skills/external/ai4s-skills" "$TMP_ROOT/input/runtime/skills/external/"
  cp -cR "$ROOT/runtime/skills/core" "$TMP_ROOT/input/runtime/skills/" 2>/dev/null || cp -R "$ROOT/runtime/skills/core" "$TMP_ROOT/input/runtime/skills/"
  mkdir -p "$TMP_ROOT/bundle/Spark Agent.app/Contents/MacOS" "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources"
  local info="$TMP_ROOT/bundle/Spark Agent.app/Contents/Info.plist"
  local identifier name version minimum executable
  local -a metadata=()
  while IFS= read -r metadata_line; do metadata+=("$metadata_line"); done < <(node -e 'const c=require(process.argv[1]); console.log(c.identifier); console.log(c.productName); console.log(c.version); console.log(c.bundle.macOS.minimumSystemVersion)' "$ROOT/apps/desktop/src-tauri/tauri.conf.json")
  identifier="${metadata[0]:-}"; name="${metadata[1]:-}"; version="${metadata[2]:-}"; minimum="${metadata[3]:-}"
  executable="$(sed -n '/^\[package\]$/,/^\[/{s/^name[[:space:]]*=[[:space:]]*"\([^"]*\)"$/\1/p;}' "$ROOT/apps/desktop/src-tauri/Cargo.toml" | head -n 1)"
  plutil -create xml1 "$info"
  plutil -insert CFBundleIdentifier -string "$identifier" "$info"
  plutil -insert CFBundleName -string "$name" "$info"
  plutil -insert CFBundleShortVersionString -string "$version" "$info"
  plutil -insert CFBundleVersion -string "$version" "$info"
  plutil -insert LSMinimumSystemVersion -string "$minimum" "$info"
  plutil -insert CFBundleExecutable -string "$executable" "$info"
  cp -c "$TMP_ROOT/input/apps/desktop/src-tauri/binaries/opencode-aarch64-apple-darwin" "$TMP_ROOT/bundle/Spark Agent.app/Contents/MacOS/$executable" 2>/dev/null || cp "$TMP_ROOT/input/apps/desktop/src-tauri/binaries/opencode-aarch64-apple-darwin" "$TMP_ROOT/bundle/Spark Agent.app/Contents/MacOS/$executable"
  cp -c "$TMP_ROOT/input/apps/desktop/src-tauri/binaries/opencode-aarch64-apple-darwin" "$TMP_ROOT/bundle/Spark Agent.app/Contents/MacOS/opencode" 2>/dev/null || cp "$TMP_ROOT/input/apps/desktop/src-tauri/binaries/opencode-aarch64-apple-darwin" "$TMP_ROOT/bundle/Spark Agent.app/Contents/MacOS/opencode"
  cp -c "$TMP_ROOT/input/apps/desktop/src-tauri/binaries/uv-aarch64-apple-darwin" "$TMP_ROOT/bundle/Spark Agent.app/Contents/MacOS/uv" 2>/dev/null || cp "$TMP_ROOT/input/apps/desktop/src-tauri/binaries/uv-aarch64-apple-darwin" "$TMP_ROOT/bundle/Spark Agent.app/Contents/MacOS/uv"
  cp -cR "$TMP_ROOT/input/runtime/skills/external/ai4s-skills" "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/skills" 2>/dev/null || cp -R "$TMP_ROOT/input/runtime/skills/external/ai4s-skills" "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/skills"
  cp -cR "$TMP_ROOT/input/runtime/skills/core" "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/skills-core" 2>/dev/null || cp -R "$TMP_ROOT/input/runtime/skills/core" "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/skills-core"
  cp -cR "$TMP_ROOT/science-baseline-arm64" "$TMP_ROOT/science-runtime-bundle" 2>/dev/null || cp -R "$TMP_ROOT/science-baseline-arm64" "$TMP_ROOT/science-runtime-bundle"
  cp -cR "$TMP_ROOT/science-runtime-bundle/runtime" "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core" 2>/dev/null || cp -R "$TMP_ROOT/science-runtime-bundle/runtime" "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core"
  cp -cR "$TMP_ROOT/science-runtime-bundle/science-core-sbom" "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core-sbom" 2>/dev/null || cp -R "$TMP_ROOT/science-runtime-bundle/science-core-sbom" "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core-sbom"
}
make_fixture; verify; count=$((count + 1))
make_fixture; mv "$TMP_ROOT/bundle/Spark Agent.app" "$TMP_ROOT/bundle/real.app"; ln -s "$TMP_ROOT/bundle/real.app" "$TMP_ROOT/bundle/Spark Agent.app"; expect_fail verify
make_fixture; main_executable="$(bundle_executable)"; mv "$TMP_ROOT/bundle/Spark Agent.app/Contents/MacOS/$main_executable" "$TMP_ROOT/bundle/main"; ln -s "$TMP_ROOT/bundle/main" "$TMP_ROOT/bundle/Spark Agent.app/Contents/MacOS/$main_executable"; expect_fail verify
make_fixture; mv "$TMP_ROOT/bundle/Spark Agent.app/Contents/MacOS/opencode" "$TMP_ROOT/bundle/opencode"; ln -s "$TMP_ROOT/bundle/opencode" "$TMP_ROOT/bundle/Spark Agent.app/Contents/MacOS/opencode"; expect_fail verify
make_fixture; mv "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/skills" "$TMP_ROOT/bundle/skills"; ln -s "$TMP_ROOT/bundle/skills" "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/skills"; expect_fail verify
make_fixture; printf x >> "$TMP_ROOT/input/apps/desktop/src-tauri/binaries/opencode-aarch64-apple-darwin"; expect_fail verify
make_fixture; mv "$TMP_ROOT/input/apps/desktop/src-tauri/binaries/opencode-aarch64-apple-darwin" "$TMP_ROOT/input/opencode"; ln -s "$TMP_ROOT/input/opencode" "$TMP_ROOT/input/apps/desktop/src-tauri/binaries/opencode-aarch64-apple-darwin"; expect_fail verify
make_fixture; printf x >> "$TMP_ROOT/bundle/Spark Agent.app/Contents/MacOS/opencode"; expect_fail verify
for tree in skills skills-core; do
  make_fixture; printf x > "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/$tree/extra"; expect_fail verify
  make_fixture; first="$(find "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/$tree" -type f -print -quit)"; mv "$first" "$first.missing"; expect_fail verify
  make_fixture; first="$(find "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/$tree" -type f -print -quit)"; printf x >> "$first"; expect_fail verify
  make_fixture; first="$(find "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/$tree" -type f -print -quit)"; mv "$first" "$TMP_ROOT/bundle/file"; ln -s "$TMP_ROOT/bundle/file" "$first"; expect_fail verify
done
for tree in external/ai4s-skills core; do
  make_fixture; printf x > "$TMP_ROOT/input/runtime/skills/$tree/extra"; expect_fail verify
  make_fixture; first="$(find "$TMP_ROOT/input/runtime/skills/$tree" -type f -print -quit)"; mv "$first" "$first.missing"; expect_fail verify
  make_fixture; first="$(find "$TMP_ROOT/input/runtime/skills/$tree" -type f -print -quit)"; printf x >> "$first"; expect_fail verify
  make_fixture; first="$(find "$TMP_ROOT/input/runtime/skills/$tree" -type f -print -quit)"; mv "$first" "$TMP_ROOT/bundle/input-file"; ln -s "$TMP_ROOT/bundle/input-file" "$first"; expect_fail verify
done
for key in CFBundleIdentifier CFBundleName CFBundleShortVersionString CFBundleVersion LSMinimumSystemVersion; do
  make_fixture; plutil -replace "$key" -string invalid "$TMP_ROOT/bundle/Spark Agent.app/Contents/Info.plist"; expect_fail verify
done
make_fixture; verify; rm "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core/compose.yaml"; expect_fail verify
make_fixture; verify; printf x > "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core/extra"; expect_fail verify
make_fixture; verify; printf x >> "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core/science-core.oci.tar"; expect_fail verify
make_fixture; verify; python3 - "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core/manifest.json" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
value = json.loads(path.read_text())
value["images"][0]["sha256"] = "0" * 64
path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
PY
expect_fail verify
make_fixture; verify; printf x >> "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core/compose.yaml"; expect_fail verify
make_fixture; verify; python3 - "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core/manifest.json" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
value = json.loads(path.read_text())
value["images"][0]["image"] = "io.github.shawliu998.sparkagent/science-core:0.1.8"
path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
PY
expect_fail verify
make_fixture; verify; python3 - "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core/manifest.json" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
value = json.loads(path.read_text())
value["images"][0]["imageId"] = "sha256:" + "f" * 64
path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
PY
expect_fail verify
make_fixture; verify; python3 - "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core-sbom/manifest.json" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
value = json.loads(path.read_text())
value["subjects"][0]["imageId"] = "sha256:" + "e" * 64
path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
PY
expect_fail verify
make_fixture; verify; mv "$TMP_ROOT/science-runtime-bundle" "$TMP_ROOT/science-runtime-real"; ln -s "$TMP_ROOT/science-runtime-real" "$TMP_ROOT/science-runtime-bundle"; expect_fail verify
make_fixture; verify; mv "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core/compose.yaml" "$TMP_ROOT/science-resource-real"; ln -s "$TMP_ROOT/science-resource-real" "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core/compose.yaml"; expect_fail verify
make_fixture; verify; mv "$TMP_ROOT/science-runtime-bundle/runtime/manifest.json" "$TMP_ROOT/science-hardlink-original"; ln "$TMP_ROOT/science-runtime-bundle/runtime/compose.yaml" "$TMP_ROOT/science-runtime-bundle/runtime/manifest.json"; expect_fail verify
make_fixture; verify; rm -rf "$TMP_ROOT/science-runtime-bundle"; cp -cR "$TMP_ROOT/science-baseline-amd64" "$TMP_ROOT/science-runtime-bundle" 2>/dev/null || cp -R "$TMP_ROOT/science-baseline-amd64" "$TMP_ROOT/science-runtime-bundle"; expect_fail verify
make_fixture; verify; rm -rf "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core" "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core-sbom"; cp -cR "$TMP_ROOT/science-baseline-amd64/runtime" "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core" 2>/dev/null || cp -R "$TMP_ROOT/science-baseline-amd64/runtime" "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core"; cp -cR "$TMP_ROOT/science-baseline-amd64/science-core-sbom" "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core-sbom" 2>/dev/null || cp -R "$TMP_ROOT/science-baseline-amd64/science-core-sbom" "$TMP_ROOT/bundle/Spark Agent.app/Contents/Resources/science-core-sbom"; expect_fail verify
make_fixture; expect_fail env TAURI_CONFIG='{"bundle":{"externalBin":[]}}' bash "$ROOT/scripts/release/build-macos.sh" --verify-only --app "$TMP_ROOT/bundle/Spark Agent.app" --target aarch64-apple-darwin --science-runtime-bundle "$TMP_ROOT/science-runtime-bundle"
mkdir -p "$TMP_ROOT/dmg-empty"
expect_fail select_single_dmg "$TMP_ROOT/dmg-empty"
mkdir -p "$TMP_ROOT/dmg-one"
touch "$TMP_ROOT/dmg-one/Spark Agent.dmg"
[[ "$(select_single_dmg "$TMP_ROOT/dmg-one")" == "$TMP_ROOT/dmg-one/Spark Agent.dmg" ]] || exit 1
count=$((count + 1))
touch "$TMP_ROOT/dmg-one/duplicate.dmg"
expect_fail select_single_dmg "$TMP_ROOT/dmg-one"
printf 'macOS release gate fixtures passed: %d assertions\n' "$count"
