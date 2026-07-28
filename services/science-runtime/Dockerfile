FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=600 \
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

COPY requirements.lock ./requirements.lock
RUN python -m pip install \
      --require-hashes \
      --only-binary=:all: \
      --no-deps \
      --no-compile \
      --no-cache-dir \
      --requirement requirements.lock \
    && python -m pip check

ENV PIP_NO_INDEX=1 \
    PYTHONPATH=/app/src

COPY src ./src

RUN chmod -R a=rX /app/src \
    && groupadd --system --gid 10001 science-runtime \
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
