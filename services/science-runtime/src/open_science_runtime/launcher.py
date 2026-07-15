from __future__ import annotations

import json
import shutil
import socket
import stat
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import uvicorn

from .config import configured_socket_path

_SocketIdentity = tuple[int, int, int]


def _prepare_socket_path(socket_path: Path) -> None:
    parent = socket_path.parent
    try:
        parent_stat = parent.lstat()
    except OSError as error:
        raise RuntimeError("Runtime socket directory is unavailable") from error
    if not stat.S_ISDIR(parent_stat.st_mode) or stat.S_ISLNK(parent_stat.st_mode):
        raise RuntimeError("Runtime socket parent must be a real directory")
    # The mount is a dedicated 16 MiB tmpfs. Restore its mode and clear every
    # entry so notebook-created siblings, directories, and poisoned symlinks
    # cannot persist across the container's restart policy.
    parent.chmod(0o700)
    for entry in parent.iterdir():
        try:
            entry_stat = entry.lstat()
            if stat.S_ISDIR(entry_stat.st_mode):
                shutil.rmtree(entry)
            else:
                # unlink() removes a symlink itself; it never follows its target.
                entry.unlink(missing_ok=True)
        except OSError as error:
            raise RuntimeError("Could not clear the dedicated runtime socket volume") from error


def _healthcheck(socket_path: Path) -> None:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(2.0)
    try:
        client.connect(str(socket_path))
        client.sendall(b"GET /health HTTP/1.1\r\nHost: runtime\r\nConnection: close\r\n\r\n")
        response = bytearray()
        while chunk := client.recv(4096):
            response.extend(chunk)
    finally:
        client.close()
    try:
        headers, body = bytes(response).split(b"\r\n\r\n", maxsplit=1)
        status_line = headers.split(b"\r\n", maxsplit=1)[0]
        health_object: object = json.loads(body)
    except ValueError as error:
        raise RuntimeError("Runtime healthcheck returned an invalid HTTP response") from error
    if (
        b" 200 " not in status_line
        or not isinstance(health_object, dict)
        or cast(Mapping[str, object], health_object).get("status") != "ok"
    ):
        raise RuntimeError(f"Runtime healthcheck failed: {status_line!r} {body!r}")


def _socket_identity(socket_path: Path) -> _SocketIdentity:
    socket_stat = socket_path.lstat()
    if not stat.S_ISSOCK(socket_stat.st_mode):
        raise RuntimeError("Runtime UDS path is not a socket")
    return socket_stat.st_dev, socket_stat.st_ino, stat.S_IMODE(socket_stat.st_mode)


def _bind_listener(socket_path: Path) -> tuple[socket.socket, _SocketIdentity]:
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(socket_path))
        listener.listen(128)
        listener.set_inheritable(True)
        socket_path.chmod(0o666)
        identity = _socket_identity(socket_path)
        if identity[2] != 0o666:
            raise RuntimeError("Runtime UDS permissions changed while binding")
    except Exception:
        listener.close()
        raise
    return listener, identity


def _socket_matches(socket_path: Path, identity: _SocketIdentity) -> bool:
    try:
        return _socket_identity(socket_path) == identity
    except (OSError, RuntimeError):
        return False


def _socket_layout_matches(socket_path: Path, identity: _SocketIdentity) -> bool:
    parent = socket_path.parent
    try:
        parent_stat = parent.lstat()
        if (
            not stat.S_ISDIR(parent_stat.st_mode)
            or stat.S_IMODE(parent_stat.st_mode) != 0o700
        ):
            return False
        entries = list(parent.iterdir())
    except OSError:
        return False
    return (
        len(entries) == 1
        and entries[0].name == socket_path.name
        and _socket_matches(socket_path, identity)
    )


def _watch_socket(
    socket_path: Path,
    identity: _SocketIdentity,
    server: uvicorn.Server,
    stopped: threading.Event,
) -> None:
    while not stopped.wait(0.25):
        if not _socket_layout_matches(socket_path, identity):
            server.should_exit = True
            return


def _serve(socket_path: Path) -> None:
    _prepare_socket_path(socket_path)
    listener, identity = _bind_listener(socket_path)
    config = uvicorn.Config(
        "open_science_runtime.app:app",
        access_log=False,
        proxy_headers=False,
        server_header=False,
    )
    server = uvicorn.Server(config)
    stopped = threading.Event()
    watchdog = threading.Thread(
        target=_watch_socket,
        args=(socket_path, identity, server, stopped),
        name="runtime-socket-watchdog",
        daemon=True,
    )
    watchdog.start()
    try:
        try:
            server.run(sockets=[listener])
        except KeyboardInterrupt:
            pass
    finally:
        stopped.set()
        watchdog.join(timeout=1.0)
        listener.close()
        if _socket_matches(socket_path, identity):
            socket_path.unlink()


def main() -> None:
    socket_path = configured_socket_path()
    if sys.argv[1:] == ["--healthcheck"]:
        _healthcheck(socket_path)
        return
    if sys.argv[1:]:
        raise SystemExit("Usage: python -m open_science_runtime.launcher [--healthcheck]")
    _serve(socket_path)


if __name__ == "__main__":
    main()
