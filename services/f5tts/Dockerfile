# syntax=docker/dockerfile:1
# 1. Use the modern base image that actually knows what a Blackwell GPU is
FROM nvcr.io/nvidia/pytorch:26.02-py3 AS base

LABEL maintainer="ai-infra"
LABEL component="${SERVICE}"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ffmpeg libsndfile1 libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 git tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .

# 2. Strip standard torch references from your requirements.txt
# This PREVENTS pip from accidentally overwriting the perfectly tuned 
# PyTorch that is already built into the nvcr.io base image.
RUN sed -i '/^torch/d' requirements.txt && \
    sed -i '/^torchvision/d' requirements.txt && \
    sed -i '/^torchaudio/d' requirements.txt && \
    sed -i '/^--extra-index-url/d' requirements.txt

# 3. Install your specific service requirements (F5-TTS, LivePortrait, etc.)
# Using BuildKit caching so it takes seconds, not minutes.
# ... (previous Dockerfile content remains the same) ...

# 3. Install specific dependencies FIRST, then the rest.
# We explicitly install transformers to avoid the pipeline error.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install transformers huggingface_hub && \
    pip install --no-deps f5-tts && \
    pip install -r requirements.txt || \
    pip install --break-system-packages -r requirements.txt

COPY app.py .
EXPOSE 8000
ENTRYPOINT ["tini", "--"]
# Explicitly use python3 (which points to 3.10 in the NGC container)
CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]