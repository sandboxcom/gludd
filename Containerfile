# Stage 1: Build
FROM python:3.11-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /build

COPY pyproject.toml uv.lock ./
COPY src/ src/

RUN uv sync --frozen --no-dev --no-install-project
RUN uv build --out-dir /build/dist

# Stage 2: Runtime
FROM python:3.11-slim

LABEL maintainer="Hottentot Team"
LABEL description="Hottentot agentic harness agent"

RUN groupadd --system hottentot && \
    useradd --system --gid hottentot --create-home hottentot

WORKDIR /app

COPY --from=builder /build/dist/*.whl /tmp/wheels/

RUN pip install --no-cache-dir /tmp/wheels/*.whl && \
    rm -rf /tmp/wheels

USER hottentot

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1

ENTRYPOINT ["hottentot-worker"]
