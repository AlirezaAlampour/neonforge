# Current Audited State

Audit date: 2026-09-28. This document records observed runtime evidence from the current DGX Spark; it does not infer readiness from stale documentation or HTTP liveness alone.

## Decision summary

| Workflow | Decision | Observed state |
| --- | --- | --- |
| Voiceover | Keep and protect as the reference workflow | F5, Fish, Miso, Breeze, and Vox were exposed by the gateway; Breeze was loaded and ready. Existing Voiceover behavior is unchanged. |
| Lip Sync | Keep current backend as Legacy; do not add LatentSync yet | Container process was alive, but `/opt/video-retalking/inference.py` and its checkpoint directory were absent. It previously reported false readiness. |
| Avatar | Add an honest product destination; do not integrate LongCat yet | LongCat 1.5 was not installed or runtime-tested. The host did not have enough safe shared-memory headroom. |
| Character | Keep and focus the existing Wan2.2 Animate replacement path | Managed template and most weights were present. Two pose-preprocessor files were missing and the Compose-managed ComfyUI service was stopped. |
| Video Generation | Keep Wan 2.1; no LTX work in this pass | Service is lazy/profile-gated; no explicit local checkpoint was present and no render was attempted. |
| LivePortrait | Preserve service code as Legacy/experimental | Source checkout existed, but adapter import and model-path checks failed. |

## Runtime inventory

At audit time, the NeonForge gateway, frontend, Redis, supervisor, Whisper, F5-TTS, Fish Speech, MisoTTS, Breeze TTS, VoxCPM2, LivePortrait, Lip Sync, and a separate Wan cloud UI container were running. The Compose-managed `ai-comfyui` and `ai-wan21` containers were stopped. A different `content-factory-comfyui` container was running, but it is not the configured NeonForge Character backend and was not treated as interchangeable.

Gateway `/healthz` and `/readyz` passed. Whisper was ready. The voice model catalog reported F5, Fish, VoxCPM2, MisoTTS, and Breeze available; Breeze reported its model loaded. Bounded container logs showed health/readiness polling, not successful media inference, so they were not used as generation evidence.

## Integration audit

### Frontend

Before this pass, primary navigation exposed System Status, Creative Studio, Voice Studio, Voiceover Studio, B-Roll Studio, and Lip Sync Studio. The old model-oriented surfaces duplicated creator goals. Navigation is now Voiceover, Video Generation, Character, Avatar, Lip Sync, and Utilities & Status. The older F5, LivePortrait, and ReActor tools remain available inside Character under a collapsed Legacy/experimental section.

The core interaction pattern remains local and direct: select inputs, adjust a small settings surface, generate, monitor the tracked job, and preview/download the result. No generalized workflow engine was introduced.

### Gateway

The gateway already owned Redis-backed jobs, upload/history handling, service proxying, memory admission, supervisor calls, Voiceover routes, and managed ComfyUI template patching. Those boundaries were retained.

This pass normalizes service status to creator-facing `ready`, `loading`, `disabled`, `missing_model`, `runtime_error`, and `in_use` states. Lip Sync and LivePortrait proxy routes now reject unavailable backends before accepting/reading uploads and include the backend's preflight detail.

### Docker and lifecycle

Shared paths remain `/srv/ai/models`, `/srv/ai/cache/hf`, `/srv/ai/outputs`, `/srv/ai/assets`, and `/srv/ai/logs`. The gateway still has no Docker socket; the internal supervisor retains lifecycle ownership.

Optional services are separated into Compose profiles:

- `voice-extras`: Fish Speech, MisoTTS, VoxCPM2
- `breeze`: Breeze TTS 2
- `legacy`: LivePortrait and the older Lip Sync backend
- `comfyui`: managed Character runtime
- `wan21`: on-demand video runtime
- `cloud-experimental`: unrelated cloud-backed Wan Gradio checkout

Public UI/API/ComfyUI bindings now default to `127.0.0.1` and can be explicitly changed with `BIND_ADDRESS`.

### Lip Sync

The current service is a subprocess adapter for `video-retalking` with a SadTalker branch. The audited image did not contain `/opt/video-retalking/inference.py`, and `/srv/ai/models/lipsync/video-retalking/checkpoints` did not exist. Previously the endpoint reported Ready after assigning a placeholder dictionary; it did not prove a usable model.

The service now performs a runtime-and-checkpoint preflight and returns a non-ready state/503 with exact missing requirements. Existing endpoints and backend selection are preserved. The frontend labels it Legacy and disables Generate when preflight fails.

LatentSync 1.6 was evaluated but not integrated. Its official 512-pixel model is roughly 9.64 GB and documents an 18 GB inference minimum, while its environment includes CUDA/PyTorch pins and several native packages that still require ARM64/GB10 validation. The current machine had only 16.7 GB `MemAvailable`, below NeonForge's 40 GB safety floor, so no credible end-to-end test could be performed.

### Avatar and LivePortrait

LongCat-Video-Avatar 1.5 was evaluated as the preferred single future Avatar backend. Its upstream checkpoint repository is roughly 74.9 GB and its published Python/PyTorch/FlashAttention stack has not been validated on this ARM64/GB10 host. It is not installed, no service/API has been fabricated, and the new Avatar page clearly reports Disabled.

LivePortrait remains in the repository and `legacy` profile. `/opt/LivePortrait` existed, but its current source tree provides `src/live_portrait_pipeline.py`, not the `liveportrait.api` module expected by the adapter. `/srv/ai/models/liveportrait` was also absent. Its readiness endpoint now exposes these blockers rather than claiming availability.

### Character and ComfyUI

The managed template is `gateway/templates/comfyui/wan-character-swap.workflow.json`, described by its adjacent manifest. It already supports reference-image/driving-video asset mapping, read-only model validation, parameter patching, corrected output-node wiring, and opt-in patched-graph/debug artifact output. That infrastructure was retained.

The installed ComfyUI model root contained the principal Wan2.2 Animate, Wan image-to-video, VAE, text/vision encoder, SAM2, and LightX2V LoRA assets. Validation reported these missing files:

- `yolox_l.torchscript.pt`
- `dw-ll_ucoco_384_bs5.torchscript.pt`

The Compose-managed `ai-comfyui` service was stopped. Therefore Character was not generation-tested and is correctly described as blocked, not stable.

### Video Generation

The current Wan 2.1 FastAPI service remains the default video family. It is singleton, loads lazily, has a 1.3B default, uses attention/VAE slicing, and tears its pipeline down after idle. The expected explicit paths are `/srv/ai/models/wan21/1.3B` or `/srv/ai/models/wan21/14B`; neither was present. LTX was intentionally not evaluated in this pass.

### Dependencies and tests

MisoTTS, Breeze TTS, and Whisper retain service-specific `uv.lock` files. A root `pyproject.toml` and `uv.lock` now pin the CPU-safe gateway/test environment, and the frontend now has `package-lock.json` plus `npm ci` in its Dockerfile. Existing NVIDIA/NGC PyTorch bases were not replaced.

The repository already had gateway tests for memory admission, voiceover, history, lifecycle, and ComfyUI template patching. This pass adds readiness preflight and workflow/navigation source tests plus a lightweight CI workflow. GPU generation remains a manual DGX acceptance stage.

## Resource evidence and validation limit

The gateway reported 121.7 GB total shared memory, 16.7 GB available, 105.0 GB used (86.3%), and about 9.9 GB swap in use. `/proc/meminfo` is authoritative on this UMA system. The optional idle-manager systemd unit was not installed, so active model containers were not stopped without operator approval.

Because available memory was below 40 GB:

- no LatentSync, LongCat, Wan, Character, or other heavyweight model was started;
- no heavy end-to-end generation was claimed;
- affected Docker services were not restarted merely to make the deployment appear updated.

See [docs/models.md](docs/models.md) for sources, paths, sizes, variables, first-load behavior, and license boundaries.
