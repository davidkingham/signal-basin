# Container image for the Cloudflare Containers deployment.
#
# The image carries only code. The DuckDB snapshot is pulled at startup from R2
# (see deploy/entrypoint.py) so that a deployed instance never downloads the
# 27 MB archive from GeyserTimes -- exactly one machine (a developer running
# `uv run geyser-ai ingest`) ever touches that endpoint.

# ---------------------------------------------------------------- build stage
FROM python:3.12-slim-bookworm AS builder

# uv is copied from its official distroless image rather than pip-installed.
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_CACHE_DIR=/tmp/uv-cache

WORKDIR /app

# Dependencies first so the slow resolve+install layer is cached across code
# changes. --frozen keeps the deployed set identical to uv.lock. The uv cache
# lives on a build mount so it never lands in an image layer.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/tmp/uv-cache uv sync --frozen --no-dev

# --------------------------------------------------------------- runtime stage
FROM python:3.12-slim-bookworm

# Non-root. /data holds the snapshot pulled from R2 and must be writable: the
# five-minute REST sync writes recent entries into the same DuckDB file.
RUN useradd --create-home --home-dir /home/geyser --shell /usr/sbin/nologin geyser \
    && mkdir -p /data /app \
    && chown geyser:geyser /data /app

WORKDIR /app
USER geyser

# uv installs the project itself as an editable pointer at /app/src, so both
# the virtualenv and the source tree have to come across.
COPY --from=builder --chown=geyser:geyser /app/.venv /app/.venv
COPY --chown=geyser:geyser src /app/src
COPY --chown=geyser:geyser deploy /app/deploy

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    GEYSER_AI_DATA_DIR=/data \
    GEYSER_AI_DB=/data/geysertimes.duckdb \
    GEYSER_AI_PORT=8080 \
    HOME=/home/geyser \
    MPLCONFIGDIR=/tmp/matplotlib

EXPOSE 8080

CMD ["python", "/app/deploy/entrypoint.py"]
