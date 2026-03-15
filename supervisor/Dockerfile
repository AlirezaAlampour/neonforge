# Supervisor sidecar — privileged container lifecycle manager.
# Has Docker socket access. NOT exposed to external network.
# The public gateway calls this service over the internal ai-net network
# to start/stop lazy-start containers.
FROM python:3.11-slim-bookworm AS base

LABEL maintainer="ai-infra"
LABEL component="supervisor"

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl docker.io docker-compose-v2 tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8000

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
