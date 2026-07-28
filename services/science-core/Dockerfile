FROM python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=600 \
    PIP_RETRIES=10 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPYCACHEPREFIX=/tmp/python-cache \
    HOME=/tmp/home \
    XDG_CACHE_HOME=/tmp/cache \
    SPARK_AGENT_CORE_DATA_DIR=/data \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.lock ./requirements.lock
COPY vendor/paper-search-mcp/paper_search_mcp-0.1.4+spark.3-py3-none-any.whl \
     /wheelhouse/paper_search_mcp-0.1.4+spark.3-py3-none-any.whl
COPY vendor/sgmllib3k/sgmllib3k-1.0.0-py3-none-any.whl \
     /wheelhouse/sgmllib3k-1.0.0-py3-none-any.whl
RUN python -m pip install \
      --require-hashes \
      --only-binary=:all: \
      --no-deps \
      --no-compile \
      --no-cache-dir \
      --find-links /wheelhouse \
      --requirement requirements.lock \
    && python -m pip check

ENV PIP_NO_INDEX=1

COPY src ./src
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations

RUN mkdir -p /data

EXPOSE 8765
CMD ["uvicorn", "open_science_core.app:app", "--host", "0.0.0.0", "--port", "8765"]
