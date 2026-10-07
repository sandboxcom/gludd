# syntax=docker/dockerfile:1.7
#
# General Ludd Agent — production container image.
# Build context is the repository root (matches `make container-build`).
#
#   docker build --build-arg VERSION=0.1.0-alpha.5 \
#       -t ghcr.io/<owner>/general-ludd-agent:0.1.0-alpha.5 .
#   docker run --rm -p 8000:8000 -e GLUDD_AUTH_PSK=<secret> \
#       -v gludd-data:/var/lib/general-ludd \
#       ghcr.io/<owner>/general-ludd-agent:0.1.0-alpha.5
#
# The daemon binds 0.0.0.0 inside the container; because that is an external
# interface, it fail-closes (HTTP 503 on protected paths) UNLESS GLUDD_AUTH_PSK is
# supplied at runtime. For throwaway/dev only, pass GLUDD_ALLOW_NO_AUTH=1.

ARG PYTHON_VERSION=3.12

############################
# Stage 1 — builder (uv)   #
############################
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS builder

ARG PYTHON_VERSION

# Reproducible, hermetic uv install into a self-contained venv at /app/.venv.
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# Build toolchain for native extensions (tree-sitter, etc.); confined to the
# builder so it never reaches the runtime image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) Validate and install only the locked core dependency set. The application
# itself is deliberately excluded until its versioned wheel exists.
COPY pyproject.toml uv.lock README.md ./
COPY config/dependency_profiles.toml ./config/dependency_profiles.toml
COPY scripts/dependency_profiles.py ./scripts/dependency_profiles.py
COPY requirements/profiles ./requirements/profiles
RUN --mount=type=cache,target=/root/.cache/uv \
    python scripts/dependency_profiles.py sync \
        --root /app \
        --manifest config/dependency_profiles.toml \
        --set core \
        --environment /app/.venv \
        --python "${PYTHON_VERSION}" \
        --uv uv \
        --no-install-project

# 2) Build the versioned application wheel, then install it without resolving
# dependencies again. Every third-party package remains bound to the core locks.
ARG VERSION=0.1.0-alpha.5
COPY src ./src
COPY infra/terraform ./infra/terraform
COPY LICENSE THIRD_PARTY_LICENSES.md ./
RUN sed -i "s/^__version__ = \".*\"/__version__ = \"${VERSION}\"/" src/general_ludd/__init__.py \
 && sed -i "s/^version = \".*\"/version = \"${VERSION}\"/" pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv build --wheel --out-dir /app/dist \
 && uv pip install --python /app/.venv/bin/python --no-deps --reinstall /app/dist/*.whl

############################
# Stage 2 — runtime        #
############################
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ARG VERSION=0.1.0-alpha.5

# Runtime system deps:
#   git             — the agent performs git operations on workspaces
#   ca-certificates — TLS for model/provider/Galaxy calls
#   tini            — init/PID-1 reaper (the CLI spawns gunicorn as a child)
# psycopg[binary] bundles its own libpq, so libpq5 is intentionally omitted.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        tini \
 && rm -rf /var/lib/apt/lists/*

# Non-root runtime user; HOME doubles as the writable data root so the
# HOME-derived FileStore path (~/.local/share/general-ludd/filestore, which is
# constructor-only with no env override) and the SQLite DB land on one volume.
ENV APP_HOME=/var/lib/general-ludd
RUN groupadd --gid 1000 gludd \
 && useradd --uid 1000 --gid 1000 --home-dir ${APP_HOME} --create-home --shell /usr/sbin/nologin gludd

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=${APP_HOME} \
    XDG_DATA_HOME=${APP_HOME}/.local/share \
    XDG_CONFIG_HOME=${APP_HOME}/.config \
    GLUDD_DB_PATH=${APP_HOME}/general-ludd.db \
    GLUDD_CONFIG_DIR=/app/config \
    GLUDD_TEMPLATES_DIR=/app/templates \
    GLUDD_PLAYBOOKS_DIR=/app/playbooks \
    GLUDD_LOG_LEVEL=info
# AI provider keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, ZAI_API_KEY,
# OPENROUTER_API_KEY) and GLUDD_AUTH_PSK must be injected at runtime via
# --env / --env-file / secrets — never baked into the image.

WORKDIR /app

# Bring over the relocatable locked environment and runtime asset directories.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/LICENSE /app/LICENSE
COPY config /app/config
COPY templates /app/templates
COPY playbooks /app/playbooks

# Writable data volume (SQLite DB + filestore + per-user overlay).
RUN mkdir -p ${APP_HOME}/.local/share/general-ludd/filestore ${APP_HOME}/.config \
 && chown -R gludd:gludd ${APP_HOME}
VOLUME ["/var/lib/general-ludd"]

LABEL org.opencontainers.image.title="general-ludd-agent" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.description="General Ludd autonomous agent daemon" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/sandboxcom/gludd"

# Runtime-relative state (including the git-history ``.gludd`` directory) must
# resolve beneath the owned persistent volume, never beneath read-only /app.
WORKDIR ${APP_HOME}
USER gludd

EXPOSE 8000

# /healthz is a verified public (unauthenticated) liveness route — daemon.py:1637,
# exempted in the public-path set (daemon.py:1544) — so this passes even with PSK on.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"

# Run the application server as the foreground service. Tini owns and reaps the
# single Gunicorn tree, while startup exceptions and request/error logs remain
# attached to container stdio for health-smoke and operator diagnostics.
ENTRYPOINT ["tini", "--", "gunicorn", "general_ludd.daemon:create_daemon_app()", "--worker-class", "uvicorn_worker.UvicornWorker", "--workers", "1", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "--error-logfile", "-", "--capture-output"]
