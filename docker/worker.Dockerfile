# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.11.0 /uv /usr/local/bin/uv
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    SBB_DATA_DIR=/data \
    SBB_BRAINS_DIR=/brains \
    SBB_MODELS_DIR=/models \
    HF_HOME=/models \
    HF_HUB_DISABLE_TELEMETRY=1 \
    QT_QPA_PLATFORM=offscreen
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates git git-lfs ffmpeg calibre libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 sbb \
    && useradd --uid 10001 --gid sbb --create-home sbb \
    && mkdir -p /data /brains /models \
    && chown sbb:sbb /data /brains /models
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
# The committed lock uses CPU wheels. Re-resolve against the chosen CUDA index
# inside this build only; --frozen alone would keep installing the CPU wheels.
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$TORCH_INDEX_URL" != "https://download.pytorch.org/whl/cpu" ]; then \
        python -c 'import os; from pathlib import Path; p = Path("pyproject.toml"); p.write_text(p.read_text().replace("https://download.pytorch.org/whl/cpu", os.environ["TORCH_INDEX_URL"]))' \
        && uv lock; \
    fi \
    && uv sync --frozen --no-dev --no-editable --extra worker
USER sbb
CMD ["python", "-m", "second_brain_builder.worker"]
