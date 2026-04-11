# Fish Speech API service
# Base: NVIDIA DGX Spark PyTorch image to preserve the vendor-tuned ARM64 stack
FROM nvcr.io/nvidia/pytorch:26.02-py3 AS base

LABEL maintainer="ai-infra"
LABEL component="fish_speech"
LABEL tier="warm"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        curl \
        ffmpeg \
        git \
        libasound2-dev \
        libglib2.0-0 \
        libgl1 \
        libportaudiocpp0 \
        libportaudio2 \
        libsm6 \
        libsndfile1 \
        libsox-dev \
        libxext6 \
        libxrender1 \
        portaudio19-dev \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ARG FISH_SPEECH_GIT_REF=main

RUN git clone --depth 1 --branch "${FISH_SPEECH_GIT_REF}" https://github.com/fishaudio/fish-speech.git /opt/fish-speech

RUN python - <<'PY'
import tomllib
from pathlib import Path

protected_prefixes = (
    "cuda",
    "cudnn",
    "nvidia-",
    "nvfuser",
    "pytorch-triton",
    "tensorrt",
    "torch",
    "torchaudio",
    "torchao",
    "torch-tensorrt",
    "torch_tensorrt",
    "torchvision",
    "transformer-engine",
    "triton",
    "xformers",
)

pyproject = tomllib.loads(Path("/opt/fish-speech/pyproject.toml").read_text(encoding="utf-8"))
deps = pyproject["project"]["dependencies"]

filtered = []
seen = set()
for dep in deps:
    line = dep.strip()
    lower = line.lower()
    if not line or lower.startswith(protected_prefixes):
        continue
    if line not in seen:
        filtered.append(line)
        seen.add(line)

Path("/tmp/fish_requirements.txt").write_text("\n".join(filtered) + "\n", encoding="utf-8")
PY

RUN python -m pip install --no-cache-dir setuptools-scm vcs-versioning

RUN python -m pip install --no-cache-dir -r /tmp/fish_requirements.txt

RUN python -m pip install --no-cache-dir --no-deps -e /opt/fish-speech

COPY overrides/fish_speech/inference_engine/reference_loader.py /opt/fish-speech/fish_speech/inference_engine/reference_loader.py
COPY overrides/fish_speech/models/dac/inference.py /opt/fish-speech/fish_speech/models/dac/inference.py
COPY overrides/fish_speech/utils/spectrogram.py /opt/fish-speech/fish_speech/utils/spectrogram.py
COPY overrides/tools/server/model_utils.py /opt/fish-speech/tools/server/model_utils.py

COPY compat/audiotools /app/audiotools
COPY compat/dac /app/dac

COPY bootstrap_model.py /app/bootstrap_model.py
COPY start.sh /app/start.sh

RUN chmod +x /app/start.sh

EXPOSE 8000

ENTRYPOINT ["tini", "--"]
CMD ["/app/start.sh"]
