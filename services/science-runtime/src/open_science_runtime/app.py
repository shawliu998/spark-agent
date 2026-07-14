from __future__ import annotations

import stat
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from jupyter_client.kernelspec import KernelSpecManager, NoSuchKernel

from . import __version__
from .config import (
    KERNEL_NAME,
    MAX_TIMEOUT_SECONDS,
    configured_data_root,
    configured_socket_path,
)
from .execution import execute_notebook
from .schemas import ExecuteIn, ExecuteOut, HealthOut


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    socket_path = configured_socket_path()
    try:
        socket_stat = socket_path.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISSOCK(socket_stat.st_mode):
            raise RuntimeError("Runtime UDS path is not a socket")
        # The named socket volume is the access boundary. The execution user
        # retains a restrictive umask for notebooks and artifacts.
        socket_path.chmod(0o666)
    yield


app = FastAPI(title="Open Science Runtime", version=__version__, lifespan=lifespan)
_execution_slot = threading.Lock()


@app.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    data_root = configured_data_root()
    data_root_available = data_root.exists() and data_root.is_dir()
    try:
        KernelSpecManager().get_kernel_spec(KERNEL_NAME)
        kernel_available = True
    except (NoSuchKernel, OSError):
        kernel_available = False
    return HealthOut(
        status="ok" if data_root_available and kernel_available else "degraded",
        version=__version__,
        data_root=str(data_root),
        kernel=KERNEL_NAME,
        kernel_available=kernel_available,
        max_timeout_seconds=MAX_TIMEOUT_SECONDS,
    )


@app.post("/v1/execute", response_model=ExecuteOut)
def execute(payload: ExecuteIn) -> ExecuteOut:
    if not _execution_slot.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The science runtime is already executing a notebook",
        )
    try:
        return execute_notebook(payload)
    finally:
        _execution_slot.release()
