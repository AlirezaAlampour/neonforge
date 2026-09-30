ARG BASE_IMAGE=nvcr.io/nvidia/pytorch:26.02-py3
FROM ${BASE_IMAGE}
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /bin/

LABEL maintainer="ai-infra"
LABEL component="latentsync-1.6"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl ffmpeg git libgl1 libglib2.0-0 libsndfile1 tini \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/bytedance/LatentSync.git /opt/LatentSync \
    && cd /opt/LatentSync \
    && git checkout a229c3948406bc2cf6eaf4873e662e70c6a04746

COPY arm64-inference.patch /tmp/arm64-inference.patch
RUN cd /opt/LatentSync && git apply --unidiff-zero /tmp/arm64-inference.patch

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv venv --system-site-packages /opt/venv \
 && uv export --frozen --no-dev --no-emit-project --no-emit-package torch --output-file /tmp/requirements.txt \
 && uv pip install --python /opt/venv/bin/python --no-deps -r /tmp/requirements.txt

COPY app.py ./app.py
WORKDIR /opt/LatentSync
RUN rm -rf checkpoints && ln -s /models/latentsync/checkpoints checkpoints

EXPOSE 8000
ENTRYPOINT ["tini", "--"]
CMD ["uvicorn", "--app-dir", "/app", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
