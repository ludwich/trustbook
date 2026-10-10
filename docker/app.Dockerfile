# syntax=docker/dockerfile:1
#
# Trustbook app image: built React UI + FastAPI backend + baked CPU models (SPEC §2, §4).
# Build context: repository root.
#
#   docker build -f docker/app.Dockerfile -t trustbook:dev .                      # full image
#   docker build -f docker/app.Dockerfile --build-arg BAKE_MODELS=0 -t trustbook:ci .   # no models (D-006)
#
# Layer order for caching: dependencies -> models -> rarely changed files -> UI -> app code.

ARG NODE_IMAGE=node:22-slim
ARG PYTHON_IMAGE=python:3.12-slim-trixie
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.13.0

FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------------------------------------
# Stage 1: frontend build -> /src/frontend/dist
# ---------------------------------------------------------------------------------------------
FROM ${NODE_IMAGE} AS frontend

# pnpm via npm, not corepack (SPEC §2); same version as package.json "packageManager" and CI.
RUN npm install -g pnpm@10.34.6 \
    && npm cache clean --force

WORKDIR /src/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN --mount=type=cache,id=trustbook-pnpm-store,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

COPY frontend/ ./
RUN pnpm build

# ---------------------------------------------------------------------------------------------
# Stage 2: Python runtime
# ---------------------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS runtime

# 1 = install the `ml` extra and bake the pinned models (release images).
# 0 = no ML packages and no models (CI build check, DECISIONS D-006).
ARG BAKE_MODELS=1

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    VIRTUAL_ENV=/app/.venv \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DO_NOT_TRACK=1 \
    NEEDLE_TELEMETRY=0 \
    OTEL_SDK_DISABLED=true

WORKDIR /app

# --- Dependencies (CPU torch comes from the pytorch-cpu index pinned in pyproject/uv.lock) ---
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,id=trustbook-uv,target=/root/.cache/uv \
    set -eu; \
    if [ "$BAKE_MODELS" = "1" ]; then \
        uv sync --frozen --no-dev --no-install-project --extra ml; \
    else \
        uv sync --frozen --no-dev --no-install-project; \
    fi

# --- Models (pinned revisions; only network access the build needs besides package indexes) ---
RUN --mount=type=bind,source=docker/download_models.py,target=/tmp/build/download_models.py \
    set -eu; \
    if [ "$BAKE_MODELS" = "1" ]; then \
        python /tmp/build/download_models.py --dest /opt/trustbook/models; \
    else \
        mkdir -p /opt/trustbook/models; \
    fi; \
    chmod -R a+rX /opt/trustbook

# From here on the image never talks to Hugging Face.
ENV TRUSTBOOK_DATA=/data \
    TRUSTBOOK_BROWSE_ROOT=/host \
    TRUSTBOOK_MODELS_DIR=/opt/trustbook/models \
    TRUSTBOOK_STATIC_DIR=/app/static \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_MODULES_CACHE=/tmp/trustbook-hf-modules

# --- Rarely changed files ---
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/trustbook-entrypoint
COPY --chmod=0644 docker/searxng/settings.yml /app/searxng/settings.yml

# --- UI ---
COPY --from=frontend /src/frontend/dist /app/static

# --- App code ---
COPY backend/app /app/app
RUN chmod -R a+rX /app/app /app/static /app/searxng

# Prove every baked model loads offline and scores sensibly (fails the build otherwise).
# No network: a model that tries to fetch anything at load time breaks the build here.
RUN --network=none \
    set -eu; \
    if [ "$BAKE_MODELS" = "1" ]; then \
        python -m app.capabilities.bundled_check; \
    fi; \
    rm -rf /tmp/trustbook-hf-modules /root/.cache

ARG TRUSTBOOK_VERSION=dev
ENV TRUSTBOOK_VERSION=${TRUSTBOOK_VERSION}

LABEL org.opencontainers.image.title="Trustbook" \
      org.opencontainers.image.description="Trustbook - trust your notebook. Local-first grounded research notebook." \
      org.opencontainers.image.source="https://github.com/ludwich/trustbook" \
      org.opencontainers.image.url="https://github.com/ludwich/trustbook" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="${TRUSTBOOK_VERSION}"

EXPOSE 8484

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8484/api/health', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["/usr/local/bin/trustbook-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8484"]
