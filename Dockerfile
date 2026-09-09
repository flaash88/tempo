# syntax=docker/dockerfile:1

# The frontend is built here and copied in below, so the runtime image
# carries no node, no npm and no source — only the finished files.
FROM node:22-slim AS web

WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY web/ ./
RUN npm run build


FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first, so a code change does not invalidate the layer.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY alembic.ini ./
COPY tempo ./tempo

# Beside the package, where tempo.api.static looks for it first.
COPY --from=web /web/dist ./tempo/web
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# The data volume holds the database and the raw FIT files. It belongs to
# the unprivileged user the service runs as.
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin tempo \
    && mkdir -p /app/data/fit \
    && chown -R tempo:tempo /app/data

USER tempo

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

CMD ["uvicorn", "tempo.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
