# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.10.9 AS uvbin
FROM nvcr.io/nvidia/pytorch:26.02-py3 AS base

LABEL maintainer="ai-infra"
LABEL component="breeze-tts"

ARG BREEZE_TTS_SOURCE_REV=008f769016b0a24711becd7a4925030bc93f608c

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV TOKENIZERS_PARALLELISM=false

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl ffmpeg git libsndfile1 sox tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uvbin /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

# Keep the NVIDIA image's ARM64/Blackwell accelerator stack. uv resolves the
# application graph but does not replace vendor-tuned torch packages.
RUN uv sync --frozen --no-dev --no-install-project \
    --no-install-package torch \
    --no-install-package torchaudio \
    --no-install-package torchvision \
    --no-install-package triton \
    --no-install-package cuda-bindings \
    --no-install-package cuda-pathfinder \
    --no-install-package nvidia-cublas-cu12 \
    --no-install-package nvidia-cuda-cupti-cu12 \
    --no-install-package nvidia-cuda-nvrtc-cu12 \
    --no-install-package nvidia-cuda-runtime-cu12 \
    --no-install-package nvidia-cudnn-cu12 \
    --no-install-package nvidia-cufft-cu12 \
    --no-install-package nvidia-cufile-cu12 \
    --no-install-package nvidia-curand-cu12 \
    --no-install-package nvidia-cusolver-cu12 \
    --no-install-package nvidia-cusparse-cu12 \
    --no-install-package nvidia-cusparselt-cu12 \
    --no-install-package nvidia-nccl-cu12 \
    --no-install-package nvidia-nvjitlink-cu12 \
    --no-install-package nvidia-nvshmem-cu12 \
    --no-install-package nvidia-nvtx-cu12

# Copy the complete vendor accelerator stack after the application graph has
# been installed, keeping its CUDA 13 libraries and matching Triton together.
RUN for package in torch torchvision functorch nvfuser triton nvidia cuda; do \
        for source in /usr/local/lib/python3.12/dist-packages/${package}*; do \
            [ -e "${source}" ] && cp -r "${source}" /opt/venv/lib/python3.12/site-packages/; \
        done; \
    done

# The NGC image does not ship torchaudio. Install only its locked ARM64 wheel;
# --no-deps is intentional so uv cannot replace NVIDIA's torch/CUDA stack.
RUN uv pip install --python /opt/venv/bin/python --no-deps torchaudio==2.10.0 && \
    python -c "import torch, torchaudio, torchaudio.compliance.kaldi; print(torch.__version__, torchaudio.__version__)"

RUN git clone https://github.com/breezeblue-ai/breeze-tts.git /opt/breeze-tts-src && \
    git -C /opt/breeze-tts-src checkout "${BREEZE_TTS_SOURCE_REV}"

COPY app.py ./

EXPOSE 8000
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
