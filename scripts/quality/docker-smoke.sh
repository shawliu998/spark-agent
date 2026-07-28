#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DRIVER="${ROOT_DIR}/tests/integration/docker_smoke.py"
MODE="development"

usage() {
  cat >&2 <<'EOF'
Usage: scripts/quality/docker-smoke.sh [--production]

Without arguments, runs the development Compose smoke. --production builds
isolated, uniquely tagged local images and exercises the release Compose file.
EOF
}

if [[ $# -eq 1 && "$1" == "--production" ]]; then
  MODE="production"
elif [[ $# -ne 0 ]]; then
  usage
  exit 64
fi

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
SECRET_FILE="${TEMP_ROOT}/model-secret"
CORE_IMAGE_TAG=""
RUNTIME_IMAGE_TAG=""
CORE_IMAGE_ID=""
RUNTIME_IMAGE_ID=""
CORE_URL=""
COMPOSE_ATTEMPTED=0

fail() {
  printf 'Docker integration smoke failed: %s\n' "$1" >&2
  exit 1
}

compose() {
  if [[ "${MODE}" == "production" ]]; then
    env -u OPENAI_API_KEY -u COMPOSE_FILE -u COMPOSE_PROJECT_NAME \
      SPARK_AGENT_CORE_TOKEN="${CORE_TOKEN}" \
      SPARK_AGENT_CORE_HOST_DATA_DIR="${CORE_DATA_DIR}" \
      SPARK_AGENT_OPENAI_API_KEY_FILE="${SECRET_FILE}" \
      SPARK_AGENT_CORE_IMAGE_ID="${CORE_IMAGE_ID}" \
      SPARK_AGENT_RUNTIME_IMAGE_ID="${RUNTIME_IMAGE_ID}" \
      OPENAI_API_BASE="" \
      SPARK_AGENT_LLM_MODEL="" \
      SPARK_AGENT_EMBEDDING_MODEL="" \
      docker compose \
        --file "${ROOT_DIR}/services/compose.production.yaml" \
        --project-name "${COMPOSE_PROJECT}" \
        --project-directory "${ROOT_DIR}" \
        "$@"
  else
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
  fi
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

  if [[ "${COMPOSE_ATTEMPTED}" == 1 ]]; then
    compose stop --timeout 5 science-core science-runtime >/dev/null 2>&1
    cleanup_code='from pathlib import Path; import shutil; root = Path("/data"); '
    cleanup_code+='[(shutil.rmtree(item) if item.is_dir() and not item.is_symlink() '
    cleanup_code+='else item.unlink(missing_ok=True)) for item in root.iterdir()]'
    compose run --rm --no-deps --entrypoint python science-core -c \
      "${cleanup_code}" >/dev/null 2>&1 || cleanup_failed=1
    compose down --rmi local --volumes --remove-orphans --timeout 5 \
      >/dev/null 2>&1 || cleanup_failed=1
  fi
  if [[ -n "${CORE_IMAGE_TAG}" ]]; then
    if docker image inspect "${CORE_IMAGE_TAG}" >/dev/null 2>&1; then
      docker image rm "${CORE_IMAGE_TAG}" >/dev/null 2>&1 || cleanup_failed=1
    fi
  fi
  if [[ -n "${RUNTIME_IMAGE_TAG}" ]]; then
    if docker image inspect "${RUNTIME_IMAGE_TAG}" >/dev/null 2>&1; then
      docker image rm "${RUNTIME_IMAGE_TAG}" >/dev/null 2>&1 || cleanup_failed=1
    fi
  fi
  python3 - "${TEMP_ROOT}" <<'PY' || cleanup_failed=1
import os
import shutil
import sys
import tempfile

target = os.path.realpath(sys.argv[1])
parent = os.path.realpath(tempfile.gettempdir())
if (
    os.path.dirname(target) != parent
    or not os.path.basename(target).startswith("spark-agent-docker-smoke.")
    or not os.path.isdir(target)
    or os.path.islink(sys.argv[1])
):
    raise SystemExit("refusing unsafe smoke temporary-directory cleanup")
shutil.rmtree(target)
PY

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
if [[ "${MODE}" == "production" ]]; then
  : >"${SECRET_FILE}"
  chmod 600 "${SECRET_FILE}"
fi

command -v docker >/dev/null 2>&1 || fail "Docker CLI was not found."
docker compose version >/dev/null 2>&1 || fail "Docker Compose plugin was not found."
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable."
[[ -f "${DRIVER}" ]] || fail "Integration driver is missing."

cd "${ROOT_DIR}"

if [[ "${MODE}" == "production" ]]; then
  CORE_IMAGE_TAG="spark-agent-docker-smoke-core-${RUN_SUFFIX}:local"
  RUNTIME_IMAGE_TAG="spark-agent-docker-smoke-runtime-${RUN_SUFFIX}:local"
  printf 'Building uniquely tagged production science images…\n'
  docker build --tag "${CORE_IMAGE_TAG}" "${ROOT_DIR}/services/science-core"
  docker build --tag "${RUNTIME_IMAGE_TAG}" "${ROOT_DIR}/services/science-runtime"
  CORE_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "${CORE_IMAGE_TAG}")"
  RUNTIME_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "${RUNTIME_IMAGE_TAG}")"
  [[ "${CORE_IMAGE_ID}" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "science-core image ID is invalid."
  [[ "${RUNTIME_IMAGE_ID}" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "science-runtime image ID is invalid."
  [[ "${CORE_IMAGE_ID}" != "${RUNTIME_IMAGE_ID}" ]] || fail "science services resolved to one image ID."
  compose config --quiet >/dev/null || fail "Compose configuration is invalid for the smoke environment."
else
  compose config --quiet >/dev/null || fail "Compose configuration is invalid for the smoke environment."
  printf 'Building isolated science-core and science-runtime images…\n'
  compose build science-runtime science-core
fi

printf 'Starting isolated science services…\n'
COMPOSE_ATTEMPTED=1
if ! compose up --detach --no-build science-runtime science-core; then
  compose logs --tail=160 science-core science-runtime >&2 || true
  fail "Science services did not start."
fi

if ! CORE_URL="$(discover_core_url)"; then
  compose logs --tail=160 science-core science-runtime >&2 || true
  fail "Docker did not publish a dynamic loopback port."
fi

if ! python3 "${DRIVER}" wait-ready --base-url "${CORE_URL}" --timeout 150; then
  compose logs --tail=160 science-core science-runtime >&2 || true
  fail "Science services did not become ready."
fi

if ! SPARK_AGENT_SMOKE_TOKEN="${CORE_TOKEN}" \
  python3 "${DRIVER}" exercise --base-url "${CORE_URL}" --state-file "${STATE_FILE}"; then
  compose logs --tail=160 science-core science-runtime >&2 || true
  fail "Science service exercise failed."
fi

printf 'Restarting science-core to verify durable state…\n'
if ! compose restart --timeout 10 science-core; then
  compose logs --tail=160 science-core science-runtime >&2 || true
  fail "science-core did not restart."
fi
if ! CORE_URL="$(discover_core_url)"; then
  compose logs --tail=160 science-core science-runtime >&2 || true
  fail "Docker did not republish science-core on loopback."
fi
if ! python3 "${DRIVER}" wait-ready --base-url "${CORE_URL}" --timeout 90; then
  compose logs --tail=160 science-core science-runtime >&2 || true
  fail "science-core did not become ready after restart."
fi

if ! SPARK_AGENT_SMOKE_TOKEN="${CORE_TOKEN}" \
  python3 "${DRIVER}" verify-restart \
    --base-url "${CORE_URL}" \
    --state-file "${STATE_FILE}"; then
  compose logs --tail=160 science-core science-runtime >&2 || true
  fail "Restart persistence verification failed."
fi

printf 'Docker integration smoke passed (%s).\n' "${MODE}"
