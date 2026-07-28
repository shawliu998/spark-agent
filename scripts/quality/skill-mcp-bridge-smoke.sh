#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
case "$(uname -m)" in
  arm64) OPENCODE="${ROOT_DIR}/apps/desktop/src-tauri/binaries/opencode-aarch64-apple-darwin" ;;
  x86_64) OPENCODE="${ROOT_DIR}/apps/desktop/src-tauri/binaries/opencode-x86_64-apple-darwin" ;;
  *) printf 'Skill MCP bridge smoke failed: unsupported architecture.\n' >&2; exit 1 ;;
esac
BRIDGE="${ROOT_DIR}/apps/desktop/src-tauri/target/debug/ai4s-workbench"
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/spark-skill-mcp.XXXXXX")"
PROJECT_ID="2d45e34b-c26b-45eb-bb70-894b32ae5f7f"
PROJECTS_ROOT="${TEMP_ROOT}/projects"
PROJECT_DIR="${PROJECTS_ROOT}/${PROJECT_ID}"
XDG_CONFIG="${TEMP_ROOT}/xdg-config"
XDG_DATA="${TEMP_ROOT}/xdg-data"
XDG_CACHE="${TEMP_ROOT}/xdg-cache"
XDG_STATE="${TEMP_ROOT}/xdg-state"
CONFIG_DIR="${XDG_CONFIG}/opencode"
DESCRIPTOR="${TEMP_ROOT}/session/spark-skill-mcp-connection.json"
SERVER_PID=""

fail() {
  printf 'Skill MCP bridge smoke failed: %s\n' "$1" >&2
  exit 1
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM HUP
  set +e
  if [[ -n "${SERVER_PID}" ]]; then
    kill "${SERVER_PID}" >/dev/null 2>&1
    wait "${SERVER_PID}" >/dev/null 2>&1
  fi
  if [[ "${TEMP_ROOT}" == *"/spark-skill-mcp."* ]]; then
    rm -rf "${TEMP_ROOT}"
  fi
  exit "${exit_code}"
}
trap cleanup EXIT INT TERM HUP

[[ -x "${OPENCODE}" ]] || fail "pinned OpenCode sidecar is unavailable."
[[ "$("${OPENCODE}" --version)" == "1.17.13" ]] \
  || fail "pinned OpenCode sidecar is not version 1.17.13."
[[ -x "${BRIDGE}" ]] || fail "debug Spark executable is unavailable; run cargo build first."

mkdir -p "${PROJECT_DIR}" "${CONFIG_DIR}" "${XDG_DATA}" "${XDG_CACHE}" \
  "${XDG_STATE}" "$(dirname "${DESCRIPTOR}")"
chmod 700 "${TEMP_ROOT}" "${PROJECTS_ROOT}" "${PROJECT_DIR}" "${XDG_CONFIG}" \
  "${CONFIG_DIR}" "${XDG_DATA}" "${XDG_CACHE}" "${XDG_STATE}" \
  "$(dirname "${DESCRIPTOR}")"

python3 - "${CONFIG_DIR}/opencode.json" "${BRIDGE}" "${DESCRIPTOR}" <<'PY'
import json
import sys

path, bridge, descriptor = sys.argv[1:]
with open(path, "x", encoding="utf-8") as output:
    json.dump(
        {
            "mcp": {
                "spark-skill-mcp": {
                    "type": "local",
                    "command": [bridge, "--spark-skill-mcp"],
                    "environment": {
                        "SPARK_SKILL_MCP_DESCRIPTOR": descriptor,
                    },
                    "enabled": True,
                    "timeout": 5000,
                }
            },
            "permission": {"*": "ask"},
        },
        output,
    )
PY
chmod 600 "${CONFIG_DIR}/opencode.json"

PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')"
(
  cd "${PROJECT_DIR}"
  exec env \
    XDG_CONFIG_HOME="${XDG_CONFIG}" \
    XDG_DATA_HOME="${XDG_DATA}" \
    XDG_CACHE_HOME="${XDG_CACHE}" \
    XDG_STATE_HOME="${XDG_STATE}" \
    OPENCODE_DISABLE_EXTERNAL_SKILLS=1 \
    OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1 \
    "${OPENCODE}" serve --hostname 127.0.0.1 --port "${PORT}"
) >"${TEMP_ROOT}/opencode.log" 2>&1 &
SERVER_PID=$!

python3 - "http://127.0.0.1:${PORT}" "${PROJECT_DIR}" <<'PY'
import json
import sys
import time
import urllib.parse
import urllib.request

base, project = sys.argv[1:]
for _ in range(80):
    try:
        query = urllib.parse.urlencode({"directory": project})
        with urllib.request.urlopen(base + "/mcp?" + query, timeout=1) as response:
            status = json.load(response)
        if status.get("spark-skill-mcp", {}).get("status") == "connected":
            print(
                json.dumps(
                    {
                        "directory": project,
                        "mcp": "spark-skill-mcp",
                        "protocolVersion": "2025-11-25",
                        "status": "connected",
                    },
                    sort_keys=True,
                )
            )
            raise SystemExit(0)
    except (OSError, ValueError):
        pass
    time.sleep(0.25)
raise SystemExit("OpenCode did not report the Spark MCP bridge as connected")
PY

if grep -F "SPARK_SKILL_MCP_DESCRIPTOR" "${TEMP_ROOT}/opencode.log" >/dev/null; then
  fail "the MCP descriptor environment name leaked to the OpenCode log."
fi
printf 'Skill MCP bridge smoke passed: project=%s status=connected\n' "${PROJECT_ID}"
