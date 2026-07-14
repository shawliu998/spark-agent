# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENSCIENCE_CORE_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml ./
COPY src/open_science_core/__init__.py ./src/open_science_core/__init__.py
RUN --mount=type=cache,target=/root/.cache/pip pip install -e ".[literature]"
COPY src ./src

EXPOSE 8765
CMD ["uvicorn", "open_science_core.app:app", "--host", "0.0.0.0", "--port", "8765"]
