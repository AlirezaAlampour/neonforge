# Supervisor sidecar — privileged container lifecycle manager.
# Has Docker socket access. NOT exposed to external network.
# The public gateway calls this service over the internal ai-net network
# to start/stop lazy-start containers.
FROM python:3.11-slim-bookworm AS base

LABEL maintainer="ai-infra"
LABEL component="supervisor"

ARG TARGETARCH
ARG COMPOSE_VERSION=v2.35.1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl docker.io tini \
    && case "${TARGETARCH}" in \
        amd64) compose_arch="x86_64" ;; \
        arm64) compose_arch="aarch64" ;; \
        *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
    && mkdir -p /usr/local/lib/docker/cli-plugins \
    && curl -fsSL "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${compose_arch}" \
        -o /usr/local/lib/docker/cli-plugins/docker-compose \
    && chmod +x /usr/local/lib/docker/cli-plugins/docker-compose \
    && docker compose version \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8000

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
