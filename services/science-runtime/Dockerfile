FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPYCACHEPREFIX=/tmp/python-cache \
    MPLBACKEND=Agg \
    HOME=/tmp/home \
    JUPYTER_RUNTIME_DIR=/tmp/jupyter-runtime \
    IPYTHONDIR=/tmp/ipython \
    MPLCONFIGDIR=/tmp/matplotlib \
    XDG_CACHE_HOME=/tmp/cache \
    SCIENCE_RUNTIME_DATA_ROOT=/runtime-data \
    SCIENCE_RUNTIME_SOCKET=/runtime-socket/runtime.sock

WORKDIR /app

COPY pyproject.toml ./
RUN mkdir -p src/open_science_runtime
COPY src/open_science_runtime/__init__.py ./src/open_science_runtime/__init__.py

RUN --mount=type=cache,target=/root/.cache/pip python -m pip install -e .

COPY src ./src

RUN groupadd --system --gid 10001 science-runtime \
    && useradd --system --uid 10001 --gid science-runtime \
        --create-home --home-dir /home/science-runtime \
        --shell /usr/sbin/nologin science-runtime \
    && mkdir -p \
        /runtime-data \
        /runtime-socket \
        /home/science-runtime/.local/share/jupyter/runtime \
    && chown -R science-runtime:science-runtime \
        /runtime-data \
        /runtime-socket \
        /home/science-runtime

USER science-runtime

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-m", "open_science_runtime.launcher", "--healthcheck"]

CMD ["python", "-m", "open_science_runtime.launcher"]
