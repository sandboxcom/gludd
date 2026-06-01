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

LABEL maintainer="General Ludd Team"
LABEL description="General Ludd Agent"

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates gnupg2 unzip \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://releases.hashicorp.com/terraform/1.7.0/terraform_1.7.0_linux_amd64.zip -o /tmp/terraform.zip \
    && unzip /tmp/terraform.zip -d /usr/local/bin \
    && rm /tmp/terraform.zip

RUN curl -fsSL https://github.com/opentofu/opentofu/releases/download/v1.6.1/tofu_1.6.1_linux_amd64.zip -o /tmp/tofu.zip \
    && unzip /tmp/tofu.zip -d /usr/local/bin \
    && rm /tmp/tofu.zip

RUN groupadd --system gludd && \
    useradd --system --gid gludd --create-home gludd

WORKDIR /app

COPY --from=builder /build/dist/*.whl /tmp/wheels/

RUN pip install --no-cache-dir /tmp/wheels/*.whl && \
    rm -rf /tmp/wheels

COPY config/ config/
COPY playbooks/ playbooks/
COPY templates/ templates/

USER gludd

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')" || exit 1

ENTRYPOINT ["gludd", "daemon"]
