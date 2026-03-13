# Faster-Whisper service
# Base: NVIDIA CUDA 13.0 runtime for ARM64 Blackwell compatibility
FROM nvcr.io/nvidia/cuda:13.0.0-runtime-ubuntu24.04 AS base

LABEL maintainer="ai-infra"
LABEL component="whisper"
LABEL tier="always-on"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv python3-pip \
        curl ffmpeg libsndfile1 \
        tini \
    && ln -sf /usr/bin/python3.12 /usr/bin/python3 \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# NOTE: faster-whisper depends on ctranslate2.
# If the pip wheel does not support sm_121, build from source:
#   pip install ctranslate2 --no-binary ctranslate2
# This requires the -devel base image instead of -runtime.
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY app.py .

EXPOSE 8000

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
