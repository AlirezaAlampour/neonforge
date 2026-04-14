# Architecture

## Overview

- `neonforge` is a single-host DGX Spark AI stack orchestrated from the root `docker-compose.yml`.
- The runtime model is Docker Compose on the internal bridge network `ai-net`, with shared host mounts for models, Hugging Face cache, outputs, and logs.
- The gateway includes an isolated `gateway/voiceover/` subsystem for reusable voice profiles, long-form narration jobs, and recent voiceover output management.
- Public entry points are the frontend on `:3000`, the gateway on `:8080`, Redis on `:6379`, and optional ComfyUI on `:8188`.
- Most backend services are internal FastAPI apps listening on port `8000`; the gateway is the main routed API surface.

## Services

| Service | Path | Dockerfile | Exposes / Talks To | Primary Dependencies |
| --- | --- | --- | --- | --- |
| Redis | Root compose only | No local Dockerfile (`redis:7.4-alpine` image) | Host `:6379`; used by gateway and host idle manager for job/activity state | Redis, named volume `redis-data` |
| Frontend | `frontend/` | `frontend/Dockerfile` | Host `:3000`; rewrites `/api/v1/*`, `/jobs/*`, `/memory`, `/services/*`, `/healthz` to `gateway:8000` | Next.js 14, React 18, Tailwind |
| Gateway | `gateway/` | `gateway/Dockerfile` | Host `:8080` to container `:8000`; proxies requests to AI services, owns voiceover routes, queries Redis, and calls supervisor for lifecycle operations | FastAPI, Redis, `httpx`, `python-multipart`, `ffmpeg`, shared `/outputs` and `/app/data/assets` |
| Supervisor | `supervisor/` | `supervisor/Dockerfile` | Internal-only `supervisor:8000`; gateway calls `/start/{service}`, `/stop/{service}`, `/status/{service}` | FastAPI, `httpx`, Docker socket, read-only mount of repo compose project |
| Whisper | `services/whisper/` | `services/whisper/Dockerfile` | Internal `whisper:8000`; gateway proxies `/api/v1/whisper/transcribe` to `/transcribe` | Faster-Whisper, FastAPI, shared model/cache/output mounts |
| F5-TTS | `services/f5tts/` | `services/f5tts/Dockerfile` | Internal `f5tts:8000`; gateway proxies direct TTS routes and Voiceover Studio can call it as a long-form backend | FastAPI, `librosa`, `soundfile`, shared model/cache/output mounts |
| Fish Speech | Compose image only (`dgx-ai-stack-fish_speech`) | No local Dockerfile in this repo | Internal `fish_speech:8000`; Voiceover Studio can call it when `FISH_SPEECH_ENABLED=true` | External/prebuilt image, shared model/cache/output mounts, Whisper-assisted reference transcription |
| VoxCPM2 | `services/voxcpm2/` | `services/voxcpm2/Dockerfile` | Internal `voxcpm2:8000`; Voiceover Studio can call it when `VOXCPM2_ENABLED=true` | FastAPI, PyTorch, shared model/cache/output mounts |
| LivePortrait | `services/liveportrait/` | `services/liveportrait/Dockerfile` | Internal `liveportrait:8000`; gateway proxies `/api/v1/liveportrait/animate` to `/animate`; supervisor can start/stop it | FastAPI, PyTorch, ONNX Runtime, InsightFace, MediaPipe, shared mounts |
| Lip-sync | `services/lipsync/` | `services/lipsync/Dockerfile` | Internal `lipsync:8000`; gateway proxies `/api/v1/lipsync/sync` to `/sync`; supervisor can start/stop it | FastAPI, PyTorch, OpenCV, video-retalking stack, GFPGAN/Real-ESRGAN, shared mounts |
| Wan 2.1 | `services/wan21/` | `services/wan21/Dockerfile` | Internal `wan21:8000`; gateway proxies `/api/v1/wan21/generate` to `/generate`; lazy-start profile started by supervisor and stopped after idle | FastAPI, PyTorch, Diffusers, Transformers, Accelerate, shared mounts, heavy UMA memory budget |
| ComfyUI (optional) | `comfyUI/` | `comfyUI/Dockerfile` with build context `.` | Host `:8188`; direct browser/API access; not routed through gateway | Upstream ComfyUI checkout, custom dependency resolver, shared `/models/comfyui` and `/outputs/comfyui/*` |
| Wan UI (optional) | `Wan2.2-Animate/` | `Wan2.2-Animate/Dockerfile` | Host `:7860`; separate Gradio UI for Wan experimentation | Custom Wan 2.2 UI stack, shared HF cache and ComfyUI model mount |
| Idle Manager (host) | `scripts/idle_manager.py`, `systemd/` | Not containerized | No API; reads Redis activity keys and uses Docker CLI to stop idle containers | Python, local Redis, Docker CLI, `/proc/meminfo`, `nvidia-smi` |

## Communication

- Browser clients usually enter through `frontend:3000`, which rewrites API traffic to `gateway:8000`.
- External API clients can call the gateway directly on host port `8080`.
- The gateway talks to Redis for readiness and activity state, then proxies work to `whisper`, `f5tts`, `fish_speech`, `voxcpm2`, `liveportrait`, `lipsync`, and `wan21` over `ai-net`.
- Voiceover Studio routes live inside the gateway and persist reusable profile assets under `/srv/ai/assets/voice_profiles`.
- New voice-profile uploads accept WAV, MP3, and M4A, then normalize to a PCM WAV master on ingest before downstream model use.
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
  - `/srv/ai/assets -> /app/data/assets`
  - `/srv/ai/logs -> /logs`
- Redis persistence: named volume `redis-data`
- Runtime assumptions: single NVIDIA GPU, ARM64, and UMA memory-aware admission logic in the gateway

## Notes

- `comfyUI` is optional and only starts when the `comfyui` or `full` Compose profile is enabled.
- `wan21` is also profile-gated and is designed for on-demand start through the supervisor rather than always-on startup.
- Several docs and host-side unit files still reference the older path `~/dgx-ai-stack` or `/home/xxfactionsxx/dgx-ai-stack`; the current repository path is `/home/xxfactionsxx/neonforge`.
