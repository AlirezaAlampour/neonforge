# syntax=docker/dockerfile:1
FROM nvcr.io/nvidia/pytorch:26.02-py3 AS base
COPY --from=ghcr.io/astral-sh/uv:0.6.16 /uv /uvx /bin/

LABEL maintainer="ai-infra"
LABEL component="cosyvoice3"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH="/opt/CosyVoice:/opt/CosyVoice/third_party/Matcha-TTS:${PYTHONPATH}"

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ffmpeg git tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Preserve the vendor-tuned accelerator stack from the base image.
RUN cp -r /usr/local/lib/python3.12/dist-packages/torch* /opt/venv/lib/python3.12/site-packages/ && \
    cp -r /usr/local/lib/python3.12/dist-packages/nvfuser* /opt/venv/lib/python3.12/site-packages/ || true

RUN uv pip uninstall --python /opt/venv/bin/python torchao || true
RUN uv pip install --python /opt/venv/bin/python -r requirements.txt
RUN uv pip install --python /opt/venv/bin/python --no-deps torchaudio==2.10.0
RUN uv pip install --python /opt/venv/bin/python onnxruntime==1.18.0
RUN uv pip install --python /opt/venv/bin/python setuptools==80.9.0 wheel

# Official upstream install path. This downloads code only; model weights stay local under /models.
# CosyVoice's upstream requirements include x86-only GPU runtime wheels such as
# onnxruntime-gpu==1.18.0 and TensorRT pins that are not available on this ARM64 target.
# Filter those install-time blockers while keeping the upstream package itself intact.
RUN git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git /tmp/CosyVoice && \
    python - <<'PY' && \
    uv pip install --python /opt/venv/bin/python --no-build-isolation -r /tmp/cosyvoice_requirements.txt && \
    mv /tmp/CosyVoice /opt/CosyVoice
from pathlib import Path

src = Path("/tmp/CosyVoice/requirements.txt")
dst = Path("/tmp/cosyvoice_requirements.txt")
skip_prefixes = (
    "--extra-index-url",
    "deepspeed==",
    "fastapi==",
    "fastapi-cli==",
    "gradio==",
    "grpcio==",
    "grpcio-tools==",
    "onnxruntime-gpu==",
    "onnxruntime==",
    "tensorrt-cu12==",
    "tensorrt-cu12-bindings==",
    "tensorrt-cu12-libs==",
    "torch==",
    "torchaudio==",
    "uvicorn==",
)

filtered = []
for raw_line in src.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line:
        continue
    if line.startswith(skip_prefixes):
        continue
    filtered.append(line)

dst.write_text("\n".join(filtered) + "\n", encoding="utf-8")
PY

COPY app.py .

EXPOSE 8000
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
