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

# Dependencies ONLY, and deliberately never the project itself.
#
# The virtualenv is ~870 MB. If installing the project touched it, every
# one-line source change would produce a new 870 MB layer to push, which on a
# flaky link is both slow and a reliable way to fail a deploy. `--frozen` keeps
# the deployed set identical to uv.lock, and the uv cache lives on a build mount
# so it never lands in a layer either.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/tmp/uv-cache \
    uv sync --frozen --no-dev --no-install-project

# --------------------------------------------------------------- runtime stage
FROM python:3.12-slim-bookworm

# Non-root. /data holds the snapshot pulled from R2 and must be writable: the
# five-minute REST sync writes recent entries into the same DuckDB file.
RUN useradd --create-home --home-dir /home/geyser --shell /usr/sbin/nologin geyser \
    && mkdir -p /data /app \
    && chown geyser:geyser /data /app

WORKDIR /app
USER geyser

# Dependencies, then source. In that order, and as separate layers: the venv is
# byte-identical across source changes, so a deploy that only touches Python or
# the dashboard pushes a few hundred kB instead of most of a gigabyte.
COPY --from=builder --chown=geyser:geyser /app/.venv /app/.venv
COPY --chown=geyser:geyser src /app/src
COPY --chown=geyser:geyser deploy /app/deploy

# The project is not pip-installed at all -- PYTHONPATH is enough to import it,
# and it keeps the venv independent of the source. Nothing at runtime needs the
# package metadata; the entrypoint imports `geyser_ai.api` directly.
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1 \
    GEYSER_AI_DATA_DIR=/data \
    GEYSER_AI_DB=/data/geysertimes.duckdb \
    GEYSER_AI_PORT=8080 \
    HOME=/home/geyser \
    MPLCONFIGDIR=/tmp/matplotlib

EXPOSE 8080

CMD ["python", "/app/deploy/entrypoint.py"]
