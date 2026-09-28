# Troubleshooting

## Shared-memory pressure

DGX Spark uses unified memory: CPU processes, containers, filesystem cache, and CUDA allocations draw from the same pool. `nvidia-smi` framebuffer-memory fields are not a reliable admission signal here.

```bash
mem_kb="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
awk -v kb="$mem_kb" 'BEGIN {printf "MemAvailable: %.1f GB\n", kb/1024/1024}'
docker stats --no-stream
```

- Do not start a heavy model below 40 GB `MemAvailable`.
- Stop an optional model through its normal Compose profile before loading another one.
- Do not use `docker compose --profile full up`; it can keep several large runtimes resident together.
- Swap use is evidence of recent pressure even if a process has already stopped.
- The idle-manager unit is optional. Confirm the per-user template is installed before relying on it: `systemctl status "ai-idle-manager@$(id -un).timer"`.

## A service says Ready but generation fails

Use `/services/status`, not container health alone. Liveness means the FastAPI process responds; readiness means the runtime and required files passed preflight.

```bash
curl -s http://127.0.0.1:8080/services/status | python -m json.tool
```

Creator-facing states are Ready, Loading, Disabled, Missing model, and Runtime error. Open diagnostics in the UI for exact paths only when needed.

## Lip Sync is unavailable

The current fallback is legacy video-retalking. The audited container had neither `/opt/video-retalking/inference.py` nor `/models/lipsync/video-retalking/checkpoints`, so NeonForge now reports Runtime error rather than a false Ready state.

LatentSync 1.6 has not been integrated. Its official environment pins CUDA 12.1-era dependencies, `decord`, MediaPipe, InsightFace, and `onnxruntime-gpu`; these still require an actual ARM64/GB10 build and end-to-end render before the backend can be enabled.

## LivePortrait is unavailable

The current service checkout exists, but its adapter imports `liveportrait.api`, which the checked-out upstream source does not provide, and `/srv/ai/models/liveportrait` is absent. The workflow remains Legacy until both the adapter and weights are repaired and a real render passes.

## Character reports missing models

Open Character and refresh model validation. The audited workflow was missing:

- `yolox_l.torchscript.pt`
- `dw-ll_ucoco_384_bs5.torchscript.pt`

The gateway scans shared model roots read-only; it does not download or move files. Also confirm that the Compose-managed `ai-comfyui` container is running. A separate container listening on another port does not satisfy the managed workflow URL or supervisor scan target.

## ARM64 wheel or native-extension failures

- Keep the NVIDIA NGC PyTorch base; do not replace it with a generic CPU or CUDA wheel.
- Resolve pure-Python dependencies with the service's `uv.lock` where present.
- Packages such as InsightFace, MediaPipe, `decord`, FlashAttention, and ONNX Runtime GPU need explicit ARM64/GB10 validation.
- Do not fix a missing import by installing packages interactively into a running container. Update the locked service environment and rebuild instead.

## NVIDIA PyTorch/CUDA mismatch

Check the versions already present in the NGC image before changing dependencies. Generic upstream wheels may not contain GB10/sm_121 kernels. A working vendor stack takes precedence over satisfying an upstream requirements file verbatim.

## Hugging Face model is missing

Confirm the expected path in [models.md](models.md), available disk space, access-token requirements, and license acceptance. Downloads should target `/srv/ai/models` or the shared `/srv/ai/cache/hf`, never the Git checkout.

## First load takes a long time

First use may download weights, populate kernels, or move a model into unified memory. Watch the workflow state and `MemAvailable`. A health response does not mean first-load work is complete.

## Port conflicts or remote access

Change `FRONTEND_PORT`, `GATEWAY_PORT`, or `COMFYUI_PORT` in `.env`. Set `BIND_ADDRESS` to one trusted host address. The default is loopback for safety.

```bash
ss -ltn | rg ':(3000|6379|8080|8188)\b'
docker compose ps
```

## Container logs

Always bound log reads:

```bash
docker logs --tail 100 ai-gateway
docker logs --tail 100 ai-frontend
```

For temporary startup monitoring, use `docker logs --tail 50 -f CONTAINER` and stop following as soon as success or failure is clear.
