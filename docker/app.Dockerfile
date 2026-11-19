# syntax=docker/dockerfile:1
FROM node:24.22.3-bookworm-slim AS web
WORKDIR /web
RUN npm install --global pnpm@12.15.0
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.0 /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    SBB_DATA_DIR=/data \
    SBB_BRAINS_DIR=/brains \
    SBB_MODELS_DIR=/models \
    SBB_WEB_DIR=/app/web/dist
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git git-lfs \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 sbb \
    && useradd --uid 10001 --gid sbb --create-home sbb \
    && mkdir -p /data /brains /models \
    && chown sbb:sbb /data /brains /models
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable
COPY --from=web /web/dist ./web/dist
USER sbb
EXPOSE 8484
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; r = urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8484/api/health', headers={'Host': 'localhost'}), timeout=4); assert r.status == 200"]
CMD ["uvicorn", "second_brain_builder.server:create_server", "--factory", "--host", "0.0.0.0", "--port", "8484"]
