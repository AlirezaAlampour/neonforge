# Installation

This guide installs the NeonForge control plane and lightweight baseline first. Optional GPU models are separate so a new checkout can launch without downloading every model.

## Prerequisites

- NVIDIA DGX Spark or a compatible NVIDIA Linux system
- ARM64 (`uname -m` should report `aarch64`) for the documented DGX path
- Docker Engine with Compose v2
- NVIDIA Container Toolkit configured for Docker
- Git
- 20 GB or more free for base images and caches
- Additional model storage under `/srv/ai/models`; Character and Avatar candidates can each require tens of GB

Confirm the basics:

```bash
uname -m
docker version
docker compose version
docker info --format '{{json .Runtimes}}'
```

## Clone and configure

```bash
git clone https://github.com/AlirezaAlampour/neonforge.git
cd neonforge
cp .env.example .env
```

Review `.env` before starting. `BIND_ADDRESS=127.0.0.1` is deliberately local-only. To use NeonForge over a LAN or Tailscale network, set this to the host's specific trusted interface address.

Create the shared persistent directories:

```bash
sudo install -d -o "$USER" -g "$USER" \
  /srv/ai/models \
  /srv/ai/cache/hf \
  /srv/ai/outputs \
  /srv/ai/assets \
  /srv/ai/logs
```

Do not place model weights inside the repository. All services reuse the shared model and Hugging Face cache mounts.

## Start the base stack

```bash
docker compose config --quiet
docker compose up -d
docker compose ps
```

The default launch includes Redis, the supervisor, gateway, frontend, Faster-Whisper, and the F5-TTS service container. Model processes still load lazily where supported.

Verify without loading heavyweight models:

```bash
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/readyz
curl --fail http://127.0.0.1:8080/services/status
curl --fail http://127.0.0.1:3000/
uv run scripts/verify_dgx.py --gateway-url http://127.0.0.1:8080 --json
```

The verifier's `--smoke` option may load model weights. It refuses to run below 40 GB `MemAvailable`, but it should still be used only when the intended model files are installed.

## Optional services

Start only the backend needed for the current workflow.

```bash
# Additional voice engines; review each model's terms first.
docker compose --profile voice-extras up -d fish_speech misotts voxcpm2

# Breeze TTS 2 (research/non-commercial model terms).
docker compose --profile breeze up -d breeze_tts

# Managed Character workflow.
docker compose --profile comfyui up -d comfyui

# Legacy media services; these remain unavailable until runtime/model checks pass.
docker compose --profile legacy up -d liveportrait lipsync
```

Wan 2.1 is profile-gated and started on demand by the supervisor when a gateway job passes the heavy-memory gate. Install its model first if offline operation is required.

The local `Wan2.2-Animate/` nested checkout is not part of the supported repository. Its cloud-backed Gradio sidecar is isolated behind the `cloud-experimental` profile and is not used by the NeonForge Character workflow.

## Model installation

See [models.md](models.md) for sources, expected paths, sizes, environment variables, license notes, and current validation state. Use the locked `uv` tool runner for Hugging Face downloads rather than installing a CLI globally:

```bash
uvx --from huggingface-hub hf download OWNER/MODEL \
  --local-dir /srv/ai/models/WORKFLOW/MODEL
```

## Development setup

Python dependencies:

```bash
uv sync --locked --dev
uv run pytest
```

Frontend dependencies:

```bash
cd frontend
npm ci --ignore-scripts
npm run build
```

## Updating and rebuilding

Rebuild only changed services:

```bash
docker compose build gateway frontend
docker compose up -d --no-deps gateway frontend
```

Before starting any model container, check shared memory:

```bash
mem_kb="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
awk -v kb="$mem_kb" 'BEGIN {printf "MemAvailable: %.1f GB\n", kb/1024/1024}'
```

Do not begin heavyweight loading below 40 GB available. See [troubleshooting.md](troubleshooting.md) for safe recovery steps.

## Optional idle manager

The host idle manager can stop supervisor-managed services after their configured inactivity timeout. The checked-in systemd template assumes the repository is `/home/USER/neonforge` and `uv` is available in `/home/USER/.local/bin`.

```bash
sudo cp systemd/ai-idle-manager@.service /etc/systemd/system/
sudo cp systemd/ai-idle-manager@.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now "ai-idle-manager@$(id -un).timer"
systemctl status "ai-idle-manager@$(id -un).timer"
```

If the checkout lives elsewhere, override `WorkingDirectory`, `COMPOSE_DIR`, `ExecStart`, and `ReadWritePaths` before enabling the unit.
