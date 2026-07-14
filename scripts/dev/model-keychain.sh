#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE="io.github.shawliu998.sparkagent.model-api-key"
ACCOUNT="openai-compatible"

fail() {
  printf 'Spark Agent model credential: %s\n' "$1" >&2
  exit 1
}

command -v security >/dev/null 2>&1 || fail "macOS Keychain access requires the 'security' command."

case "${1:-}" in
  set)
    printf 'Enter the model API key at the secure macOS Keychain prompt.\n'
    exec security add-generic-password \
      -a "${ACCOUNT}" \
      -s "${SERVICE}" \
      -l "Spark Agent model API key" \
      -U \
      -w
    ;;
  status)
    if security find-generic-password -a "${ACCOUNT}" -s "${SERVICE}" >/dev/null 2>&1; then
      printf 'A Spark Agent model credential is stored in macOS Keychain.\n'
    else
      printf 'No Spark Agent model credential is stored in macOS Keychain.\n'
    fi
    ;;
  delete)
    if security find-generic-password -a "${ACCOUNT}" -s "${SERVICE}" >/dev/null 2>&1; then
      security delete-generic-password -a "${ACCOUNT}" -s "${SERVICE}" >/dev/null
      printf 'The Spark Agent model credential was deleted from macOS Keychain.\n'
    else
      printf 'No Spark Agent model credential is stored in macOS Keychain.\n'
    fi
    ;;
  *)
    printf 'Usage: %s {set|status|delete}\n' "${0##*/}" >&2
    exit 2
    ;;
esac
