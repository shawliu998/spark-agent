#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DRIVER="${ROOT_DIR}/tests/integration/docker_smoke.py"
FIXTURE="${ROOT_DIR}/tests/integration/fixtures/b0-probe/SKILL.md"
case "$(uname -m)" in
  arm64) OPENCODE="${ROOT_DIR}/apps/desktop/src-tauri/binaries/opencode-aarch64-apple-darwin" ;;
  x86_64) OPENCODE="${ROOT_DIR}/apps/desktop/src-tauri/binaries/opencode-x86_64-apple-darwin" ;;
  *) printf 'Project skill discovery smoke failed: unsupported architecture.\n' >&2; exit 1 ;;
esac

RUN_SUFFIX="$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
COMPOSE_PROJECT="spark-agent-skill-discovery-${RUN_SUFFIX}"
CORE_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/spark-agent-skill-discovery.XXXXXX")"
APP_DATA_ROOT="${TEMP_ROOT}/app-data"
CORE_DATA_DIR="${APP_DATA_ROOT}/science-core-runtime/data"
STATE_FILE="${TEMP_ROOT}/projects.json"
PROFILE_ROOT="${TEMP_ROOT}/opencode-profile"
IMAGE_OVERRIDE="${TEMP_ROOT}/pinned-images.json"
COMPOSE_FILES=(--file "${ROOT_DIR}/compose.yaml")
USE_PINNED_IMAGES=false
SERVER_PID=""
CORE_URL=""

fail() {
  printf 'Project skill discovery smoke failed: %s\n' "$1" >&2
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
      "${COMPOSE_FILES[@]}" \
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
  trap - EXIT INT TERM HUP
  set +e
  if [[ -n "${SERVER_PID}" ]]; then
    kill "${SERVER_PID}" >/dev/null 2>&1
    wait "${SERVER_PID}" >/dev/null 2>&1
  fi
  compose down --rmi local --volumes --remove-orphans --timeout 5 >/dev/null 2>&1
  if [[ "${TEMP_ROOT}" == *"/spark-agent-skill-discovery."* ]]; then
    rm -rf "${TEMP_ROOT}"
  fi
  exit "${exit_code}"
}
trap cleanup EXIT INT TERM HUP

command -v docker >/dev/null 2>&1 || fail "Docker CLI was not found."
docker info >/dev/null 2>&1 || fail "Docker daemon is unavailable."
[[ -x "${OPENCODE}" ]] || fail "Pinned OpenCode sidecar is unavailable."
[[ "$("${OPENCODE}" --version)" == "1.17.13" ]] \
  || fail "Pinned OpenCode sidecar is not version 1.17.13."
[[ -f "${DRIVER}" && -f "${FIXTURE}" ]] || fail "Integration fixture is missing."
mkdir -p "${CORE_DATA_DIR}" "${PROFILE_ROOT}/config" "${PROFILE_ROOT}/data" \
  "${PROFILE_ROOT}/cache" "${PROFILE_ROOT}/state"
chmod 700 "${TEMP_ROOT}" "${APP_DATA_ROOT}" "${CORE_DATA_DIR}" "${PROFILE_ROOT}"

if docker image inspect \
  io.github.shawliu998.sparkagent/science-core:0.2.0 \
  io.github.shawliu998.sparkagent/science-runtime:0.2.0 >/dev/null 2>&1; then
  python3 - "${IMAGE_OVERRIDE}" <<'PY'
import json
import sys

with open(sys.argv[1], "x", encoding="utf-8") as output:
    json.dump(
        {
            "services": {
                "science-core": {
                    "image": "io.github.shawliu998.sparkagent/science-core:0.2.0"
                },
                "science-runtime": {
                    "image": "io.github.shawliu998.sparkagent/science-runtime:0.2.0"
                },
            }
        },
        output,
    )
PY
  COMPOSE_FILES+=(--file "${IMAGE_OVERRIDE}")
  USE_PINNED_IMAGES=true
fi

if [[ "${USE_PINNED_IMAGES}" == true ]]; then
  printf 'Starting isolated services from packaged 0.2.0 image IDs…\n'
else
  printf 'Building isolated Science Core services…\n'
  compose build science-runtime science-core
fi
compose up --detach --no-build science-runtime science-core
CORE_URL="$(discover_core_url)" || fail "Docker did not publish Science Core on loopback."
python3 "${DRIVER}" wait-ready --base-url "${CORE_URL}" --timeout 150
SPARK_AGENT_SMOKE_TOKEN="${CORE_TOKEN}" \
  python3 "${DRIVER}" create-skill-discovery-projects \
    --base-url "${CORE_URL}" --state-file "${STATE_FILE}"

PROJECT_A="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["projectA"])' "${STATE_FILE}")"
PROJECT_B="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["projectB"])' "${STATE_FILE}")"
python3 - "${PROJECT_A}" "${PROJECT_B}" <<'PY' \
  || fail "Science Core returned invalid or duplicate project IDs."
import re
import sys

canonical_uuid = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
project_a, project_b = sys.argv[1:]
if (
    canonical_uuid.fullmatch(project_a) is None
    or canonical_uuid.fullmatch(project_b) is None
    or project_a == project_b
):
    raise SystemExit(1)
PY
HOST_A="${CORE_DATA_DIR}/projects/${PROJECT_A}"
HOST_B="${CORE_DATA_DIR}/projects/${PROJECT_B}"
[[ -d "${HOST_A}" && -d "${HOST_B}" ]] || fail "Host project directories are missing."

compose exec -T science-core python -c \
  'from pathlib import Path; import sys; Path("/data/projects", sys.argv[1], ".opencode/skills/b0-probe").mkdir(parents=True)' \
  "${PROJECT_A}"
compose cp "${FIXTURE}" \
  "science-core:/data/projects/${PROJECT_A}/.opencode/skills/b0-probe/SKILL.md" >/dev/null
compose cp \
  "science-core:/data/projects/${PROJECT_A}/.opencode/skills/b0-probe/SKILL.md" \
  "${TEMP_ROOT}/container-SKILL.md" >/dev/null
cmp "${FIXTURE}" "${HOST_A}/.opencode/skills/b0-probe/SKILL.md"
cmp "${FIXTURE}" "${TEMP_ROOT}/container-SKILL.md"
[[ ! -e "${HOST_B}/.opencode/skills/b0-probe/SKILL.md" ]] \
  || fail "Project B unexpectedly contains the probe skill."

opencode_env=(
  env
  "OPENCODE_CONFIG_DIR=${PROFILE_ROOT}/config"
  "XDG_DATA_HOME=${PROFILE_ROOT}/data"
  "XDG_CACHE_HOME=${PROFILE_ROOT}/cache"
  "XDG_STATE_HOME=${PROFILE_ROOT}/state"
  "OPENCODE_DISABLE_EXTERNAL_SKILLS=1"
  "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1"
  "OPENCODE_PURE=1"
)
(cd "${HOST_A}" && "${opencode_env[@]}" "${OPENCODE}" debug skill --pure) \
  >"${TEMP_ROOT}/cli-a.json"
(cd "${HOST_B}" && "${opencode_env[@]}" "${OPENCODE}" debug skill --pure) \
  >"${TEMP_ROOT}/cli-b.json"
python3 - "${TEMP_ROOT}/cli-a.json" "${TEMP_ROOT}/cli-b.json" <<'PY'
import json
import sys

a = json.load(open(sys.argv[1], encoding="utf-8"))
b = json.load(open(sys.argv[2], encoding="utf-8"))
assert any(item.get("name") == "b0-probe" for item in a)
assert not any(item.get("name") == "b0-probe" for item in b)
PY

PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
(cd "${HOST_A}" && exec "${opencode_env[@]}" "${OPENCODE}" serve \
  --hostname 127.0.0.1 --port "${PORT}" --pure \
  >"${TEMP_ROOT}/opencode-serve.log" 2>&1) &
SERVER_PID=$!
python3 - "http://127.0.0.1:${PORT}" <<'PY'
import sys
import time
import urllib.request

url = sys.argv[1] + "/doc"
for _ in range(80):
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            if response.status == 200:
                raise SystemExit(0)
    except OSError:
        time.sleep(0.25)
raise SystemExit("OpenCode HTTP server did not become ready")
PY

python3 - "http://127.0.0.1:${PORT}" "${HOST_A}" "${HOST_B}" <<'PY'
import json
import pathlib
import sys
import urllib.parse
import urllib.request

base, project_a, project_b = sys.argv[1:]

def get(path):
    with urllib.request.urlopen(base + path, timeout=10) as response:
        return json.load(response)

def commands(directory):
    return get("/command?" + urllib.parse.urlencode({"directory": directory}))

a = commands(project_a)
b = commands(project_b)
probe = [item for item in a if item.get("name") == "b0-probe"]
assert len(probe) == 1 and probe[0].get("source") == "skill"
assert not any(item.get("name") == "b0-probe" for item in b)

# Pinned OpenCode 1.17.13 exposes /api/skill from the server's cwd instance.
# Observe it only after the product's directory-scoped /command warm-up; do not
# treat its query parameter as a cross-project authority.
skill_response = get("/api/skill")
assert pathlib.Path(skill_response["location"]["directory"]).resolve() == pathlib.Path(project_a).resolve()
cwd_skill_found = any(
    item.get("name") == "b0-probe" for item in skill_response.get("data", [])
)

print(json.dumps({
    "commandDiscovery": {
        "projectA": {"b0-probe": "skill"},
        "projectB": {"b0-probe": None},
    },
    "cwdSkillObservation": {
        "directory": str(pathlib.Path(project_a).resolve()),
        "b0-probe": cwd_skill_found,
        "authoritativeForProjectSwitching": False,
    },
}, sort_keys=True))
PY

FIXTURE_SHA="$(shasum -a 256 "${FIXTURE}" | awk '{print $1}')"
HOST_SHA="$(shasum -a 256 "${HOST_A}/.opencode/skills/b0-probe/SKILL.md" | awk '{print $1}')"
[[ "${FIXTURE_SHA}" == "${HOST_SHA}" ]] || fail "Host skill bytes changed."
printf 'Project skill discovery smoke passed: projectA=%s projectB=%s skillSha256=%s\n' \
  "${PROJECT_A}" "${PROJECT_B}" "${HOST_SHA}"
