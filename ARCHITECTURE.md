# Architecture

## Overview

- `neonforge` is a single-host DGX Spark AI stack orchestrated from the root `docker-compose.yml`.
- The runtime model is Docker Compose on the internal bridge network `ai-net`, with shared host mounts for models, Hugging Face cache, outputs, and logs.
- Public entry points are the frontend on `:3000`, the gateway on `:8080`, Redis on `:6379`, and optional ComfyUI on `:8188`.
- Most backend services are internal FastAPI apps listening on port `8000`; the gateway is the main routed API surface.

## Services

| Service | Path | Dockerfile | Exposes / Talks To | Primary Dependencies |
| --- | --- | --- | --- | --- |
| Redis | Root compose only | No local Dockerfile (`redis:7.4-alpine` image) | Host `:6379`; used by gateway and host idle manager for job/activity state | Redis, named volume `redis-data` |
| Frontend | `frontend/` | `frontend/Dockerfile` | Host `:3000`; rewrites `/api/v1/*`, `/jobs/*`, `/memory`, `/services/*`, `/healthz` to `gateway:8000` | Next.js 14, React 18, Tailwind |
| Gateway | `gateway/` | `gateway/Dockerfile` | Host `:8080` to container `:8000`; proxies requests to all AI services; queries Redis; calls supervisor for lifecycle operations | FastAPI, Redis, `httpx`, `python-multipart`, shared `/outputs` |
| Supervisor | `supervisor/` | `supervisor/Dockerfile` | Internal-only `supervisor:8000`; gateway calls `/start/{service}`, `/stop/{service}`, `/status/{service}` | FastAPI, `httpx`, Docker socket, read-only mount of repo compose project |
| Whisper | `services/whisper/` | `services/whisper/Dockerfile` | Internal `whisper:8000`; gateway proxies `/api/v1/whisper/transcribe` to `/transcribe` | Faster-Whisper, FastAPI, shared model/cache/output mounts |
| F5-TTS | `services/f5tts/` | `services/f5tts/Dockerfile` | Internal `f5tts:8000`; gateway proxies `/api/v1/tts/synthesize` and `/api/v1/tts/synthesize-with-audio`; supervisor can start/stop it | FastAPI, `librosa`, `soundfile`, shared model/cache/output mounts |
| Fish Speech Adapter | `services/fish_speech/` | `services/fish_speech/Dockerfile` | Internal `fish_speech:8000`; gateway routes unified `/api/v1/tts/jobs` provider calls here when enabled | FastAPI, `httpx`, shared output mount, Fish Speech-compatible upstream runtime |
| Premium Clone TTS Adapter | `services/premium_clone_tts/` | `services/premium_clone_tts/Dockerfile` | Internal `premium_clone_tts:8000`; gateway routes unified `/api/v1/tts/jobs` provider calls here when enabled | FastAPI, `httpx`, shared output mount, scaffolded upstream contract |
| LivePortrait | `services/liveportrait/` | `services/liveportrait/Dockerfile` | Internal `liveportrait:8000`; gateway proxies `/api/v1/liveportrait/animate` to `/animate`; supervisor can start/stop it | FastAPI, PyTorch, ONNX Runtime, InsightFace, MediaPipe, shared mounts |
| Lip-sync | `services/lipsync/` | `services/lipsync/Dockerfile` | Internal `lipsync:8000`; gateway proxies `/api/v1/lipsync/sync` to `/sync`; supervisor can start/stop it | FastAPI, PyTorch, OpenCV, video-retalking stack, GFPGAN/Real-ESRGAN, shared mounts |
| Wan 2.1 | `services/wan21/` | `services/wan21/Dockerfile` | Internal `wan21:8000`; gateway proxies `/api/v1/wan21/generate` to `/generate`; lazy-start profile started by supervisor and stopped after idle | FastAPI, PyTorch, Diffusers, Transformers, Accelerate, shared mounts, heavy UMA memory budget |
| ComfyUI (optional) | `comfyUI/` | `comfyUI/Dockerfile` with build context `.` | Host `:8188`; direct browser/API access; not routed through gateway | Upstream ComfyUI checkout, custom dependency resolver, shared `/models/comfyui` and `/outputs/comfyui/*` |
| Idle Manager (host) | `scripts/idle_manager.py`, `systemd/` | Not containerized | No API; reads Redis activity keys and uses Docker CLI to stop idle containers | Python, local Redis, Docker CLI, `/proc/meminfo`, `nvidia-smi` |

## Communication

- Browser clients usually enter through `frontend:3000`, which rewrites API traffic to `gateway:8000`.
- External API clients can call the gateway directly on host port `8080`.
- The gateway talks to Redis for readiness and activity state, then proxies work to `whisper`, `f5tts`, `fish_speech`, `premium_clone_tts`, `liveportrait`, `lipsync`, and `wan21` over `ai-net`.
- TTS-specific routing now goes through a provider registry in `gateway/app.py`, which exposes provider metadata, capability flags, unified TTS job submission, and provider health endpoints.
- The gateway does not mount the Docker socket. It delegates lifecycle operations to the internal supervisor service over HTTP.
- The supervisor holds the Docker socket and runs `docker compose` to start or stop managed services (`wan21`, `f5tts`, `liveportrait`, `lipsync`).
- The host idle manager polls Redis activity timestamps and stops idle managed containers from outside Docker via the local Docker CLI.
- All GPU-backed services and ComfyUI share the same host-backed storage for models, cache, outputs, and logs.

## Shared Infrastructure

- Compose root: `docker-compose.yml`
- Internal network: `ai-net`
- Shared host mounts:
  - `/srv/ai/models -> /models`
  - `/srv/ai/cache/hf -> /cache/hf`
  - `/srv/ai/outputs -> /outputs`
  - `/srv/ai/logs -> /logs`
- Redis persistence: named volume `redis-data`
- Runtime assumptions: single NVIDIA GPU, ARM64, and UMA memory-aware admission logic in the gateway

## Notes

- `comfyUI` is optional and only starts when the `comfyui` or `full` Compose profile is enabled.
- `wan21` is also profile-gated and is designed for on-demand start through the supervisor rather than always-on startup.
- Several docs and host-side unit files still reference the older path `~/dgx-ai-stack` or `/home/xxfactionsxx/dgx-ai-stack`; the current repository path is `/home/xxfactionsxx/neonforge`.
