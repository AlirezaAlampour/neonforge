#!/usr/bin/env bash
set -euo pipefail

export PYTHONUNBUFFERED=1

python /app/bootstrap_model.py

exec python -m tools.api_server \
  --listen 0.0.0.0:8000 \
  --llama-checkpoint-path "${FISH_SPEECH_MODEL_PATH:-/models/fish_speech/s2-pro}" \
  --decoder-checkpoint-path "${FISH_SPEECH_DECODER_PATH:-/models/fish_speech/s2-pro/codec.pth}" \
  --decoder-config-name "${FISH_SPEECH_DECODER_CONFIG_NAME:-modded_dac_vq}"
