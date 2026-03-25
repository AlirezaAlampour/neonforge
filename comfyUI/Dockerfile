# syntax=docker/dockerfile:1.7
FROM nvcr.io/nvidia/pytorch:26.02-py3 AS base

LABEL maintainer="ai-infra"
LABEL component="comfyui"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/cache/hf \
    TRANSFORMERS_CACHE=/cache/hf

ARG COMFYUI_REF=0904cc3fe5a551e3716851f12a568e481badd301
ARG WANVIDEO_REF=df8f3e49daaad117cf3090cc916c83f3d001494c
ARG F5TTS_NODE_REF=6478664f834bb43b0d63e85f8fd0bb46eea62ca1
ARG WORKFLOW_CONVERTER_REF=bc8538278f82053b3ca10a44d62d02596f8e1a37
ARG TORCHAUDIO_VERSION=2.10.0
ARG OPENCV_HEADLESS_VERSION=4.13.0.92

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ffmpeg \
        git \
        libgit2-dev \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libsndfile1 \
        libxext6 \
        libxrender1 \
        pkg-config \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt

RUN git clone https://github.com/comfyanonymous/ComfyUI.git /opt/ComfyUI \
    && git -C /opt/ComfyUI checkout "${COMFYUI_REF}" \
    && git clone https://github.com/kijai/ComfyUI-WanVideoWrapper.git /opt/ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper \
    && git -C /opt/ComfyUI/custom_nodes/ComfyUI-WanVideoWrapper checkout "${WANVIDEO_REF}" \
    && git clone https://github.com/niknah/ComfyUI-F5-TTS.git /opt/ComfyUI/custom_nodes/ComfyUI-F5-TTS \
    && git -C /opt/ComfyUI/custom_nodes/ComfyUI-F5-TTS checkout "${F5TTS_NODE_REF}" \
    && git clone https://github.com/SethRobinson/comfyui-workflow-to-api-converter-endpoint.git /opt/ComfyUI/custom_nodes/comfyui-workflow-to-api-converter-endpoint \
    && git -C /opt/ComfyUI/custom_nodes/comfyui-workflow-to-api-converter-endpoint checkout "${WORKFLOW_CONVERTER_REF}" \
    && git -C /opt/ComfyUI/custom_nodes/ComfyUI-F5-TTS submodule update --init --recursive \
    && git clone https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git /opt/ComfyUI/custom_nodes/ComfyUI-VideoHelperSuite \
    && git clone https://github.com/kijai/ComfyUI-KJNodes.git /opt/ComfyUI/custom_nodes/ComfyUI-KJNodes \
    && git clone https://github.com/Fannovel16/comfyui_controlnet_aux.git /opt/ComfyUI/custom_nodes/comfyui_controlnet_aux \
    && git clone https://github.com/kijai/ComfyUI-segment-anything-2.git /opt/ComfyUI/custom_nodes/ComfyUI-segment-anything-2

COPY comfyUI/resolve_deps.py /opt/dgx-auto-resolver/resolve_deps.py

WORKDIR /opt/ComfyUI

RUN python - <<'PY'
import re
from pathlib import Path

protected = {"torch", "torchaudio", "torchvision"}
skip_prefixes = ("--extra-index-url", "--index-url", "-f ", "--find-links")

src = Path("requirements.txt")
dst = Path("/tmp/comfyui_requirements.txt")
lines = []

for raw_line in src.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or line.startswith(skip_prefixes):
        continue
    match = re.match(r"([A-Za-z0-9_.-]+)", line)
    if match and match.group(1).lower() in protected:
        continue
    lines.append(line)

dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade-strategy only-if-needed -r /tmp/comfyui_requirements.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --no-deps "torchaudio==${TORCHAUDIO_VERSION}"

RUN python -m pip freeze --exclude-editable > /tmp/base_requirements.txt

COPY comfyUI/safe_requirements.txt /tmp/safe_requirements.txt

RUN python - <<'PY'
import re
from pathlib import Path

protected = {"torch", "torchaudio", "torchvision"}
no_deps = {"encodec", "vocos"}
skip_prefixes = ("--extra-index-url", "--index-url", "-f ", "--find-links")
opencv_requirement = "opencv-python-headless==4.13.0.92"
onnxruntime_requirement = "onnxruntime==1.24.3"

base_lines = {
    line.strip()
    for line in Path("/tmp/base_requirements.txt").read_text(encoding="utf-8").splitlines()
    if line.strip()
}

app_lines = []
nodeps_lines = []
seen = set()

for raw_line in Path("/tmp/safe_requirements.txt").read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or line.startswith(skip_prefixes):
        continue

    match = re.match(r"([A-Za-z0-9_.-]+)", line)
    package = match.group(1).lower() if match else ""
    if package in protected or line in base_lines or line in seen:
        continue

    seen.add(line)
    if package in no_deps:
        nodeps_lines.append(line)
    else:
        app_lines.append(line)

if not any(line.lower().startswith("opencv-python") for line in app_lines):
    app_lines.append(opencv_requirement)
if not any(line.lower().startswith("onnxruntime") for line in app_lines):
    app_lines.append(onnxruntime_requirement)

Path("/tmp/app_requirements.txt").write_text(
    "\n".join(app_lines) + "\n",
    encoding="utf-8",
)
Path("/tmp/app_requirements_nodeps.txt").write_text(
    "\n".join(nodeps_lines) + ("\n" if nodeps_lines else ""),
    encoding="utf-8",
)
PY

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --upgrade-strategy only-if-needed -r /tmp/app_requirements.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    python - <<'PY'
from pathlib import Path
import subprocess
import sys

for requirement in Path("/tmp/app_requirements_nodeps.txt").read_text(encoding="utf-8").splitlines():
    requirement = requirement.strip()
    if not requirement:
        continue
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            requirement,
        ],
        check=True,
    )
PY

RUN mkdir -p \
        /models/comfyui \
        /outputs/comfyui/input \
        /outputs/comfyui/output \
        /outputs/comfyui/user \
        /opt/ComfyUI/input \
        /opt/ComfyUI/output \
        /opt/ComfyUI/user \
    && rm -rf /opt/ComfyUI/models \
    && ln -s /models/comfyui /opt/ComfyUI/models

RUN pip install -U --pre comfyui-manager \
    && pip install -r /opt/ComfyUI/custom_nodes/comfyui_controlnet_aux/requirements.txt

EXPOSE 8188

ENTRYPOINT ["tini", "--"]
CMD ["python", "main.py", "--listen", "0.0.0.0", "--port", "8188", "--input-directory", "/outputs/comfyui/input", "--output-directory", "/outputs/comfyui/output", "--user-directory", "/outputs/comfyui/user", "--enable-manager"]
