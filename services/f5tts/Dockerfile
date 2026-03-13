# F5-TTS service
# Uses CUDA 13.0 devel image because PyTorch may need to JIT-compile
# kernels for sm_121 (Blackwell GB10).
FROM nvcr.io/nvidia/cuda:13.0.0-devel-ubuntu24.04 AS base

LABEL maintainer="ai-infra"
LABEL component="f5tts"
LABEL tier="warm"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV TORCH_CUDA_ARCH_LIST="12.1"

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.12 python3.12-venv python3.12-dev python3-pip \
        curl ffmpeg libsndfile1 git \
        tini \
    && ln -sf /usr/bin/python3.12 /usr/bin/python3 \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY app.py .

EXPOSE 8000

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
