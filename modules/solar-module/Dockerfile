FROM python:3.12-slim-bookworm

ENV POETRY_VERSION=1.8.3 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

# Patch OS-level packages to eliminate known vulnerabilities
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir poetry==$POETRY_VERSION

# Build context must be repo root (see docker-compose.yml and azure-pipelines.yml)
COPY shared/ /shared
RUN pip install --no-cache-dir /shared

COPY modules/solar-module/pyproject.toml modules/solar-module/poetry.lock* ./
RUN poetry install --no-root --only main

COPY modules/solar-module/ .

ENV PYTHONUNBUFFERED=1
ENV EDGE_MODE=production

CMD ["python", "main.py"]
