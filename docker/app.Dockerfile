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

# Shared libraries Chromium's headless shell links against (D-025), plus one font family.
# Playwright's --with-deps would add ~400 MB more (full font sets, X11 tools).
# Debian package versions follow the base image; pinning them here would break rebuilds.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libasound2t64 libatk-bridge2.0-0t64 libatk1.0-0t64 libatspi2.0-0t64 libdbus-1-3 libdrm2 \
        libgbm1 libglib2.0-0t64 libnspr4 libnss3 libxcomposite1 libxdamage1 libxfixes3 \
        libxkbcommon0 libxrandr2 libxi6 fonts-liberation ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# gitleaks (MIT) scans linked folders for secrets before indexing (SPEC §7.4, D-024).
ARG GITLEAKS_VERSION=8.30.1
ARG GITLEAKS_SHA256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
ADD --checksum=sha256:${GITLEAKS_SHA256} \
    https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz \
    /tmp/gitleaks.tar.gz
RUN tar -xzf /tmp/gitleaks.tar.gz -C /usr/local/bin gitleaks \
    && rm /tmp/gitleaks.tar.gz \
    && chmod 0755 /usr/local/bin/gitleaks \
    && gitleaks version

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
# The CLI (../cli, a path dependency) is installed with the app code below, so changing it does
# not rebuild this layer; only its pyproject is needed here.
COPY backend/pyproject.toml backend/uv.lock ./
COPY cli/pyproject.toml /cli/pyproject.toml
RUN --mount=type=cache,id=trustbook-uv,target=/root/.cache/uv \
    set -eu; \
    if [ "$BAKE_MODELS" = "1" ]; then \
        uv sync --frozen --no-dev --no-install-project --no-install-package trustbook-cli --extra ml; \
    else \
        uv sync --frozen --no-dev --no-install-project --no-install-package trustbook-cli; \
    fi

# --- Headless Chromium for JavaScript-only pages (Playwright, D-025). Video ffmpeg is not needed. ---
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
RUN set -eu; \
    if [ "$BAKE_MODELS" = "1" ]; then \
        python -m playwright install --only-shell chromium; \
        rm -rf /opt/pw-browsers/ffmpeg-*; \
        chmod -R a+rX /opt/pw-browsers; \
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
    DOCLING_ARTIFACTS_PATH=/opt/trustbook/models/docling \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_MODULES_CACHE=/tmp/trustbook-hf-modules

# --- Rarely changed files ---
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/trustbook-entrypoint
COPY --chmod=0644 docker/searxng/settings.yml /app/searxng/settings.yml

# --- UI ---
COPY --from=frontend /src/frontend/dist /app/static

# --- App code (+ the eval runner and public smoke set, so gates run with `docker compose exec`) ---
COPY backend/app /app/app
COPY eval /app/eval
RUN chmod -R a+rX /app/app /app/static /app/searxng /app/eval

# --- The `trustbook` CLI (D-087): `docker compose exec app trustbook …`; the eval runner imports
# its API client. Installed from a copy of cli/ (a wheel, not editable), without dependencies
# (httpx is already in the environment).
RUN --mount=type=cache,id=trustbook-uv,target=/root/.cache/uv \
    --mount=type=bind,source=cli,target=/tmp/build/cli,rw \
    set -eu; \
    rm -rf /cli; \
    uv pip install --no-deps /tmp/build/cli; \
    trustbook --version

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
