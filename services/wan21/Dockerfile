FROM nvcr.io/nvidia/pytorch:24.10-py3 AS base

LABEL maintainer="ai-infra"
LABEL component="${SERVICE}"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install required system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ffmpeg libsndfile1 libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 git tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .

# Strip out the CPU PyTorch downloads so we keep the pre-installed NVIDIA GPU version
RUN sed -i '/^torch/d' requirements.txt && \
    sed -i '/^--extra-index-url/d' requirements.txt && \
    pip install --no-cache-dir -r requirements.txt || pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY app.py .
EXPOSE 8000
ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
