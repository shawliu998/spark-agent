#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TAURI_MANIFEST="$ROOT/apps/desktop/src-tauri/Cargo.toml"
PYTHON_BIN="${PYTHON:-python3}"
PYTHON_PATH=""

usage() {
  cat >&2 <<'EOF'
Usage: scripts/quality/run-check.sh <module> <check>

Modules and checks:
  desktop  lint | typecheck | test
  rust     fmt | lint | test
  core     lint | typecheck | test
  runtime  lint | typecheck | test
EOF
}

run_step() {
  local label="$1"
  shift

  printf '\n==> %s\n' "$label"
  if "$@"; then
    printf '<== PASS: %s\n' "$label"
  else
    local status=$?
    printf '<== FAIL: %s (exit %d)\n' "$label" "$status" >&2
    return "$status"
  fi
}

run_in_directory() {
  local label="$1"
  local directory="$2"
  shift 2

  printf '\n==> %s\n' "$label"
  if (cd "$directory" && "$@"); then
    printf '<== PASS: %s\n' "$label"
  else
    local status=$?
    printf '<== FAIL: %s (exit %d)\n' "$label" "$status" >&2
    return "$status"
  fi
}

prepare_rust_check() {
  # Tauri validates bundle inputs while compiling. Quality checks do not package
  # the app, so explicitly remove external binaries from the test-only merged
  # config and provide the git-ignored skill resource directory it still reads.
  mkdir -p "$ROOT/runtime/skills/external/ai4s-skills"
  export TAURI_CONFIG='{"bundle":{"externalBin":[]}}'
}

prepare_python_check() {
  if [[ -n "${PYTHON_PATH}" ]]; then
    return 0
  fi
  PYTHON_PATH="$("${PYTHON_BIN}" -c 'import sys; print(sys.executable)')"
  if [[ ! -x "${PYTHON_PATH}" ]]; then
    printf 'Python interpreter is not executable: %s\n' "${PYTHON_PATH}" >&2
    return 1
  fi
}

if [[ $# -ne 2 ]]; then
  usage
  exit 64
fi

module="$1"
check="$2"

case "$module:$check" in
  desktop:lint)
    run_step "desktop / ESLint" pnpm --dir "$ROOT" --filter @ai4s/desktop lint
    ;;
  desktop:typecheck)
    run_step "desktop / TypeScript" pnpm --dir "$ROOT" --filter @ai4s/desktop typecheck
    ;;
  desktop:test)
    run_step "desktop / Vitest" pnpm --dir "$ROOT" --filter @ai4s/desktop test
    ;;
  rust:fmt)
    run_step "Rust desktop / rustfmt" \
      cargo fmt --manifest-path "$TAURI_MANIFEST" --all -- --check
    ;;
  rust:lint)
    run_step "Rust desktop / compile-only Tauri inputs" prepare_rust_check
    run_step "Rust desktop / Clippy" \
      cargo clippy --manifest-path "$TAURI_MANIFEST" \
      --all-targets --all-features -- -D warnings
    ;;
  rust:test)
    run_step "Rust desktop / compile-only Tauri inputs" prepare_rust_check
    run_step "Rust desktop / cargo test" \
      cargo test --manifest-path "$TAURI_MANIFEST" --all-targets
    ;;
  core:lint)
    prepare_python_check
    run_in_directory "science-core / Ruff" "$ROOT/services/science-core" \
      "$PYTHON_PATH" -m ruff check .
    ;;
  core:typecheck)
    prepare_python_check
    run_in_directory "science-core / Pyright" "$ROOT/services/science-core" \
      "$PYTHON_PATH" -m pyright --pythonpath "$PYTHON_PATH"
    ;;
  core:test)
    prepare_python_check
    run_in_directory "science-core / pytest" "$ROOT/services/science-core" \
      "$PYTHON_PATH" -m pytest
    ;;
  runtime:lint)
    prepare_python_check
    run_in_directory "science-runtime / Ruff" "$ROOT/services/science-runtime" \
      "$PYTHON_PATH" -m ruff check .
    ;;
  runtime:typecheck)
    prepare_python_check
    run_in_directory "science-runtime / Pyright" "$ROOT/services/science-runtime" \
      "$PYTHON_PATH" -m pyright --pythonpath "$PYTHON_PATH"
    ;;
  runtime:test)
    prepare_python_check
    run_in_directory "science-runtime / pytest" "$ROOT/services/science-runtime" \
      "$PYTHON_PATH" -m pytest
    ;;
  *)
    printf 'Unknown quality check: %s / %s\n' "$module" "$check" >&2
    usage
    exit 64
    ;;
esac
