#!/usr/bin/env bash

# Shared, side-effect-free release helpers. Callers remain responsible for
# validating the parent directory before passing it here.
select_single_dmg() {
  local bundle_dir="$1"
  local candidate
  local -a dmgs=()

  [[ -d "$bundle_dir" && ! -L "$bundle_dir" ]] || {
    printf 'DMG bundle directory is missing or a symlink: %s\n' "$bundle_dir" >&2
    return 1
  }
  while IFS= read -r -d '' candidate; do
    [[ ! -L "$candidate" ]] || {
      printf 'DMG candidate is a symlink: %s\n' "$candidate" >&2
      return 1
    }
    dmgs+=("$candidate")
  done < <(find -P "$bundle_dir" -type f -name '*.dmg' -print0)

  if [[ "${#dmgs[@]}" -ne 1 ]]; then
    printf 'Expected exactly one DMG, found %d\n' "${#dmgs[@]}" >&2
    return 1
  fi
  printf '%s\n' "${dmgs[0]}"
}

science_target_arch() {
  case "$1" in
    aarch64-apple-darwin) printf 'arm64\n' ;;
    x86_64-apple-darwin) printf 'amd64\n' ;;
    *)
      printf 'Unsupported Science runtime target: %s\n' "$1" >&2
      return 1
      ;;
  esac
}

validate_science_resource_directories() {
  local repo_root="$1"
  local runtime_dir="$2"
  local sbom_dir="$3"
  local target="$4"
  local image_arch

  image_arch="$(science_target_arch "$target")" || return 1
  python3 - "$runtime_dir" "$sbom_dir" <<'PY'
import os
import stat
import sys

expected = {
    sys.argv[1]: {
        "compose.yaml",
        "manifest.json",
        "science-core.oci.tar",
        "science-runtime.oci.tar",
    },
    sys.argv[2]: {
        "manifest.json",
        "science-core.spdx.json",
        "science-runtime.spdx.json",
    },
}
identities = set()
directory_identities = []
for raw, names in expected.items():
    path = os.path.abspath(raw)
    before = os.lstat(path)
    if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise SystemExit(f"Science resource directory is unsafe: {os.path.basename(path)}")
    actual = {entry.name for entry in os.scandir(path)}
    if actual != names:
        raise SystemExit(
            f"Science resource directory contract differs: {os.path.basename(path)}"
        )
    directory_identities.append((path, before.st_dev, before.st_ino))
    for name in sorted(names):
        item = os.path.join(path, name)
        metadata = os.lstat(item)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise SystemExit(f"Science resource is not a single-link regular file: {name}")
        identity = (metadata.st_dev, metadata.st_ino)
        if identity in identities:
            raise SystemExit(f"Science resources share an inode: {name}")
        identities.add(identity)
if len({(device, inode) for _, device, inode in directory_identities}) != len(
    directory_identities
):
    raise SystemExit("Science resource directories share an inode")
for path, device, inode in directory_identities:
    after = os.lstat(path)
    if (
        (after.st_dev, after.st_ino) != (device, inode)
        or not stat.S_ISDIR(after.st_mode)
        or stat.S_ISLNK(after.st_mode)
    ):
        raise SystemExit(f"Science resource directory changed: {os.path.basename(path)}")
PY
  local validation_status=$?
  ((validation_status == 0)) || return "$validation_status"
  python3 "$repo_root/scripts/release/science-sbom.py" verify \
    --root "$repo_root" \
    --runtime-dir "$runtime_dir" \
    --sbom-dir "$sbom_dir" \
    --arch "$image_arch"
}

validate_science_runtime_bundle() {
  local repo_root="$1"
  local artifact_root="$2"
  local target="$3"

  python3 - "$artifact_root" <<'PY'
import os
import stat
import sys

root = os.path.abspath(sys.argv[1])
before = os.lstat(root)
if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
    raise SystemExit("Science runtime artifact root must be a real directory")
if {entry.name for entry in os.scandir(root)} != {"runtime", "science-core-sbom"}:
    raise SystemExit("Science runtime artifact root must contain exactly runtime and science-core-sbom")
children = []
for name in ("runtime", "science-core-sbom"):
    child = os.path.join(root, name)
    metadata = os.lstat(child)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise SystemExit(f"Science runtime artifact child is unsafe: {name}")
    children.append((metadata.st_dev, metadata.st_ino))
if len(set(children)) != 2:
    raise SystemExit("Science runtime artifact directories share an inode")
after = os.lstat(root)
if (
    (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
    or not stat.S_ISDIR(after.st_mode)
    or stat.S_ISLNK(after.st_mode)
):
    raise SystemExit("Science runtime artifact root changed during validation")
PY
  local root_validation_status=$?
  ((root_validation_status == 0)) || return "$root_validation_status"
  validate_science_resource_directories \
    "$repo_root" \
    "$artifact_root/runtime" \
    "$artifact_root/science-core-sbom" \
    "$target"
}

generate_science_tauri_overlay() {
  local repo_root="$1"
  local artifact_root="$2"
  local target="$3"
  local overlay_path="$4"

  validate_science_runtime_bundle "$repo_root" "$artifact_root" "$target" || return 1
  python3 - "$artifact_root" "$overlay_path" <<'PY'
import json
import os
import stat
import sys

artifact_root = os.path.realpath(sys.argv[1])
output = os.path.abspath(sys.argv[2])
parent = os.path.dirname(output)
parent_before = os.lstat(parent)
if (
    not stat.S_ISDIR(parent_before.st_mode)
    or stat.S_ISLNK(parent_before.st_mode)
    or stat.S_IMODE(parent_before.st_mode) != 0o700
):
    raise SystemExit("controlled Tauri config parent must be a real 0700 directory")
if os.path.lexists(output):
    raise SystemExit("controlled Tauri config already exists")

resources = {}
for directory, names in (
    (
        "runtime",
        (
            "compose.yaml",
            "manifest.json",
            "science-core.oci.tar",
            "science-runtime.oci.tar",
        ),
    ),
    (
        "science-core-sbom",
        ("manifest.json", "science-core.spdx.json", "science-runtime.spdx.json"),
    ),
):
    for name in names:
        source = os.path.join(artifact_root, directory, name)
        resources[source] = f"{directory}/{name}" if directory != "runtime" else f"science-core/{name}"

config = {"bundle": {"resources": resources}}
descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
    json.dump(config, stream, ensure_ascii=True, indent=2, sort_keys=True)
    stream.write("\n")
metadata = os.lstat(output)
parent_after = os.lstat(parent)
if (
    not stat.S_ISREG(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_nlink != 1
    or stat.S_IMODE(metadata.st_mode) != 0o600
    or (parent_after.st_dev, parent_after.st_ino)
    != (parent_before.st_dev, parent_before.st_ino)
):
    raise SystemExit("controlled Tauri config failed post-write validation")
PY
}

verify_bundled_science_resources() {
  local repo_root="$1"
  local artifact_root="$2"
  local app_resources="$3"
  local target="$4"
  local directory name

  validate_science_runtime_bundle "$repo_root" "$artifact_root" "$target" || return 1
  validate_science_resource_directories \
    "$repo_root" \
    "$app_resources/science-core" \
    "$app_resources/science-core-sbom" \
    "$target" || return 1
  for directory in runtime science-core-sbom; do
    if [[ "$directory" == runtime ]]; then
      bundled_directory='science-core'
    else
      bundled_directory='science-core-sbom'
    fi
    for name in $(find "$artifact_root/$directory" -mindepth 1 -maxdepth 1 -type f -exec basename {} \; | LC_ALL=C sort); do
      cmp -s "$artifact_root/$directory/$name" "$app_resources/$bundled_directory/$name" || {
        printf 'Bundled Science resource differs from independent input: %s/%s\n' \
          "$bundled_directory" "$name" >&2
        return 1
      }
    done
  done
}

release_file_size() {
  if stat -f '%z' "$1" >/dev/null 2>&1; then
    stat -f '%z' "$1"
  else
    stat -c '%s' "$1"
  fi
}

release_file_sha256() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    printf 'No SHA-256 implementation is available\n' >&2
    return 1
  fi
}

# v1 commits to each sorted relative path, byte size, and file SHA-256.
release_content_tree_v1() {
  python3 - "$1" <<'PY'
import hashlib
import os
import stat
import sys

root = os.path.abspath(sys.argv[1])
digest = hashlib.sha256()
total = 0
for parent, directories, files in os.walk(root, followlinks=False):
    directories.sort()
    files.sort()
    for name in files:
        path = os.path.join(parent, name)
        metadata = os.lstat(path)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise SystemExit("release tree contains a non-regular file")
        relative = os.path.relpath(path, root).encode()
        file_digest = hashlib.sha256()
        with open(path, "rb") as stream:
            while chunk := stream.read(1024 * 1024):
                file_digest.update(chunk)
        total += metadata.st_size
        digest.update(relative + b"\0" + str(metadata.st_size).encode() + b"\0")
        digest.update(file_digest.digest())
print(f"{total}|{digest.hexdigest()}")
PY
}
