#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DRIVER="${ROOT_DIR}/tests/integration/docker_smoke.py"
if ! command -v python3 >/dev/null 2>&1; then
  printf 'Docker integration smoke failed: python3 is required.\n' >&2
  exit 1
fi
RUN_SUFFIX="$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
COMPOSE_PROJECT="spark-agent-smoke-${RUN_SUFFIX}"
CORE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/spark-agent-docker-smoke.XXXXXX")"
CORE_DATA_DIR="${TEMP_ROOT}/core-data"
STATE_FILE="${TEMP_ROOT}/smoke-state.json"
CORE_URL=""

fail() {
  printf 'Docker integration smoke failed: %s\n' "$1" >&2
  exit 1
}

compose() {
  env -u OPENAI_API_KEY -u COMPOSE_FILE -u COMPOSE_PROJECT_NAME \
    SPARK_AGENT_CORE_TOKEN="${CORE_TOKEN}" \
    SPARK_AGENT_CORE_HOST_DATA_DIR="${CORE_DATA_DIR}" \
    SPARK_AGENT_CORE_PUBLISH="127.0.0.1::8765" \
    SPARK_AGENT_OPENAI_API_KEY_SECRET="" \
    OPENAI_API_BASE="" \
    SPARK_AGENT_LLM_MODEL="" \
    SPARK_AGENT_EMBEDDING_MODEL="" \
    docker compose \
      --file "${ROOT_DIR}/compose.yaml" \
      --project-name "${COMPOSE_PROJECT}" \
      --project-directory "${ROOT_DIR}" \
      "$@"
}

discover_core_url() {
  local attempt=0
  local published=""
  while ((attempt < 80)); do
    published="$(compose port science-core 8765 2>/dev/null | tail -n 1 || true)"
    if [[ "${published}" =~ ^127\.0\.0\.1:[0-9]+$ ]]; then
      printf 'http://%s\n' "${published}"
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 0.25
  done
  return 1
}

cleanup() {
  local exit_code=$?
  local cleanup_failed=0
  local cleanup_code
  trap - EXIT INT TERM HUP
  set +e

  cleanup_code='from pathlib import Path; import shutil; root = Path("/data"); '
  cleanup_code+='[(shutil.rmtree(item) if item.is_dir() and not item.is_symlink() '
  cleanup_code+='else item.unlink(missing_ok=True)) for item in root.iterdir()]'
  compose stop --timeout 5 science-core science-runtime >/dev/null 2>&1
  compose run --rm --no-deps --entrypoint python science-core -c \
    "${cleanup_code}" >/dev/null 2>&1
  compose down --rmi local --volumes --remove-orphans --timeout 5 \
    >/dev/null 2>&1 || cleanup_failed=1
  rm -rf "${TEMP_ROOT}" || cleanup_failed=1

  if [[ ${exit_code} -eq 0 && ${cleanup_failed} -ne 0 ]]; then
    printf 'Docker integration smoke cleanup did not complete. Compose project: %s\n' \
      "${COMPOSE_PROJECT}" >&2
    exit_code=1
  fi
  exit "${exit_code}"
}

trap cleanup EXIT INT TERM HUP

mkdir -p "${CORE_DATA_DIR}"
chmod 700 "${TEMP_ROOT}" "${CORE_DATA_DIR}"

command -v docker >/dev/null 2>&1 || fail "Docker CLI was not found."
docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin was not found."
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable."
[[ -f "${DRIVER}" ]] || fail "Integration driver is missing."

cd "${ROOT_DIR}"
compose config --quiet >/dev/null || fail "compose.yaml is invalid for the smoke environment."

printf 'Building isolated science-core and science-runtime images…\n'
compose build science-runtime science-core

printf 'Starting isolated science services…\n'
compose up --detach --no-build science-runtime science-core

CORE_URL="$(discover_core_url)" || fail "Docker did not publish a dynamic loopback port."

if ! python3 "${DRIVER}" wait-ready --base-url "${CORE_URL}" --timeout 150; then
  compose logs --tail=160 science-core science-runtime >&2 || true
  fail "Science services did not become ready."
fi

SPARK_AGENT_SMOKE_TOKEN="${CORE_TOKEN}" \
  python3 "${DRIVER}" exercise --base-url "${CORE_URL}" --state-file "${STATE_FILE}"

printf 'Restarting science-core to verify durable state…\n'
compose restart --timeout 10 science-core
CORE_URL="$(discover_core_url)" || fail "Docker did not republish science-core on loopback."
if ! python3 "${DRIVER}" wait-ready --base-url "${CORE_URL}" --timeout 90; then
  compose logs --tail=160 science-core science-runtime >&2 || true
  fail "science-core did not become ready after restart."
fi

SPARK_AGENT_SMOKE_TOKEN="${CORE_TOKEN}" \
  python3 "${DRIVER}" verify-restart \
    --base-url "${CORE_URL}" \
    --state-file "${STATE_FILE}"

printf 'Docker integration smoke passed.\n'
