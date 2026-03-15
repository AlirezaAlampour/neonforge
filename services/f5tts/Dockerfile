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
COPY requirements.txt .

# 1. Create a clean virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 2. THE NUCLEAR OPTION: Copy the perfect NVIDIA tensor libraries
RUN cp -r /usr/local/lib/python3.12/dist-packages/torch* /opt/venv/lib/python3.12/site-packages/ && \
    cp -r /usr/local/lib/python3.12/dist-packages/nvfuser* /opt/venv/lib/python3.12/site-packages/ || true

# 3. Strip torch references
RUN sed -i '/^torch/d' requirements.txt && \
    sed -i '/^torchvision/d' requirements.txt && \
    sed -i '/^torchaudio/d' requirements.txt && \
    sed -i '/^--extra-index-url/d' requirements.txt

# 4. Install the standard requirements
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# 5. Install F5-TTS dependencies manually (REMOVED torchao, ADDED torchdiffeq)
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install transformers huggingface_hub accelerate datasets modelscope ema-pytorch x-transformers vocos rwkv cached_path pypinyin pydub jieba matplotlib torchdiffeq
    
# 6. FORCE AMPUTATION: Ensure torchao is completely removed so transformers doesn't try to load it
RUN pip uninstall -y torchao || true

# 7. Force install F5-TTS
RUN pip install --no-deps f5-tts==0.3.4

COPY app.py .
EXPOSE 8000
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]