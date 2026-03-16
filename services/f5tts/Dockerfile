# syntax=docker/dockerfile:1
FROM nvcr.io/nvidia/pytorch:26.02-py3 AS base

LABEL maintainer="ai-infra"
LABEL component="${SERVICE}"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ffmpeg libsndfile1 libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 git tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt safe_requirements.txt ./
COPY tools/ ./tools/

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Preserve the vendor-tuned accelerator stack from the base image.
RUN cp -r /usr/local/lib/python3.12/dist-packages/torch* /opt/venv/lib/python3.12/site-packages/ && \
    cp -r /usr/local/lib/python3.12/dist-packages/nvfuser* /opt/venv/lib/python3.12/site-packages/ || true

# The base image ships torchao, but transformers will import it and trip over
# unsupported ops for this service stack.
RUN python -m pip uninstall -y torchao || true

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --disable-pip-version-check --upgrade-strategy only-if-needed -r requirements.txt

RUN python - <<'PY'
from pathlib import Path

protected_prefixes = (
    "cuda",
    "cudnn",
    "nvidia-",
    "nvfuser",
    "pytorch-triton",
    "tensorrt",
    "torch @",
    "torch==",
    "torch===",
    "torchao",
    "torch-tensorrt",
    "torch_tensorrt",
    "torchaudio @",
    "torchaudio==",
    "torchaudio===",
    "torchtitan",
    "torchvision @",
    "torchvision==",
    "torchvision===",
    "transformer-engine",
    "triton",
    "xformers",
)

src = Path("/app/safe_requirements.txt")
dst = Path("/tmp/app_requirements.txt")
pkg_dst = Path("/tmp/f5tts_requirement.txt")
audio_dst = Path("/tmp/torchaudio_requirement.txt")
filtered_lines = []
seen = set()
f5_tts_requirement = "f5-tts==0.3.4"
torchaudio_requirement = "torchaudio==2.10.0"
numpy_pin = "numpy<=1.26.4"

for raw_line in src.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue

    lower = line.lower()
    if lower.startswith(protected_prefixes):
        continue

    if lower.startswith(("f5-tts==", "f5-tts @", "f5-tts===")):
        f5_tts_requirement = line
        continue

    if lower.startswith(("torchaudio==", "torchaudio @", "torchaudio===")):
        torchaudio_requirement = line
        continue

    if lower == "numpy" or lower.startswith(
        ("numpy==", "numpy!=", "numpy<", "numpy>", "numpy~=", "numpy @", "numpy===")
    ):
        line = numpy_pin

    if line not in seen:
        filtered_lines.append(line)
        seen.add(line)

if numpy_pin not in seen:
    filtered_lines.append(numpy_pin)

dst.write_text("\n".join(filtered_lines) + "\n", encoding="utf-8")
pkg_dst.write_text(f5_tts_requirement + "\n", encoding="utf-8")
audio_dst.write_text(torchaudio_requirement + "\n", encoding="utf-8")
PY

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --disable-pip-version-check --no-deps -r /tmp/torchaudio_requirement.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --disable-pip-version-check --no-deps -r /tmp/app_requirements.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --disable-pip-version-check --no-deps -r /tmp/f5tts_requirement.txt

# --- ARM64 SOUNDFILE OVERRIDE PATCH ---
# Injects a fallback to bypass the missing torchcodec library on ARM64
RUN cat <<EOF >> /opt/venv/lib/python3.12/site-packages/torchaudio/_torchcodec.py
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
# --------------------------------------

COPY app.py .

EXPOSE 8000
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
