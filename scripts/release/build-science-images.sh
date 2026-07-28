#!/usr/bin/env bash
# Build one architecture's two offline Science Core images and emit the exact
# four-file resource contract consumed by the Tauri loader.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CORE_TAG='io.github.shawliu998.sparkagent/science-core:0.2.0'
RUNTIME_TAG='io.github.shawliu998.sparkagent/science-runtime:0.2.0'
COMPOSE_SOURCE="$ROOT/services/compose.production.yaml"
SBOM_TOOL="$ROOT/scripts/release/science-sbom.py"
MIN_FREE_BYTES=$((8 * 1024 * 1024 * 1024))
MAX_ARCHIVE_BYTES=$((3 * 1024 * 1024 * 1024))
MAX_OUTPUT_BYTES=$((5 * 1024 * 1024 * 1024))

fail() {
  printf 'Science image build failed: %s\n' "$1" >&2
  exit 1
}

usage() {
  cat >&2 <<'EOF'
Usage: scripts/release/build-science-images.sh --platform linux/arm64|linux/amd64 --output <new-directory>
       scripts/release/build-science-images.sh --verify-fixtures

The build command does not push to a registry or use remote cache. It exports
two Docker-loadable single-platform archives, verifies their real Docker IDs/OS/architecture/tag,
validates the production Compose, and writes manifest.json from the resulting
bytes. The output directory must not already exist.

The fixture command performs only local static/input-contract checks. It does
not build an image or start a container.
EOF
}

platform=''
output=''
verify_fixtures=0
while (($#)); do
  case "$1" in
    --platform)
      [[ $# -ge 2 ]] || fail '--platform requires a value'
      platform="$2"
      shift 2
      ;;
    --output)
      [[ $# -ge 2 ]] || fail '--output requires a value'
      output="$2"
      shift 2
      ;;
    --verify-fixtures)
      verify_fixtures=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *) fail "unknown argument: $1" ;;
  esac
done

expected_arch=''
output_parent=''
staging=''
sbom_staging=''
sbom_output=''
fixture_root=''
complete=0
CORE_ID=''
RUNTIME_ID=''

run_bounded() {
  local seconds="$1"
  shift
  python3 - "$seconds" "$@" <<'PY'
import os
import signal
import subprocess
import sys

timeout = int(sys.argv[1])
command = sys.argv[2:]
process = subprocess.Popen(command, start_new_session=True)
try:
    raise SystemExit(process.wait(timeout=timeout))
except subprocess.TimeoutExpired:
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    print(f"command timed out after {timeout}s: {command[0]}", file=sys.stderr)
    raise SystemExit(124)
PY
}

remove_private_tree() {
  python3 - "$1" "$2" <<'PY'
import os
import shutil
import stat
import sys

original_target = os.path.abspath(sys.argv[1])
allowed_parent = os.path.realpath(sys.argv[2])
target_stat = os.lstat(original_target)
target = os.path.realpath(original_target)
if (
    os.path.dirname(target) != allowed_parent
    or not os.path.basename(target).startswith(".spark-science-")
    or not stat.S_ISDIR(target_stat.st_mode)
    or stat.S_ISLNK(target_stat.st_mode)
):
    raise SystemExit("refusing unsafe private-tree cleanup")
shutil.rmtree(target)
PY
}

report_loaded_images() {
  if [[ -n "$CORE_ID" ]]; then
    printf 'Local Docker tag intentionally retained: %s=%s\n' "$CORE_TAG" "$CORE_ID" >&2
  fi
  if [[ -n "$RUNTIME_ID" ]]; then
    printf 'Local Docker tag intentionally retained: %s=%s\n' "$RUNTIME_TAG" "$RUNTIME_ID" >&2
  fi
}

cleanup() {
  if [[ -n "$fixture_root" && -d "$fixture_root" && ! -L "$fixture_root" ]]; then
    remove_private_tree "$fixture_root" "$(dirname "$fixture_root")"
  fi
  if [[ "$complete" != 1 && -n "$staging" && -d "$staging" && ! -L "$staging" ]]; then
    remove_private_tree "$staging" "$output_parent"
  fi
  if [[ "$complete" != 1 && -n "$sbom_staging" && -d "$sbom_staging" && ! -L "$sbom_staging" ]]; then
    remove_private_tree "$sbom_staging" "$output_parent"
  fi
  report_loaded_images
}
trap cleanup EXIT

require_free_space() {
  python3 - "$1" "$MIN_FREE_BYTES" <<'PY'
import shutil
import sys

path, required = sys.argv[1], int(sys.argv[2])
available = shutil.disk_usage(path).free
if available < required:
    raise SystemExit(
        f"output filesystem has {available} free bytes; at least {required} are required"
    )
PY
}

require_archive_size() {
  python3 - "$1" "$MAX_ARCHIVE_BYTES" <<'PY'
import os
import sys

path, maximum = sys.argv[1], int(sys.argv[2])
size = os.stat(path, follow_symlinks=False).st_size
if size <= 0 or size > maximum:
    raise SystemExit(f"Docker-loadable archive size {size} is outside 1..{maximum} bytes")
PY
}

validate_runtime_inputs() {
  local data_dir="$1"
  local secret_file="$2"
  local expected_identity="${3:-}"
  python3 - "$data_dir" "$secret_file" "$expected_identity" <<'PY'
import os
import stat
import sys

data_dir, secret_file, expected_identity = sys.argv[1:]

def reject(message):
    raise SystemExit(f"runtime input preflight failed: {message}")

if not os.path.isabs(data_dir) or not os.path.isabs(secret_file):
    reject("data and secret paths must be absolute")
try:
    data_stat = os.lstat(data_dir)
except FileNotFoundError:
    reject("data directory is missing")
if not stat.S_ISDIR(data_stat.st_mode) or stat.S_ISLNK(data_stat.st_mode):
    reject("data path must be a real directory")

parent = os.path.dirname(secret_file)
name = os.path.basename(secret_file)
try:
    parent_before = os.lstat(parent)
except FileNotFoundError:
    reject("secret parent directory is missing")
if (
    not stat.S_ISDIR(parent_before.st_mode)
    or stat.S_ISLNK(parent_before.st_mode)
    or stat.S_IMODE(parent_before.st_mode) != 0o700
):
    reject("secret parent must be a real directory with mode 0700")

directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
try:
    parent_fd = os.open(parent, directory_flags)
except OSError:
    reject("secret parent could not be opened safely")
try:
    parent_open = os.fstat(parent_fd)
    if (parent_open.st_dev, parent_open.st_ino) != (parent_before.st_dev, parent_before.st_ino):
        reject("secret parent identity changed during validation")
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        secret_fd = os.open(name, file_flags, dir_fd=parent_fd)
    except (FileNotFoundError, OSError):
        reject("secret must be an existing non-symlink file")
    try:
        opened = os.fstat(secret_fd)
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    finally:
        os.close(secret_fd)
finally:
    os.close(parent_fd)

identity = (opened.st_dev, opened.st_ino)
if not stat.S_ISREG(opened.st_mode):
    reject("secret must be a regular file")
if opened.st_nlink != 1:
    reject("secret must have exactly one hard link")
if stat.S_IMODE(opened.st_mode) != 0o600:
    reject("secret file mode must be 0600")
if (before.st_dev, before.st_ino) != identity or (after.st_dev, after.st_ino) != identity:
    reject("secret inode changed during validation")
parent_after = os.lstat(parent)
if (parent_after.st_dev, parent_after.st_ino) != (parent_before.st_dev, parent_before.st_ino):
    reject("secret parent identity changed during validation")
actual_identity = f"{opened.st_dev}:{opened.st_ino}"
if expected_identity and expected_identity != actual_identity:
    reject("secret inode differs from the approved identity")
print(actual_identity)
PY
}

expect_preflight_failure() {
  local label="$1"
  shift
  if validate_runtime_inputs "$@" >/dev/null 2>&1; then
    fail "fixture unexpectedly accepted: $label"
  fi
  printf 'Fixture rejected as required: %s\n' "$label"
}

verify_static_fixtures() {
  command -v python3 >/dev/null 2>&1 || fail 'python3 is unavailable'
  command -v docker >/dev/null 2>&1 || fail 'Docker CLI is unavailable for Compose validation'
  docker compose version >/dev/null 2>&1 || fail 'Docker Compose is unavailable'

  fixture_root="$(mktemp -d "${TMPDIR:-/tmp}/.spark-science-fixtures.XXXXXX")"
  [[ -d "$fixture_root" && ! -L "$fixture_root" ]] || fail 'could not create fixture directory'
  chmod 0700 "$fixture_root"

  mkdir "$fixture_root/data" "$fixture_root/valid" "$fixture_root/symlink" \
    "$fixture_root/hardlink" "$fixture_root/mode" "$fixture_root/public" \
    "$fixture_root/stable"
  chmod 0700 "$fixture_root/valid" "$fixture_root/symlink" "$fixture_root/hardlink" \
    "$fixture_root/mode" "$fixture_root/stable"
  chmod 0755 "$fixture_root/public"
  printf 'fixture-only\n' >"$fixture_root/valid/secret"
  chmod 0600 "$fixture_root/valid/secret"
  local stable_identity
  stable_identity="$(validate_runtime_inputs "$fixture_root/data" "$fixture_root/valid/secret")"
  validate_runtime_inputs "$fixture_root/data" "$fixture_root/valid/secret" \
    "$stable_identity" >/dev/null

  expect_preflight_failure 'missing data directory' \
    "$fixture_root/missing-data" "$fixture_root/valid/secret"
  ln -s ../valid/secret "$fixture_root/symlink/secret"
  expect_preflight_failure 'secret symlink' "$fixture_root/data" "$fixture_root/symlink/secret"
  printf 'fixture-only\n' >"$fixture_root/hardlink/secret"
  chmod 0600 "$fixture_root/hardlink/secret"
  ln "$fixture_root/hardlink/secret" "$fixture_root/hardlink/second-link"
  expect_preflight_failure 'secret hardlink' "$fixture_root/data" "$fixture_root/hardlink/secret"
  printf 'fixture-only\n' >"$fixture_root/mode/secret"
  chmod 0644 "$fixture_root/mode/secret"
  expect_preflight_failure 'secret mode 0644' "$fixture_root/data" "$fixture_root/mode/secret"
  printf 'fixture-only\n' >"$fixture_root/public/secret"
  chmod 0600 "$fixture_root/public/secret"
  expect_preflight_failure 'secret parent mode other than 0700' \
    "$fixture_root/data" "$fixture_root/public/secret"
  printf 'fixture-only\n' >"$fixture_root/stable/secret"
  chmod 0600 "$fixture_root/stable/secret"
  stable_identity="$(validate_runtime_inputs "$fixture_root/data" "$fixture_root/stable/secret")"
  printf 'replacement\n' >"$fixture_root/stable/replacement"
  chmod 0600 "$fixture_root/stable/replacement"
  mv -f "$fixture_root/stable/replacement" "$fixture_root/stable/secret"
  expect_preflight_failure 'secret inode replacement' \
    "$fixture_root/data" "$fixture_root/stable/secret" "$stable_identity"

  python3 - "$ROOT" <<'PY'
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
expected = {
    "services/science-core/.dockerignore": [
        "**", "!Dockerfile", "!requirements.lock", "!vendor/",
        "!vendor/paper-search-mcp/",
        "!vendor/paper-search-mcp/paper_search_mcp-0.1.4+spark.3-py3-none-any.whl",
        "!vendor/sgmllib3k/",
        "!vendor/sgmllib3k/sgmllib3k-1.0.0-py3-none-any.whl",
        "!src/", "!src/**",
        "!alembic.ini", "!migrations/", "!migrations/**",
    ],
    "services/science-runtime/.dockerignore": [
        "**", "!Dockerfile", "!requirements.lock", "!src/", "!src/**",
    ],
}
for relative, rules in expected.items():
    actual = (root / relative).read_text(encoding="utf-8").splitlines()
    if actual != rules:
        raise SystemExit(f"deny-by-default allowlist differs: {relative}")

compose = (root / "services/compose.production.yaml").read_text(encoding="utf-8")
required = [
    "create_host_path: false",
    "pull_policy: never",
    "network_mode: none",
    'group_add:\n      - "10001"',
    "o: size=16m,uid=10001,gid=10001,mode=0710",
]
if any(item not in compose for item in required):
    raise SystemExit("production Compose is missing a required static boundary")
if "mode: 0400" in compose:
    raise SystemExit("production Compose must not claim a host-file secret mode")
PY

  local env_file="$fixture_root/compose.env"
  cat >"$env_file" <<EOF
SPARK_AGENT_CORE_IMAGE_ID=sha256:$(printf '1%.0s' {1..64})
SPARK_AGENT_RUNTIME_IMAGE_ID=sha256:$(printf '2%.0s' {1..64})
SPARK_AGENT_CORE_TOKEN=$(printf '0%.0s' {1..64})
SPARK_AGENT_CORE_HOST_DATA_DIR=$fixture_root/data
SPARK_AGENT_OPENAI_API_KEY_FILE=$fixture_root/valid/secret
EOF
  chmod 0600 "$env_file"
  stable_identity="$(validate_runtime_inputs "$fixture_root/data" "$fixture_root/valid/secret")"
  run_bounded 30 docker compose --env-file "$env_file" \
    --file "$COMPOSE_SOURCE" config --quiet
  validate_runtime_inputs "$fixture_root/data" "$fixture_root/valid/secret" \
    "$stable_identity" >/dev/null

  remove_private_tree "$fixture_root" "$(dirname "$fixture_root")"
  fixture_root=''
  python3 "$SBOM_TOOL" fixtures --root "$ROOT"
  printf 'Static and runtime-input fixtures passed without starting containers.\n'
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

archive_image_id() {
  python3 - "$1" "$2" <<'PY'
import hashlib
import json
import re
import sys
import tarfile

archive, expected_tag = sys.argv[1:]
with tarfile.open(archive, mode="r:") as image_tar:
    member = image_tar.getmember("manifest.json")
    if not member.isfile() or member.size > 1024 * 1024:
        raise SystemExit("invalid Docker archive manifest")
    stream = image_tar.extractfile(member)
    if stream is None:
        raise SystemExit("missing Docker archive manifest")
    manifest = json.load(stream)
    if not isinstance(manifest, list) or len(manifest) != 1:
        raise SystemExit("archive must contain exactly one image")
    entry = manifest[0]
    if entry.get("RepoTags") != [expected_tag]:
        raise SystemExit("archive tag differs from the fixed Spark tag")
    config = entry.get("Config")
    if not isinstance(config, str):
        raise SystemExit("archive has a non-canonical image config")
    docker_match = re.fullmatch(r"([0-9a-f]{64})\.json", config)
    containerd_match = re.fullmatch(r"blobs/sha256/([0-9a-f]{64})", config)
    match = docker_match or containerd_match
    if match is None:
        raise SystemExit("archive has a non-canonical image config")
    try:
        config_member = image_tar.getmember(config)
    except KeyError:
        raise SystemExit("archive image config is missing") from None
    if not config_member.isfile() or config_member.size > 1024 * 1024:
        raise SystemExit("archive image config is invalid")
    config_stream = image_tar.extractfile(config_member)
    if config_stream is None:
        raise SystemExit("archive image config is unreadable")
    config_bytes = config_stream.read()
    if hashlib.sha256(config_bytes).hexdigest() != match.group(1):
        raise SystemExit("archive image config digest differs from its path")
    try:
        image_config = json.loads(config_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SystemExit("archive image config is not valid JSON") from None
    if not isinstance(image_config, dict):
        raise SystemExit("archive image config is not an object")
    config_digest = match.group(1)
    accepted_ids = [f"sha256:{config_digest}"]
    try:
        index_member = image_tar.getmember("index.json")
    except KeyError:
        index_member = None
    if index_member is not None:
        if not index_member.isfile() or index_member.size > 1024 * 1024:
            raise SystemExit("Docker archive index is invalid")
        index_stream = image_tar.extractfile(index_member)
        if index_stream is None:
            raise SystemExit("Docker archive index is unreadable")
        index = json.load(index_stream)
        descriptors = index.get("manifests") if isinstance(index, dict) else None
        if (
            not isinstance(index, dict)
            or index.get("schemaVersion") != 2
            or not isinstance(descriptors, list)
            or len(descriptors) != 1
        ):
            raise SystemExit("Docker archive index must contain exactly one manifest")
        descriptor = descriptors[0]
        digest = descriptor.get("digest") if isinstance(descriptor, dict) else None
        digest_match = re.fullmatch(r"sha256:([0-9a-f]{64})", digest or "")
        if digest_match is None:
            raise SystemExit("Docker archive manifest digest is non-canonical")
        manifest_path = f"blobs/sha256/{digest_match.group(1)}"
        try:
            manifest_member = image_tar.getmember(manifest_path)
        except KeyError:
            raise SystemExit("Docker archive manifest is missing") from None
        if not manifest_member.isfile() or manifest_member.size != descriptor.get("size"):
            raise SystemExit("Docker archive manifest size differs from its descriptor")
        manifest_stream = image_tar.extractfile(manifest_member)
        if manifest_stream is None:
            raise SystemExit("Docker archive manifest is unreadable")
        manifest_bytes = manifest_stream.read()
        if hashlib.sha256(manifest_bytes).hexdigest() != digest_match.group(1):
            raise SystemExit("Docker archive manifest digest differs from its descriptor")
        docker_manifest = json.loads(manifest_bytes)
        docker_config = docker_manifest.get("config") if isinstance(docker_manifest, dict) else None
        if not isinstance(docker_config, dict) or docker_config.get("digest") != accepted_ids[0]:
            raise SystemExit("Docker archive manifest points to a different image config")
        accepted_ids.append(digest)
print("\n".join(accepted_ids))
PY
}

build_one() {
  local name="$1"
  local context="$2"
  local tag="$3"
  local archive="$4"
  local expected_user="$5"
  local archive_ids inspected id os_name architecture user accepted_id id_matches

  printf 'Building %s for %s…\n' "$name" "$platform"
  run_bounded 1800 docker buildx build \
    --progress plain \
    --platform "$platform" \
    --pull \
    --no-cache \
    --provenance=false \
    --sbom=false \
    --tag "$tag" \
    --output "type=docker,dest=$archive" \
    "$context"
  [[ -f "$archive" && ! -L "$archive" ]] || fail "$name archive was not produced"
  require_archive_size "$archive" || fail "$name Docker-loadable archive exceeds its size boundary"
  archive_ids="$(archive_image_id "$archive" "$tag")" || fail "$name archive is invalid"
  run_bounded 300 docker image load --input "$archive"
  inspected="$(run_bounded 30 docker image inspect --format \
    '{{.Id}}|{{.Os}}|{{.Architecture}}|{{with index .Config "User"}}{{.}}{{end}}' "$tag")" || \
    fail "$name image inspect failed"
  IFS='|' read -r id os_name architecture user <<<"$inspected"
  id_matches=0
  while IFS= read -r accepted_id; do
    if [[ "$id" == "$accepted_id" ]]; then id_matches=1; break; fi
  done <<<"$archive_ids"
  [[ "$id_matches" == 1 && "$id" =~ ^sha256:[0-9a-f]{64}$ ]] || \
    fail "$name image ID differs from its archive descriptors"
  [[ "$os_name" == linux && "$architecture" == "$expected_arch" ]] || \
    fail "$name image platform is $os_name/$architecture, expected linux/$expected_arch"
  [[ "$user" == "$expected_user" ]] || fail "$name image has unexpected runtime user: $user"
  if [[ "$name" == science-runtime ]]; then
    run_bounded 30 docker run --rm \
      --network none \
      --read-only \
      --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m \
      --cap-drop ALL \
      --security-opt no-new-privileges \
      --entrypoint python \
      "$id" \
      -c 'import open_science_runtime.launcher'
  fi
  if [[ "$name" == science-core ]]; then CORE_ID="$id"; else RUNTIME_ID="$id"; fi
}

if [[ "$verify_fixtures" == 1 ]]; then
  [[ -z "$platform" && -z "$output" ]] || \
    fail '--verify-fixtures cannot be combined with --platform or --output'
  verify_static_fixtures
  complete=1
  exit 0
fi

case "$platform" in
  linux/arm64) expected_arch='arm64' ;;
  linux/amd64) expected_arch='amd64' ;;
  *) fail '--platform must be exactly linux/arm64 or linux/amd64' ;;
esac
[[ -n "$output" ]] || fail '--output is required'
command -v docker >/dev/null 2>&1 || fail 'Docker CLI is unavailable'
command -v python3 >/dev/null 2>&1 || fail 'python3 is unavailable'
[[ "$(docker version --format '{{.Server.Os}}' 2>/dev/null)" == linux ]] || \
  fail 'Docker Desktop Linux engine is unavailable'
docker buildx version >/dev/null 2>&1 || fail 'Docker Buildx is unavailable'

output_parent="$(dirname "$output")"
mkdir -p "$output_parent"
[[ -d "$output_parent" && ! -L "$output_parent" ]] || fail 'output parent is unsafe'
output_parent="$(cd -P "$output_parent" && pwd -P)"
[[ "$output_parent" != / ]] || fail 'filesystem root cannot be the output parent'
output="$output_parent/$(basename "$output")"
[[ ! -e "$output" ]] || fail "refusing to overwrite output: $output"
sbom_output="$output_parent/science-core-sbom"
[[ ! -e "$sbom_output" ]] || fail "refusing to overwrite SBOM output: $sbom_output"
require_free_space "$output_parent" || fail 'output filesystem lacks the required free space'

umask 077
staging="$(mktemp -d "$output_parent/.spark-science-images.${expected_arch}.XXXXXX")"
[[ -d "$staging" && ! -L "$staging" ]] || fail 'could not create private staging'

core_archive="$staging/science-core.oci.tar"
runtime_archive="$staging/science-runtime.oci.tar"
build_one science-core "$ROOT/services/science-core" "$CORE_TAG" "$core_archive" ''
build_one science-runtime "$ROOT/services/science-runtime" "$RUNTIME_TAG" \
  "$runtime_archive" 'science-runtime'
[[ "$CORE_ID" != "$RUNTIME_ID" ]] || fail 'the two services resolved to one image ID'
cp "$COMPOSE_SOURCE" "$staging/compose.yaml"
chmod 0644 "$staging/compose.yaml" "$core_archive" "$runtime_archive"
compose_sha="$(sha256_file "$staging/compose.yaml")"
core_sha="$(sha256_file "$core_archive")"
runtime_sha="$(sha256_file "$runtime_archive")"

python3 - "$staging/manifest.json" "$compose_sha" \
  "$CORE_TAG" "$CORE_ID" "$core_sha" \
  "$RUNTIME_TAG" "$RUNTIME_ID" "$runtime_sha" <<'PY'
import json
import os
import sys

path, compose_sha, core_tag, core_id, core_sha, runtime_tag, runtime_id, runtime_sha = sys.argv[1:]
manifest = {
    "schemaVersion": 1,
    "composeSha256": compose_sha,
    "images": [
        {
            "archive": "science-core.oci.tar",
            "image": core_tag,
            "imageId": core_id,
            "sha256": core_sha,
        },
        {
            "archive": "science-runtime.oci.tar",
            "image": runtime_tag,
            "imageId": runtime_id,
            "sha256": runtime_sha,
        },
    ],
}
with open(path, "x", encoding="utf-8") as output:
    json.dump(manifest, output, ensure_ascii=True, indent=2)
    output.write("\n")
os.chmod(path, 0o644)
PY

mkdir "$staging/data"
printf 'test-secret\n' >"$staging/model-secret"
chmod 0600 "$staging/model-secret"
secret_identity="$(validate_runtime_inputs "$staging/data" "$staging/model-secret")"
expect_preflight_failure 'missing data directory in build gate' \
  "$staging/missing-data" "$staging/model-secret"
env_file="$staging/compose.env"
cat >"$env_file" <<EOF
SPARK_AGENT_CORE_IMAGE_ID=$CORE_ID
SPARK_AGENT_RUNTIME_IMAGE_ID=$RUNTIME_ID
SPARK_AGENT_CORE_TOKEN=$(printf '0%.0s' {1..64})
SPARK_AGENT_CORE_HOST_DATA_DIR=$staging/data
SPARK_AGENT_OPENAI_API_KEY_FILE=$staging/model-secret
EOF
chmod 0600 "$env_file"
run_bounded 30 docker compose --env-file "$env_file" \
  --file "$staging/compose.yaml" config --quiet
validate_runtime_inputs "$staging/data" "$staging/model-secret" \
  "$secret_identity" >/dev/null
rmdir "$staging/data"
unlink "$staging/model-secret"
unlink "$env_file"

actual_names="$(find "$staging" -mindepth 1 -maxdepth 1 -type f -exec basename {} \; | LC_ALL=C sort)"
expected_names=$'compose.yaml\nmanifest.json\nscience-core.oci.tar\nscience-runtime.oci.tar'
[[ "$actual_names" == "$expected_names" ]] || fail 'resource output is incomplete or contains extra files'
python3 - "$staging" "$MAX_OUTPUT_BYTES" <<'PY'
import os
import sys

root, maximum = sys.argv[1], int(sys.argv[2])
total = sum(
    entry.stat(follow_symlinks=False).st_size
    for entry in os.scandir(root)
    if entry.is_file(follow_symlinks=False)
)
if total > maximum:
    raise SystemExit(f"resource output total {total} exceeds {maximum} bytes")
PY

sbom_staging="$(mktemp -d "$output_parent/.spark-science-sbom.${expected_arch}.XXXXXX")"
[[ -d "$sbom_staging" && ! -L "$sbom_staging" ]] || fail 'could not reserve SBOM staging'
rmdir "$sbom_staging"
python3 "$SBOM_TOOL" generate \
  --root "$ROOT" \
  --runtime-dir "$staging" \
  --output "$sbom_staging" \
  --arch "$expected_arch"
python3 "$SBOM_TOOL" verify \
  --root "$ROOT" \
  --runtime-dir "$staging" \
  --sbom-dir "$sbom_staging" \
  --arch "$expected_arch"

python3 - "$staging" "$output" "$sbom_staging" "$sbom_output" <<'PY'
import os
import sys

runtime_staging, runtime_output, sbom_staging, sbom_output = sys.argv[1:]
os.rename(runtime_staging, runtime_output)
try:
    os.rename(sbom_staging, sbom_output)
except BaseException:
    os.rename(runtime_output, runtime_staging)
    raise
PY
complete=1
printf 'Science image build passed: platform=%s core=%s runtime=%s output=%s sbom=%s\n' \
  "$platform" "$CORE_ID" "$RUNTIME_ID" "$output" "$sbom_output"
