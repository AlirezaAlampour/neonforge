# syntax=docker/dockerfile:1
FROM nvcr.io/nvidia/pytorch:26.02-py3 AS base
COPY --from=ghcr.io/astral-sh/uv:0.6.16 /uv /uvx /bin/

LABEL maintainer="ai-infra"
LABEL component="qwen3tts"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

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

# Official upstream package install. This downloads code only; model weights stay local under /models.
RUN uv pip install --python /opt/venv/bin/python qwen-tts
RUN uv pip install --python /opt/venv/bin/python --no-deps torchaudio==2.10.0

COPY app.py .

EXPOSE 8000
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
