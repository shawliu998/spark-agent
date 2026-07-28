#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_PROJECT="${SPARK_AGENT_INTERNAL_COMPOSE_PROJECT:-spark-agent-internal-dev}"
CORE_DATA_DIR="${SPARK_AGENT_CORE_HOST_DATA_DIR:-${ROOT_DIR}/.local/science-core}"
DESKTOP_PORT="${SPARK_AGENT_DESKTOP_PORT:-5173}"
MODEL_KEYCHAIN_SERVICE="io.github.shawliu998.sparkagent.model-api-key"
MODEL_KEYCHAIN_ACCOUNT="openai-compatible"
MODEL_API_KEY=""
DEV_PID=""

fail() {
  printf 'Spark Agent internal startup failed: %s\n' "$1" >&2
  exit 1
}

compose() {
  SPARK_AGENT_OPENAI_API_KEY_SECRET="" \
    docker compose --project-name "${COMPOSE_PROJECT}" --project-directory "${ROOT_DIR}" "$@"
}

compose_with_model_secret() {
  SPARK_AGENT_OPENAI_API_KEY_SECRET="${MODEL_API_KEY}" \
    docker compose --project-name "${COMPOSE_PROJECT}" --project-directory "${ROOT_DIR}" "$@"
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM HUP
  if [[ -n "${DEV_PID}" ]] && kill -0 "${DEV_PID}" 2>/dev/null; then
    kill "${DEV_PID}" 2>/dev/null || true
    wait "${DEV_PID}" 2>/dev/null || true
  fi
  compose down --volumes --remove-orphans --timeout 5 >/dev/null 2>&1 || true
  exit "${exit_code}"
}

if [[ -n "${OPENAI_API_KEY+x}" ]]; then
  fail "OPENAI_API_KEY is set in the host environment. Run 'unset OPENAI_API_KEY', store the credential with 'pnpm model-key:set', then retry."
fi
if [[ -n "${SPARK_AGENT_OPENAI_API_KEY_SECRET+x}" ]]; then
  fail "SPARK_AGENT_OPENAI_API_KEY_SECRET is set in the host environment. Unset it and use 'pnpm model-key:set' so only macOS Keychain persists the credential."
fi
[[ "${DESKTOP_PORT}" =~ ^[0-9]+$ ]] || fail "SPARK_AGENT_DESKTOP_PORT must be a numeric TCP port."
(( DESKTOP_PORT >= 1024 && DESKTOP_PORT <= 65535 )) || fail "SPARK_AGENT_DESKTOP_PORT must be between 1024 and 65535."
[[ "${DESKTOP_PORT}" == "1420" || "${DESKTOP_PORT}" == "5173" ]] || fail "SPARK_AGENT_DESKTOP_PORT must be 1420 or 5173 so it matches the science-core loopback CORS allowlist."

command -v docker >/dev/null 2>&1 || fail "Docker CLI was not found. Install Docker Desktop or OrbStack."
docker compose version >/dev/null 2>&1 || fail "Docker Compose was not found. Install the Docker Compose plugin."
docker info >/dev/null 2>&1 || fail "Docker is installed but its daemon is unavailable. Start Docker Desktop or OrbStack, then retry."
command -v python3 >/dev/null 2>&1 || fail "python3 is required to create the ephemeral service credential."
command -v pnpm >/dev/null 2>&1 || fail "pnpm was not found. Install the repository's declared pnpm version."
command -v security >/dev/null 2>&1 || fail "macOS Keychain access is required. The 'security' command was not found."

mkdir -p "${CORE_DATA_DIR}"
CORE_DATA_DIR="$(cd "${CORE_DATA_DIR}" && pwd -P)"
export SPARK_AGENT_CORE_HOST_DATA_DIR="${CORE_DATA_DIR}"
export SPARK_AGENT_CORE_TOKEN
SPARK_AGENT_CORE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
[[ ${#SPARK_AGENT_CORE_TOKEN} -eq 64 ]] || fail "Could not create the ephemeral service credential."

cd "${ROOT_DIR}"
compose config --quiet >/dev/null 2>&1 || fail "compose.yaml is invalid for the internal runtime configuration."

trap cleanup EXIT INT TERM HUP

# Remove only this script's stable project. This recovers containers left by a
# previous forced exit without touching a user-managed Compose project.
compose down --volumes --remove-orphans --timeout 5 >/dev/null 2>&1 || true

conflicting_containers="$(
  docker ps --quiet |
    while IFS= read -r container_id; do
      # Containers may exit between listing and inspection. A vanished container
      # cannot still own the bind mount, so skip it without weakening the check.
      docker inspect --format "{{json .}}" "${container_id}" 2>/dev/null || true
    done |
    CORE_DATA_DIR="${CORE_DATA_DIR}" python3 -c '
import json
import os
import sys

def host_path(value: str) -> str:
    # Docker Desktop reports macOS bind sources through its Linux VM prefix.
    # Normalize only known host-mount prefixes before comparing real paths.
    for prefix in ("/host_mnt", "/run/desktop/mnt/host"):
        if value.startswith(f"{prefix}/"):
            value = value[len(prefix):]
            break
    return os.path.realpath(value)


target = host_path(os.environ["CORE_DATA_DIR"])
names = []
for line in sys.stdin:
    if not line.strip():
        continue
    container = json.loads(line)
    for mount in container.get("Mounts", []):
        if mount.get("Type") != "bind":
            continue
        source = host_path(str(mount.get("Source", "")))
        try:
            overlaps = os.path.commonpath([target, source]) in {target, source}
        except ValueError:
            overlaps = False
        if overlaps:
            names.append(str(container.get("Name", "unknown")).lstrip("/"))
            break
print(", ".join(sorted(names)))
'
)"
[[ -z "${conflicting_containers}" ]] || fail "The science-core data directory is already mounted by: ${conflicting_containers}. Stop that stack or choose another SPARK_AGENT_CORE_HOST_DATA_DIR."

printf 'Building the isolated science services…\n'
compose build

# Keep the credential out of the potentially long image build and read it only
# for the single Compose command that materializes the runtime secret.
if security find-generic-password \
  -a "${MODEL_KEYCHAIN_ACCOUNT}" \
  -s "${MODEL_KEYCHAIN_SERVICE}" >/dev/null 2>&1; then
  MODEL_API_KEY="$(
    security find-generic-password \
      -a "${MODEL_KEYCHAIN_ACCOUNT}" \
      -s "${MODEL_KEYCHAIN_SERVICE}" \
      -w
  )" || fail "The model credential exists in macOS Keychain but could not be read."
fi
[[ ${#MODEL_API_KEY} -le 4096 ]] || fail "The model credential exceeds the safe size limit."

printf 'Starting the isolated science services…\n'
if ! compose_with_model_secret up -d; then
  MODEL_API_KEY=""
  fail "Docker Compose could not start the isolated science services."
fi
MODEL_API_KEY=""

published=""
for _ in $(seq 1 60); do
  published="$(compose port science-core 8765 2>/dev/null | tail -n 1 || true)"
  [[ "${published}" =~ ^127\.0\.0\.1:[0-9]+$ ]] && break
  published=""
  sleep 0.25
done
[[ -n "${published}" ]] || fail "Docker did not publish the science-core loopback port."

CORE_URL="http://${published}"
health_ready() {
  python3 - "${CORE_URL}" <<'PY'
import json
import sys
import urllib.request

try:
    with urllib.request.urlopen(f"{sys.argv[1]}/health", timeout=2) as response:
        health = json.load(response)
except Exception:
    raise SystemExit(1)
if not (
    health.get("status") == "ok"
    and health.get("database") == "ok"
    and health.get("runtime") == "ready"
):
    raise SystemExit(1)
PY
}

for _ in $(seq 1 120); do
  if health_ready; then
    break
  fi
  sleep 1
done
if ! health_ready; then
  compose logs --tail=120 >&2 || true
  fail "science-core did not become healthy within 120 seconds; recent service logs are shown above."
fi

printf 'Science services ready at %s. Starting the desktop web client on port %s…\n' "${CORE_URL}" "${DESKTOP_PORT}"
VITE_SCIENCE_CORE_URL="${CORE_URL}" \
VITE_SCIENCE_CORE_TOKEN="${SPARK_AGENT_CORE_TOKEN}" \
  pnpm --filter @ai4s/desktop exec vite \
    --host 127.0.0.1 --port "${DESKTOP_PORT}" --strictPort &
DEV_PID=$!
wait "${DEV_PID}"
DEV_PID=""
