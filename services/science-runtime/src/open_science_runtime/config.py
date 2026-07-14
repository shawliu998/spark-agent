from __future__ import annotations

import os
from pathlib import Path

DATA_ROOT_ENV = "SCIENCE_RUNTIME_DATA_ROOT"
DEFAULT_DATA_ROOT = Path("/runtime-data")
SOCKET_PATH_ENV = "SCIENCE_RUNTIME_SOCKET"
DEFAULT_SOCKET_PATH = Path("/runtime-socket/runtime.sock")
KERNEL_NAME = "python3"
MAX_TIMEOUT_SECONDS = 120


def configured_data_root() -> Path:
    """Return the configured absolute data root without resolving request paths."""

    data_root = Path(os.environ.get(DATA_ROOT_ENV, str(DEFAULT_DATA_ROOT)))
    if not data_root.is_absolute():
        raise RuntimeError(f"{DATA_ROOT_ENV} must be an absolute path")
    return data_root


def configured_socket_path() -> Path:
    socket_path = Path(os.environ.get(SOCKET_PATH_ENV, str(DEFAULT_SOCKET_PATH)))
    if socket_path != DEFAULT_SOCKET_PATH:
        raise RuntimeError(
            f"{SOCKET_PATH_ENV} must be exactly {DEFAULT_SOCKET_PATH}; "
            "the launcher clears its dedicated parent volume"
        )
    return socket_path
