# DGX Spark Compatibility Notes

Audit date: 2026-09-28. Target platform: NVIDIA DGX Spark, ARM64/Grace, GB10/Blackwell (`sm_121`), Ubuntu 24.04, CUDA-capable Docker, and 128 GB unified memory.

## Rules that protect the working stack

- Treat `/proc/meminfo` `MemAvailable` as the resource signal. CPU allocations, page cache, and GPU tensors draw from the same physical memory.
- Keep at least 40 GB available before heavyweight Wan, ComfyUI, Avatar, or Lip Sync validation.
- Preserve the NVIDIA/NGC PyTorch base in GPU images. A generic upstream CUDA wheel can satisfy package metadata while lacking the right ARM64/Blackwell kernels.
- Build native dependencies in the intended container; never use an x86 wheel or an interactive package install as evidence of reproducibility.
- Distinguish process liveness from model readiness and from a real successful render.

## Audited compatibility

| Component | ARM64/GB10 assessment | Current evidence |
| --- | --- | --- |
| Gateway, supervisor, Redis, frontend | Low risk | Running and responsive |
| Faster-Whisper | Medium | Ready in the audited runtime using CPU `int8`; GPU CTranslate2 path was not validated |
| F5-TTS | Medium | Existing service available; retained as Voiceover default |
| Fish Speech | Medium/high maintenance | Existing service and installed `s2-pro` assets present |
| Breeze TTS 2 | Medium | Existing NGC-based service reported loaded/ready; H100 fast path remains disabled |
| MisoTTS | Medium | Existing NGC-based optional service available; model is resolved on first use |
| VoxCPM2 | Medium/experimental | Existing local model and service available |
| LivePortrait | High | Blocked: adapter import mismatch and absent model root |
| video-retalking | High | Blocked: runtime entry point and checkpoints absent |
| Wan2.2 Character | High/heavy | Principal weights present; two preprocessors absent and managed ComfyUI stopped |
| Wan 2.1 video | High/heavy | Service code present; local checkpoint/render unverified |
| LatentSync 1.6 | High/unverified | Not installed; native dependency stack and real render unverified |
| LongCat Avatar 1.5 | High/unverified | Not installed; ~74.9 GB upstream checkpoint and FlashAttention stack unverified |

## Candidate decisions

### LatentSync 1.6

LatentSync is the preferred quality candidate for a future Lip Sync replacement, but it is not a NeonForge backend today. Its official 1.6 instructions specify a CUDA 12.1-era PyTorch environment and native packages including `decord`, MediaPipe, InsightFace, and ONNX Runtime GPU. Each requires actual aarch64/GB10 verification. The upstream model is about 9.64 GB and documents an 18 GB inference minimum. The audited host had only 16.7 GB available shared memory, so installing or rendering would have violated the 40 GB safety floor.

The existing video-retalking adapter is retained as Legacy and now fails closed when its runtime/checkpoints are missing. This is a smaller and more truthful change than adding an untested backend abstraction.

### LongCat-Video-Avatar 1.5

LongCat is the single preferred future Avatar candidate. The upstream model repository is about 74.9 GB, based on a 13.6B family, and its published environment includes PyTorch 2.6/CUDA 12.4 and FlashAttention 2.7.4.post1. It needs an NGC-preserving ARM64 build plus human, stylized, and longer-audio renders before integration. No InfiniteTalk or second avatar backend was added.

### Wan

Wan remains the default video family. Character keeps the current Wan2.2 Animate managed ComfyUI template and its validated graph-patching/debug infrastructure. Video Generation keeps the smaller Wan 2.1 service default. LTX is deliberately deferred because no concrete failure justified expanding this pass.

## Native dependency risk

The highest-risk packages are CUDA/native extensions such as FlashAttention, CTranslate2 GPU, ONNX Runtime GPU, InsightFace, MediaPipe, `decord`, BasicSR/GFPGAN/Real-ESRGAN, and some ComfyUI custom nodes. Their availability on PyPI is not proof that an aarch64 wheel supports `sm_121`.

For a reproducible fix:

1. Start from the service's checked-in NGC image and lockfile.
2. Confirm the existing PyTorch/CUDA architecture support before installing anything.
3. Pin and build only the missing dependency in the Dockerfile/locked environment.
4. Run import, readiness, and real-generation tests inside that image.
5. Record model/version/license details in [docs/models.md](docs/models.md).

See [docs/troubleshooting.md](docs/troubleshooting.md) for operational checks and [CURRENT_STATE.md](CURRENT_STATE.md) for the observed deployment state.
