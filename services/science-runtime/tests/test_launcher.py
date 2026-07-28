# These tests intentionally exercise private socket-layout invariants without
# promoting implementation details into the runtime's public API.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import stat
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

from open_science_runtime.launcher import (
    _bind_listener,
    _prepare_socket_path,
    _socket_layout_matches,
    _watch_socket,
)


def test_prepare_socket_path_keeps_group_traverse_only(tmp_path: Path) -> None:
    socket_parent = tmp_path / "runtime-socket"
    socket_parent.mkdir(mode=0o755)
    (socket_parent / "stale").write_text("stale", encoding="utf-8")

    _prepare_socket_path(socket_parent / "runtime.sock")

    assert stat.S_IMODE(socket_parent.stat().st_mode) == 0o710
    assert list(socket_parent.iterdir()) == []


def test_socket_watchdog_requires_group_traverse_layout() -> None:
    with tempfile.TemporaryDirectory(prefix="spark-runtime-", dir="/tmp") as temporary:
        socket_parent = Path(temporary) / "socket"
        socket_parent.mkdir(mode=0o710)
        socket_path = socket_parent / "runtime.sock"
        listener, identity = _bind_listener(socket_path)
        try:
            assert identity[2] == 0o666
            assert _socket_layout_matches(socket_path, identity)
            sibling = socket_parent / "unexpected"
            sibling.write_text("unexpected", encoding="utf-8")
            assert not _socket_layout_matches(socket_path, identity)
            sibling.unlink()
            socket_path.chmod(0o600)
            assert not _socket_layout_matches(socket_path, identity)
            socket_path.chmod(0o666)
            assert _socket_layout_matches(socket_path, identity)
            socket_parent.chmod(0o700)
            assert not _socket_layout_matches(socket_path, identity)
        finally:
            listener.close()


def test_socket_watchdog_stops_after_inode_replacement() -> None:
    with tempfile.TemporaryDirectory(prefix="spark-runtime-", dir="/tmp") as temporary:
        socket_parent = Path(temporary) / "socket"
        socket_parent.mkdir(mode=0o710)
        socket_path = socket_parent / "runtime.sock"
        original, identity = _bind_listener(socket_path)
        original.close()
        socket_path.unlink()
        replacement, _ = _bind_listener(socket_path)
        server = SimpleNamespace(should_exit=False)
        stopped = threading.Event()
        watcher = threading.Thread(
            target=_watch_socket,
            args=(socket_path, identity, server, stopped),  # type: ignore[arg-type]
        )
        watcher.start()
        try:
            watcher.join(timeout=1.0)
            assert not watcher.is_alive()
            assert server.should_exit
        finally:
            stopped.set()
            watcher.join(timeout=1.0)
            replacement.close()
