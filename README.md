# DGX Spark AI Stack

A production-grade local AI video pipeline running on a single NVIDIA DGX Spark (GB10, 128 GB UMA, ARM64, CUDA 13.0).

## What This Is

This stack runs five AI inference services behind a single API gateway, with job queuing, memory-aware scheduling, and automatic idle management. Everything runs in Docker containers on one machine with one GPU.

**Services:**

| Service | Purpose | Tier | GPU Weight |
|---------|---------|------|------------|
| **Faster-Whisper** | Audio transcription (STT) | Always-on | Light (~1-2 GB) |
| **F5-TTS** | Text-to-speech synthesis | Warm | Medium (~2-3 GB) |
| **LivePortrait** | Face animation from image + driving video | Warm | Medium (~2-4 GB) |
| **Lip-sync** | Sync lip movements to audio (video-retalking) | Warm | Medium (~3-5 GB) |
| **Wan 2.1** | Text-to-video generation | Lazy-start singleton | Heavy (~8-40 GB) |

**Infrastructure:**

| Component | Purpose |
|-----------|---------|
| **Gateway** | Public API entry point, job queue, memory gating, request routing |
| **Supervisor** | Internal sidecar with Docker socket, starts/stops lazy containers |
| **Redis** | Job state, activity timestamps, service coordination |
| **Idle Manager** | Host-side systemd service, stops idle containers, thermal monitoring |

## Architecture

```
                    Internet / LAN
                         |
                    :8080 (Gateway)
                         |
              +----------+----------+
              |     API Gateway     |  <-- job queue, memory gate, semaphores
              |   (no Docker sock)  |      UMA hard limit: 80%
              +----------+----------+      medium concurrency: 1
                         |                 heavy concurrency: 1
          +--------------+--------------+
          |              |              |
    +-----+----+  +------+-----+  +----+-----+
    |  Redis   |  | Supervisor |  |  Whisper  |
    | (state)  |  | (Docker    |  | (always   |
    |          |  |  socket)   |  |  on)      |
    +----------+  +------+-----+  +----------+
                         |
              +----------+----------+
              |          |          |
         +----+---+ +----+---+ +---+----+
         | F5-TTS | |  Live  | |  Lip   |
         | (warm) | |Portrait| | -sync  |
         |        | | (warm) | | (warm) |
         +--------+ +--------+ +--------+

         +----------------------------------+
         |        Wan 2.1 (lazy)            |
         |  Started on demand by Supervisor |
         |  Stopped after 300s idle         |
         +----------------------------------+
```

### Service Tiers

**Always-on** -- Container and model always running. Instant response.
- Redis, Gateway, Supervisor, Whisper

**Warm** -- Container always running, model loaded on first request, destroyed after idle timeout (default 30 min). Healthz responds immediately; first inference triggers model load.
- F5-TTS, LivePortrait, Lip-sync

**Lazy-start singleton** -- Container stopped by default. Gateway asks Supervisor to start it on demand. Stopped by idle_manager after 5 min of inactivity. Only one instance ever runs.
- Wan 2.1

### Key Design Decisions

1. **True model destruction.** When a model goes idle, the service doesn't just call `torch.cuda.empty_cache()`. It moves sub-modules to CPU, deletes the Python object, runs `gc.collect()` twice, clears the CUDA allocator, and logs the actual GB freed via `/proc/meminfo`. This is essential on UMA where "GPU memory" and "system memory" are the same pool.

2. **Supervisor sidecar.** The public-facing gateway has zero Docker socket access. Container lifecycle (start/stop Wan 2.1) is delegated to an internal-only supervisor that holds the socket. This limits the blast radius of a gateway compromise.

3. **UMA-aware memory gating.** The gateway checks `/proc/meminfo` before admitting GPU jobs. Default hard limit: 80% used. Per-tier reservations ensure minimum available GB (heavy=40 GB, medium=10 GB, light=2 GB). `nvidia-smi` memory reporting is "Not Supported" on DGX Spark UMA.

4. **GPU concurrency = 1 for all tiers.** Single GPU, single device. Even medium-weight models serialize to prevent CUDA context contention and OOM on shared UMA.

5. **video-retalking over MuseTalk.** See [COMPATIBILITY_NOTES.md](COMPATIBILITY_NOTES.md) for the full comparison. TL;DR: video-retalking has proven quality, subprocess isolation, and lower memory. MuseTalk has no ARM64 support and unstable API.

## Quick Start

```bash
# 1. Clone and configure
cd ~/dgx-ai-stack
cp .env.example .env
# Edit .env if needed (defaults are conservative and production-safe)

# 2. Create host directories
sudo mkdir -p /srv/ai/{models,cache/hf,outputs,logs}
sudo chown -R $USER:$USER /srv/ai

# 3. Start always-on tier
docker compose up -d redis supervisor gateway whisper

# 4. Start warm tier
docker compose up -d f5tts liveportrait lipsync

# 5. Verify
python3 scripts/verify_dgx.py --smoke

# 6. (Optional) Install idle manager systemd units
sudo cp systemd/ai-idle-manager.service /etc/systemd/system/
sudo cp systemd/ai-idle-manager.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ai-idle-manager.timer
```

Wan 2.1 starts automatically on first request via the gateway:
```bash
curl -X POST http://localhost:8080/api/v1/wan21/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "a cat walking in a garden", "num_frames": 16, "width": 512, "height": 512}'
```

## API Reference

All requests go through `http://localhost:8080`.

| Method | Endpoint | Service | Tier |
|--------|----------|---------|------|
| POST | `/api/v1/whisper/transcribe` | Whisper | Light |
| POST | `/api/v1/tts/synthesize` | F5-TTS | Medium |
| POST | `/api/v1/liveportrait/animate` | LivePortrait | Medium |
| POST | `/api/v1/lipsync/sync` | Lip-sync | Medium |
| POST | `/api/v1/wan21/generate` | Wan 2.1 | Heavy |
| GET | `/api/v1/comfyui/templates` | Managed ComfyUI templates | Heavy |
| GET | `/api/v1/comfyui/templates/{template_id}` | Template detail + validation | Heavy |
| GET | `/api/v1/comfyui/models` | Read-only ComfyUI model inventory | -- |
| GET | `/api/v1/comfyui/assets` | Uploaded Character Swap assets | -- |
| POST | `/api/v1/comfyui/assets/upload` | Upload Character Swap asset | -- |
| DELETE | `/api/v1/comfyui/assets/{asset_id}` | Delete uploaded Character Swap asset | -- |
| POST | `/api/v1/comfyui/jobs` | Queue template-driven ComfyUI job | Heavy |
| GET | `/api/v1/comfyui/jobs/{job_id}` | Template-driven ComfyUI job detail | Heavy |
| GET | `/api/v1/outputs/{path}` | File retrieval | -- |
| GET | `/healthz` | Gateway health | -- |
| GET | `/readyz` | Gateway + Redis readiness | -- |
| GET | `/memory` | UMA memory status + thresholds | -- |
| GET | `/services/status` | All service health | -- |
| GET | `/jobs/{job_id}` | Job status lookup | -- |

## ComfyUI Templates

NeonForge now includes a managed Character Swap flow in the Studio UI that runs ComfyUI workflows through the gateway instead of treating ComfyUI as an untracked sidecar.

### Where files live

- Template manifests and workflow source files live in `gateway/templates/comfyui/`.
- Uploaded Character Swap assets live under `${ASSETS_DIR}/comfyui/uploads/` and are mounted in the gateway at `/app/data/assets/comfyui/uploads/`.
- Before submission, selected uploaded assets are staged into the ComfyUI input directory at `/outputs/comfyui/input/`.
- Final videos are written by ComfyUI to `/outputs/comfyui/output/` and are exposed through the existing history/download APIs.

### Template Workflow JSON vs Runtime Payload

- The template workflow JSON is the source-of-truth graph stored in the repo.
- The runtime payload sent to ComfyUI is always an API prompt graph under `payload.prompt`.
- For full exported workflows, NeonForge converts the stored workflow to API format through ComfyUI's `/workflow/convert` endpoint at submission time, then patches only the manifest-defined runtime inputs.
- The first managed template patches:
  - `reference_image -> node 57 -> inputs.image`
  - `driving_video -> node 63 -> inputs.video`
  - `seed -> node 27 -> inputs.seed`
  - `steps -> node 27 -> inputs.steps`
  - `cfg -> node 27 -> inputs.cfg`
  - `denoise_strength -> node 27 -> inputs.denoise_strength`

### Model Validation

- NeonForge scans only the ComfyUI model roots from `COMFYUI_MODEL_ROOTS` (default: `/models/comfyui,/opt/ComfyUI/custom_nodes/comfyui_controlnet_aux/ckpts`).
- `COMFYUI_MODEL_ROOTS` accepts multiple roots separated by commas, newlines, or semicolons.
- In Docker Compose, the gateway mounts `/models` read-only for shared weights, and it falls back to a read-only supervisor-backed scan for ComfyUI container-only roots such as `comfyui_controlnet_aux/ckpts`.
- Validation is read-only. NeonForge never downloads models, never mutates the ComfyUI models directory, and never tries to "fix" missing files.
- If a template references a model that is not present, submission is rejected with a clear validation error before the workflow is queued.
- The validator reports which root satisfied each required model and includes the scanned roots in `/api/v1/comfyui/models` for debugging.
- The Wan Character Swap template validates the workflow's referenced Wan checkpoint, VAE, CLIP vision, text encoder, LoRAs, SAM2 weights, and preprocess ONNX/TorchScript files by filename.

### Adding a New Template

1. Add a manifest file to `gateway/templates/comfyui/<template-id>.manifest.json`.
2. Add the workflow source JSON to `gateway/templates/comfyui/` and point `workflow_file` at it.
3. Define `required_inputs`, `optional_params`, the `output` node expectation, and the `runtime_mappings` that patch node inputs at runtime.
4. Make sure every referenced model is already present in the mounted ComfyUI model roots, then refresh the Character Swap tab or call `GET /api/v1/comfyui/templates` to see validation results.

### Per-Service Health Endpoints (internal, port 8000)

Each service exposes:
- `GET /healthz` -- process alive (no model check)
- `GET /readyz` -- model loaded status + UMA usage
- `GET /smoke` -- lightweight deterministic test (may trigger model load)

## Directory Layout

```
dgx-ai-stack/
  docker-compose.yml          # All services, volumes, networks
  .env.example                # Configuration template
  ACCEPTANCE_TESTS.md         # Step-by-step validation + benchmark matrix
  COMPATIBILITY_NOTES.md      # ARM64 / CUDA / DGX Spark risks per service
  README.md                   # This file
  gateway/
    app.py                    # FastAPI gateway, job queue, memory gate
    Dockerfile
    requirements.txt
  supervisor/
    app.py                    # Internal sidecar, Docker socket holder
    Dockerfile
    requirements.txt
  services/
    whisper/
      app.py                  # Faster-whisper, always-on
      Dockerfile
      requirements.txt
    f5tts/
      app.py                  # F5-TTS, warm with true model destroy
      Dockerfile
      requirements.txt
    liveportrait/
      app.py                  # LivePortrait, warm with true model destroy
      Dockerfile
      requirements.txt
    lipsync/
      app.py                  # video-retalking/SadTalker, warm
      Dockerfile
      requirements.txt
    wan21/
      app.py                  # Wan 2.1, lazy singleton with true destroy
      Dockerfile
      requirements.txt
  scripts/
    verify_dgx.py             # Health check, smoke tests, latency percentiles
    idle_manager.py            # Systemd-driven idle container stopper
  systemd/
    ai-idle-manager.service    # Systemd unit for idle manager
    ai-idle-manager.timer      # 60-second timer for idle checks
```

### Host Bind Mounts

| Host Path | Container Mount | Purpose |
|-----------|----------------|---------|
| `/srv/ai/models` | `/models` | Model weights (persistent) |
| `/srv/ai/cache/hf` | `/cache/hf` | HuggingFace download cache |
| `/srv/ai/outputs` | `/outputs` | Generated files (audio, video) |
| `/srv/ai/logs` | `/logs` | Service logs |

## Configuration

All configuration is via `.env`. Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMORY_HARD_LIMIT` | `80` | UMA % usage that blocks new GPU jobs |
| `MEM_RESERVE_HEAVY_GB` | `40` | Min GB free to admit heavy jobs (Wan) |
| `MEM_RESERVE_MEDIUM_GB` | `10` | Min GB free to admit medium jobs |
| `MEM_RESERVE_LIGHT_GB` | `2` | Min GB free to admit light jobs |
| `WAN21_MODEL_VARIANT` | `1.3B` | Wan model size (1.3B safe, 14B risky) |
| `WAN21_IDLE_TIMEOUT` | `300` | Seconds before idle Wan container stops |
| `F5TTS_IDLE_TIMEOUT` | `1800` | Seconds before F5-TTS model is destroyed |
| `LIPSYNC_BACKEND` | `video-retalking` | Lip-sync backend (or `sadtalker`) |

## Hardware Target

- **NVIDIA DGX Spark** -- GB10 GPU, Blackwell architecture (sm_121)
- **128 GB UMA** -- CPU and GPU share one memory pool
- **ARM64 (aarch64)** -- NVIDIA Grace CPU
- **CUDA 13.0** -- Driver 580.126.09
- **Single GPU** -- All concurrency is serialized

Key UMA implications:
- `nvidia-smi` reports memory as "Not Supported"
- All memory monitoring uses `/proc/meminfo` (MemAvailable, SwapFree)
- Docker `--memory` limits don't cap GPU tensor allocations
- OOM is system-level (Linux OOM killer), not CUDA-level

## Validation

Run the verification script:
```bash
python3 scripts/verify_dgx.py --gateway-url http://localhost:8080 --smoke --json
```

This reports:
- Service health (/healthz, /readyz)
- Smoke test results with latency percentiles (p50/p95/p99)
- Memory gate status and thresholds
- Host memory: MemAvailable, SwapFree, Buffers/Cache
- Per-container RSS via `docker stats`
- Per-process GPU memory via `nvidia-smi pmon`
- GPU temperature, utilization, power, clocks

See [ACCEPTANCE_TESTS.md](ACCEPTANCE_TESTS.md) for full step-by-step validation including the benchmark matrix.

## Known Risks

See [COMPATIBILITY_NOTES.md](COMPATIBILITY_NOTES.md) for the full assessment. Top risks:

1. **PyTorch sm_121 kernels** -- pip wheels lack Blackwell support. Use NGC containers or JIT compile.
2. **CTranslate2 ARM64** -- May need source build for GPU acceleration.
3. **InsightFace / MediaPipe ARM64** -- Limited or missing ARM64 support for LivePortrait dependencies.
4. **video-retalking C++ deps** -- basicsr, gfpgan, facexlib need source builds on ARM64.
5. **Wan 14B memory** -- 40-80 GB peak on 128 GB UMA is risky. Default to 1.3B.
6. **UMA OOM** -- System-level, not CUDA-level. The Linux OOM killer may terminate unrelated processes.

## For LLM Agents / AI Assistants

If you are an LLM reading this codebase:

- **Architecture**: Microservices in Docker, one container per AI model. Gateway routes and queues. Supervisor manages container lifecycle.
- **State**: Redis stores job records and per-service activity timestamps. Models are stateless (weights loaded from `/models`, outputs written to `/outputs`).
- **Memory model**: UMA (shared CPU+GPU). All memory checks use `/proc/meminfo`, never `nvidia-smi`. The gateway gates job admission at 80% UMA usage.
- **Concurrency**: Single GPU. `gpu_heavy_sem=1`, `gpu_medium_sem=1`. Jobs within a tier serialize.
- **Model lifecycle**: Warm services load on first request, truly destroy (not just cache-flush) after idle timeout. Lazy services (Wan) have their entire container stopped.
- **Testing**: Run `python3 scripts/verify_dgx.py --smoke --json` for a machine-readable health report.
- **Key files**: `docker-compose.yml` for topology, `gateway/app.py` for routing/scheduling, `supervisor/app.py` for container lifecycle, `.env.example` for all config knobs.

## License

Private project. Not licensed for redistribution.
