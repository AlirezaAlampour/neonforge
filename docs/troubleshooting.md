# Troubleshooting

## A job says Preparing GPU memory

This is normal. NeonForge is inspecting `/proc/meminfo`, stopping only idle allowlisted model services, waiting for UMA to return, and starting the requested backend. Do not manually stop containers unless the resulting error says automatic reclamation exhausted its allowed candidates.

Inspect the technical state:

```bash
curl -s http://127.0.0.1:8080/workloads/status | jq
awk '/MemTotal|MemAvailable/ {print}' /proc/meminfo
```

The supervisor may stop F5-TTS, Fish Speech, VoxCPM2, MisoTTS, Breeze TTS, ComfyUI, LatentSync, LivePortrait, legacy Wan 2.1, or the experimental Wan UI. It never stops frontend, gateway, Redis, supervisor, Whisper, unrelated containers, or arbitrary host processes.

## Automatic reclamation cannot reach the launch minimum

The error reports required and current `MemAvailable`, services already unloaded, and active claims. Check Utilities & Status for the remaining model services. External workloads are intentionally not killed; stop an unrelated experiment yourself only if it belongs to you.

Swap use can remain high after pressure. Use `MemAvailable` as the admission signal on DGX Spark; discrete-VRAM fields from `nvidia-smi` are not authoritative for UMA.

## Character fails or ComfyUI disappears

Character's longer 85-frame acceptance attempt reached the host OOM killer after preprocessing/model load. The validated preview path therefore uses the first 17 driving frames, requires 48 GiB before a cold start, and runs ComfyUI with `restart: "no"` so OOM remains visible.

Confirm both pose preprocessors:

```bash
test -f /srv/ai/models/comfyui/controlnet_aux/hr16/yolox-onnx/yolox_l.torchscript.pt
test -f /srv/ai/models/comfyui/controlnet_aux/hr16/DWPose-TorchScript-BatchSize5/dw-ll_ucoco_384_bs5.torchscript.pt
```

The gateway detects a stopped ComfyUI backend promptly and allows a 60-second history-publication grace when a completed prompt leaves the queue.

## Lip Sync is unavailable

LatentSync expects:

```text
/srv/ai/models/latentsync/checkpoints/latentsync_unet.pt
/srv/ai/models/latentsync/checkpoints/whisper/tiny.pt
```

Check readiness and bounded logs:

```bash
docker compose --profile lipsync ps -a lipsync
docker logs --tail 100 ai-lipsync
```

The stopped container is still creator-ready: the supervisor starts it after a request. `Missing model` is different and identifies absent files.

## Video Generation reports setup required

Open `/api/v1/comfyui/templates/hunyuan-video-15-t2v` and inspect `validation.missing`. The expected Hunyuan diffusion, Qwen, ByT5, and VAE filenames are listed in [models.md](models.md). The gateway scan is read-only.

## Avatar is disabled

This is intentional. EchoMimicV3-Flash is selected, but its official dependency file does not resolve on Linux ARM64 because TensorFlow 2.15 and `decord` do not provide the required combination of Python/platform wheels. No backend is exposed until a locked patched image and real image+audio output pass.

## ARM64 dependency errors

- use uv only;
- keep the service's NGC PyTorch/CUDA base;
- exclude or patch unused x86-only dependencies only with an inference-path audit;
- regenerate and commit the service lock;
- rebuild the image; do not mutate a running container.

## Service state versus health

Container health only proves that the wrapper process responds. `/services/status` distinguishes Ready, Loading, Disabled, Missing model, Runtime error, and In use. `/workloads/status` shows whether an on-demand backend is stopped, claimed, or inside its warm-idle window.

## Logs

Always bound log reads:

```bash
docker logs --tail 100 ai-gateway
docker logs --tail 100 ai-supervisor
docker logs --tail 100 ai-comfyui
```

For brief live monitoring use `docker logs --tail 50 -f CONTAINER`, then stop following as soon as the relevant event is visible.
