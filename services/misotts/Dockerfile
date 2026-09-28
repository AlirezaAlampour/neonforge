# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.10.9 AS uvbin
FROM nvcr.io/nvidia/pytorch:26.02-py3 AS base

LABEL maintainer="ai-infra"
LABEL component="misotts"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake curl ffmpeg git libgl1 libglib2.0-0 libopus-dev libsm6 libsndfile1 libxext6 libxrender1 pkg-config tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uvbin /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV CMAKE=/usr/bin/cmake

RUN uv sync --frozen --no-dev --no-install-project \
    --no-install-package bitsandbytes \
    --no-install-package torch \
    --no-install-package torchaudio \
    --no-install-package torchao \
    --no-install-package torchvision

# Preserve the vendor-tuned accelerator stack from the NVIDIA base image.
RUN cp -r /usr/local/lib/python3.12/dist-packages/torch* /opt/venv/lib/python3.12/site-packages/ && \
    cp -r /usr/local/lib/python3.12/dist-packages/functorch* /opt/venv/lib/python3.12/site-packages/ && \
    cp -r /usr/local/lib/python3.12/dist-packages/torchao* /opt/venv/lib/python3.12/site-packages/ && \
    cp -r /usr/local/lib/python3.12/dist-packages/torchvision* /opt/venv/lib/python3.12/site-packages/ && \
    cp -r /usr/local/lib/python3.12/dist-packages/nvfuser* /opt/venv/lib/python3.12/site-packages/ || true

RUN git clone https://github.com/MisoLabsAI/MisoTTS.git /opt/misotts-src && \
    git -C /opt/misotts-src checkout 7535b5e55370ba8c309c918212a80e6d2b074b74

COPY app.py ./
COPY torchaudio ./torchaudio
COPY tts ./tts

EXPOSE 8000
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
