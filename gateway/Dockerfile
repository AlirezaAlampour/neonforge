# Gateway — lightweight API router + job queue
# No GPU needed. Plain Python image on ARM64.
FROM python:3.11-slim-bookworm AS base

LABEL maintainer="ai-infra"
LABEL component="gateway"

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ffmpeg \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info"]
