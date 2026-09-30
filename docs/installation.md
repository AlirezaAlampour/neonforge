# Installation

NeonForge targets NVIDIA DGX Spark/GB10 on Linux ARM64. Docker Engine, Compose v2, NVIDIA Container Toolkit, Git, uv, and persistent storage under `/srv/ai` are required.

## Base stack

```bash
git clone --branch neonforge-v0.2 https://github.com/AlirezaAlampour/neonforge.git
cd neonforge
cp .env.example .env

sudo install -d -o "$USER" -g "$USER" \
  /srv/ai/models /srv/ai/cache/hf /srv/ai/outputs /srv/ai/assets /srv/ai/logs

uv lock --check
docker compose config --quiet
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8080/readyz
curl --fail http://127.0.0.1:3000/
```

The frontend and gateway bind to loopback by default. Use one specific trusted address for `BIND_ADDRESS` when remote access is intended.

## Models and profiles

Model weights stay outside Git. See [models.md](models.md) for exact filenames, sources, sizes, hashes, and license notes.

Profiles describe installable services, but normal creator jobs should let the supervisor start heavy backends on demand:

```bash
# Optional voice engines
docker compose --profile voice build f5tts misotts voxcpm2 breeze_tts
# Fish uses a prebuilt local image; verify that image before enabling it.

# Managed HunyuanVideo and Wan Character runtime
docker compose --profile video build comfyui

# LatentSync 1.6
uv lock --check --project services/lipsync
docker compose --profile lip-sync build lipsync
```

Optional profiles are `voice`, `video`, `character` and `lip-sync`. The supervisor prepares memory and starts `comfyui` or `lipsync` when a gateway job claims it.

## Automatic memory settings

The supervisor uses `/proc/meminfo`, not `nvidia-smi`, for shared UMA. Relevant overrides:

| Variable | Default | Purpose |
| --- | ---: | --- |
| `IDLE_SCAN_INTERVAL` | 15 s | Claim/idle scan cadence |
| `MEMORY_RECLAIM_TIMEOUT` | 90 s | Wait for stopped services to return memory |
| `COMFYUI_IDLE_TIMEOUT` | 300 s | Hunyuan/Character warm window |
| `LIPSYNC_IDLE_TIMEOUT` | 300 s | LatentSync warm window |
| `F5TTS_IDLE_TIMEOUT` | 900 s | F5 container warm window |
| `FISH_SPEECH_IDLE_TIMEOUT` | 900 s | Fish warm window |
| `MISOTTS_IDLE_TIMEOUT` | 900 s | Miso warm window |
| `BREEZE_TTS_IDLE_TIMEOUT` | 900 s | Breeze warm window |
| `VOXCPM2_IDLE_TIMEOUT` | 900 s | Vox warm window |

Launch minimums are supervisor-owned policy, including 64 GiB for 17-frame Wan Character, 40–60 GiB for HunyuanVideo by resolution/frame count, and 32 GiB for LatentSync. The gateway cannot name arbitrary containers.

Inspect decisions at `/workloads/status` or System Info:

```bash
curl --fail http://127.0.0.1:8080/workloads/status | jq
```

## Development and verification

```bash
uv lock --check
uv sync --locked --dev
uv run --locked pytest

cd frontend
npm run lint
npm run build
```

Rebuild only affected services:

```bash
docker compose build supervisor gateway frontend
docker compose up -d --no-deps supervisor gateway frontend
docker compose config --quiet
```

All Python package changes must use uv and checked-in locks. Never install interactively into a running container or replace an NGC vendor-tuned PyTorch stack with generic wheels.
