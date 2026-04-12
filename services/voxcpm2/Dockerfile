# syntax=docker/dockerfile:1
FROM nvcr.io/nvidia/pytorch:26.02-py3 AS base

LABEL maintainer="ai-infra"
LABEL component="voxcpm2"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ffmpeg libsndfile1 libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Preserve the vendor-tuned accelerator stack from the base image.
RUN cp -r /usr/local/lib/python3.12/dist-packages/torch* /opt/venv/lib/python3.12/site-packages/ && \
    cp -r /usr/local/lib/python3.12/dist-packages/nvfuser* /opt/venv/lib/python3.12/site-packages/ || true

RUN python -m pip uninstall -y torchao || true

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --disable-pip-version-check --upgrade-strategy only-if-needed -r requirements.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --disable-pip-version-check --no-deps torchaudio==2.10.0

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --disable-pip-version-check --no-deps voxcpm==2.0.2

# ARM64 fallback for torchaudio on the DGX vendor image.
RUN cat <<'EOF' >> /opt/venv/lib/python3.12/site-packages/torchaudio/_torchcodec.py
import soundfile as sf
import torch

def fallback_load(uri, *args, **kwargs):
    data, sr = sf.read(str(uri), dtype="float32", always_2d=True)
    return torch.from_numpy(data).T, sr

def fallback_save(uri, src, sample_rate, *args, **kwargs):
    sf.write(str(uri), src.T.cpu().numpy(), sample_rate)

load_with_torchcodec = fallback_load
save_with_torchcodec = fallback_save
EOF

COPY app.py .

EXPOSE 8000
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
