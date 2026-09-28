# Faster-Whisper service
# Base: NVIDIA CUDA 13.0 runtime for ARM64 Blackwell compatibility
FROM ghcr.io/astral-sh/uv:0.10.9 AS uv

FROM nvcr.io/nvidia/cuda:13.0.0-runtime-ubuntu24.04 AS base

LABEL maintainer="ai-infra"
LABEL component="whisper"
LABEL tier="always-on"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 \
        curl ffmpeg libsndfile1 \
        tini \
    && ln -sf /usr/bin/python3.12 /usr/bin/python3 \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=uv /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock safe_requirements.txt ./
# NOTE: faster-whisper depends on ctranslate2.
# If the wheel does not support sm_121, build ctranslate2 from source.
# This requires the -devel base image instead of -runtime.
RUN uv venv --python /usr/bin/python3.12 /opt/venv && \
    uv pip install \
        --python /opt/venv/bin/python \
        --strict \
        --requirements safe_requirements.txt
ENV PATH="/opt/venv/bin:$PATH"

COPY app.py .

EXPOSE 8000

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
