# Compatibility Notes -- DGX Spark AI Stack

## Host Platform Summary

| Property | Value |
|----------|-------|
| CPU Architecture | ARM64 (aarch64) -- NVIDIA Grace |
| GPU | NVIDIA GB10 (Blackwell) |
| Compute Capability | sm_121 |
| CUDA Driver | 580.126.09 |
| CUDA Toolkit | 13.0 |
| Memory | 128 GB UMA (shared CPU+GPU) |
| OS | Ubuntu 24.04 LTS |
| Docker | 29.1.3 |
| Compose | v5.0.1 |
| NVIDIA Runtime | Available |

---

## Per-Service Compatibility Assessment

### 1. Faster-Whisper (Service: `whisper`)

| Risk | Severity | Notes |
|------|----------|-------|
| CTranslate2 ARM64 wheel | MEDIUM | faster-whisper depends on CTranslate2. PyPI ships aarch64 wheels for some versions, but sm_121 (Blackwell) GPU kernels may not be included. If CUDA inference fails, it falls back to CPU (slow). |
| CUDA 13.0 compatibility | MEDIUM | CTranslate2 is typically built against CUDA 11.x/12.x. The CUDA driver is backward-compatible, but JIT compilation for sm_121 may not be available if the CT2 build doesn't include PTX for recent architectures. |
| **Mitigation** | | Build CTranslate2 from source inside the container using `--no-binary ctranslate2`. Requires the `-devel` base image. Alternatively, use the `large-v3` model in float32 on CPU as a degraded fallback. |
| Model downloads | LOW | Whisper models are small (<3 GB) and architecture-independent (just weights). No compatibility risk. |

### 2. F5-TTS (Service: `f5tts`)

| Risk | Severity | Notes |
|------|----------|-------|
| PyTorch ARM64 + sm_121 | HIGH | PyTorch pip wheels from pytorch.org ship with CUDA 12.4 and typically include PTX for sm_90 (Hopper) but NOT sm_121 (Blackwell). JIT compilation from PTX may work via `TORCH_CUDA_ARCH_LIST=12.1` but is slow on first run (~5-10 min). |
| f5-tts pip package | MEDIUM | The `f5-tts` package may have no aarch64 wheel. Installation from source should work since it's pure Python + PyTorch, but transitive C extension dependencies (e.g., `jieba`, `pypinyin`) need verification. |
| Audio libraries | LOW | `librosa`, `soundfile`, `ffmpeg` all have mature ARM64 support on Ubuntu 24.04. |
| **Mitigation** | | Use NGC PyTorch container as base (includes Blackwell kernels), OR use the `-devel` CUDA image and set `TORCH_CUDA_ARCH_LIST=12.1` for JIT compilation. First-run latency will be high but subsequent runs use cached kernels. |

### 3. LivePortrait (Service: `liveportrait`)

| Risk | Severity | Notes |
|------|----------|-------|
| ONNX Runtime GPU | HIGH | `onnxruntime-gpu` may not have ARM64 + CUDA 13 wheels. The standard pip package supports x86_64 primarily. For ARM64, use `onnxruntime-gpu` from NVIDIA's index or build from source. |
| InsightFace | HIGH | `insightface` depends on compiled C++ extensions. ARM64 wheels may not be available on PyPI. Building from source requires `cmake`, `gcc`, and the full CUDA toolkit. |
| MediaPipe | MEDIUM | Google's `mediapipe` has limited ARM64 Linux support. It may need to be replaced with a custom face detection solution or built from Bazel sources (complex). |
| OpenCV headless | LOW | `opencv-python-headless` has ARM64 wheels and works well on Ubuntu 24.04. |
| **Mitigation** | | Consider pre-building InsightFace and ONNX Runtime in a multi-stage Docker build. If MediaPipe fails on ARM64, substitute with `face-alignment` or `dlib` for landmark detection. |

### 4. Lip-sync (Service: `lipsync`)

| Risk | Severity | Notes |
|------|----------|-------|
| Wav2Lip-HQ | CRITICAL | **Do not use.** Last substantive commit: 2021. Depends on ancient PyTorch (1.x), uses `face_detection` package with no ARM64 binary, and is incompatible with CUDA 12+. No active maintainers. |
| video-retalking | HIGH | Research-grade code (SIGGRAPH Asia 2022). Uses multiple sub-models: face parsing, lip-sync GAN, face enhancement. The codebase assumes x86_64 and specific PyTorch versions. ARM64 builds of `basicsr`, `facexlib`, and `gfpgan` are untested. |
| face-alignment | MEDIUM | ARM64 builds usually work but require `dlib` compilation. The `face_detection` sub-dependency is the main risk. |
| basicsr / gfpgan / realesrgan | MEDIUM | These packages have C++ extensions compiled against specific CUDA versions. ARM64 wheels are uncommon. Building from source is required and may fail due to CUDA arch flags. |
| SadTalker (alternative) | MEDIUM | More actively maintained than Wav2Lip-HQ but still research code. Better ARM64 story since it relies primarily on PyTorch and standard CV libraries. |
| **Mitigation** | | Start with `video-retalking`. If ARM64 build fails, fall back to `SadTalker`. Plan for 1-2 days of debugging dependency builds. Consider pre-building all C++ extensions in a dedicated builder stage. |

#### Lip-Sync Backend Comparison: video-retalking vs MuseTalk vs SadTalker

This stack defaults to **video-retalking**. Below is the rationale.

| Criterion | video-retalking | MuseTalk | SadTalker |
|-----------|----------------|----------|-----------|
| **Quality** | High -- SIGGRAPH Asia 2022. Strong lip-sync accuracy, built-in face enhancement (GFPGAN). | Very high -- real-time-capable lip-sync with latent space editing, strong temporal consistency. | Medium -- primarily a talking-head generator, lip sync is a side effect of audio-driven motion. |
| **Architecture** | GAN-based. Multiple specialized sub-models (face parse, lip GAN, enhancer). | Diffusion-based (latent space). Requires a VAE encoder/decoder + audio encoder + denoising U-Net. | 3DMM + face renderer. Lighter than diffusion but produces more artifacts on non-frontal faces. |
| **GPU Memory** | ~3-5 GB (multiple small models loaded sequentially). | ~4-8 GB (diffusion U-Net + VAE). Higher peak during denoising steps. | ~2-4 GB. Lightest of the three. |
| **ARM64 / sm_121** | HIGH RISK. C++ extensions (basicsr, gfpgan, facexlib) need source builds for aarch64. No pre-built wheels. | VERY HIGH RISK. Depends on diffusers + custom CUDA ops. No ARM64 testing reported by maintainers. The custom musetalk package is x86-only in its current release. | MEDIUM RISK. Primarily PyTorch + OpenCV. Fewer native extensions. Best ARM64 compatibility of the three. |
| **Maintenance** | Last release: 2023. Research code, no active maintainer. Issues pile up. | Active as of 2024. Rapid iteration but unstable API. Breaking changes between versions. | Maintained through 2024. More stable API than MuseTalk. |
| **Real-time capable** | No. Batch processing only. ~10-30s per clip. | Yes (with optimization). Can approach real-time on A100. Unlikely on GB10. | No. Similar batch latency to video-retalking. |
| **Production readiness** | Low but proven. Many deployments exist (research labs, demos). | Very low. Still experimental. API and model weights change frequently. | Low. More stable than MuseTalk but fewer production deployments. |
| **Subprocess isolation** | Yes -- we run it as a subprocess. Crashes don't take down the service. | Difficult -- requires in-process GPU model loading. No clean subprocess interface. | Possible via subprocess but less tested. |

**Decision: video-retalking is the default.** Reasons:

1. **Proven quality at SIGGRAPH level.** MuseTalk may produce better results in ideal conditions, but video-retalking has more real-world validation.

2. **Subprocess isolation.** video-retalking can be invoked as a standalone script, meaning crashes/OOM in the lip-sync process don't corrupt the parent service. MuseTalk requires in-process model management.

3. **Lower memory ceiling.** On a shared 128 GB UMA system running 5+ services, video-retalking's 3-5 GB is safer than MuseTalk's 4-8 GB with diffusion peaks.

4. **ARM64 risk is high for all three**, but video-retalking's dependency tree (basicsr, gfpgan, facexlib) is better documented for source builds than MuseTalk's custom ops.

5. **MuseTalk is recommended as a future upgrade** once: (a) ARM64 wheels are available, (b) the API stabilizes, and (c) it can be tested on GB10 Blackwell. At that point, add it as `LIPSYNC_BACKEND=musetalk` and benchmark against video-retalking.

6. **SadTalker is the fallback** if video-retalking's C++ dependencies fail to build on ARM64. Set `LIPSYNC_BACKEND=sadtalker` in `.env`.

### 5. Wan 2.1 (Service: `wan21`)

| Risk | Severity | Notes |
|------|----------|-------|
| Memory footprint | CRITICAL | 14B model: ~28 GB weights (fp16) + 30-60 GB activations during generation. On 128 GB UMA with OS + other services, this may trigger OOM. **Default to 1.3B.** |
| PyTorch + Diffusers | HIGH | Same PyTorch ARM64/sm_121 risk as F5-TTS. The `diffusers` library is pure Python and architecture-independent, but the underlying PyTorch CUDA kernels need sm_121 support. |
| Attention/VAE slicing | MEDIUM | Memory optimization techniques (`enable_attention_slicing`, `enable_vae_slicing`) are critical on UMA but may not be available for all Wan 2.1 pipeline variants. Verify against the specific diffusers version. |
| Model download size | LOW | 1.3B is ~3 GB, 14B is ~28 GB. Download once to `/srv/ai/models/wan21/`. Architecture-independent. |
| **Mitigation** | | Hard-cap resolution at 512x512. Limit frame count. Use `torch.float16` always. Monitor `/proc/meminfo` before and during generation. Gate on available memory (80% hard limit + 40 GB heavy reserve). |

### 6. API Gateway (Service: `gateway`)

| Risk | Severity | Notes |
|------|----------|-------|
| No GPU dependency | NONE | Pure Python (FastAPI + httpx + redis). Works perfectly on ARM64. |
| No Docker socket | NONE | As of v2, the gateway delegates orchestration to the supervisor sidecar. The gateway container has no Docker socket access, reducing attack surface. |

### 7. Supervisor (Service: `supervisor`)

| Risk | Severity | Notes |
|------|----------|-------|
| Docker socket access | LOW | The supervisor holds `/var/run/docker.sock` (read-only) but is not exposed to external networks. It only accepts requests from the gateway over the internal `ai-net` bridge. |
| ARM64 support | NONE | Pure Python (FastAPI + httpx) + Docker CLI. All components have native ARM64 support. |

### 8. Redis (Service: `redis`)

| Risk | Severity | Notes |
|------|----------|-------|
| ARM64 support | NONE | Official Redis Alpine images support ARM64 natively. No compatibility concerns. |

---

## Cross-Cutting Risks

### CUDA 13.0 Backward Compatibility

The CUDA 13.0 **driver** is backward-compatible with applications compiled against CUDA 11.x and 12.x toolkits. However:

- Applications must include PTX code for sm_121 OR a compatible sm_XX target that can be JIT-compiled.
- If a library ships only with sm_70/sm_80/sm_89 SASS (no PTX), it will fail on GB10.
- Setting `TORCH_CUDA_ARCH_LIST=12.1` forces PyTorch to JIT-compile kernels, but this only works if the PyTorch build includes the necessary PTX.
- **Recommendation**: Use NGC-provided PyTorch builds where possible, or build from source with explicit sm_121 support.

### UMA Memory Model

Unlike discrete GPU systems:
- There is no separate VRAM pool. GPU and CPU share 128 GB.
- `nvidia-smi` reports "Not Supported" for memory usage.
- CUDA `cudaMalloc` allocations come from the same physical memory as CPU malloc.
- OOM conditions are **system-level** (Linux OOM killer), not GPU-level (CUDA OOM).
- Docker `--memory` limits apply to RSS only and do NOT cap GPU tensor allocations.
- **Monitoring must use `/proc/meminfo`** (MemAvailable, SwapFree), not `nvidia-smi`.
- The default memory hard limit is 80% (configurable via `MEMORY_HARD_LIMIT`).
- Per-tier reservations ensure minimum available GB before job admission.

### ARM64 Python Ecosystem

Most major Python ML packages now support ARM64 via:
- Native aarch64 wheels on PyPI (numpy, scipy, scikit-learn, Pillow, etc.)
- Building from source during `pip install` (slower but works)

Problem areas:
- Packages with pre-compiled CUDA kernels (CTranslate2, ONNX Runtime GPU)
- Packages with dlib/C++ dependencies (face-alignment, InsightFace)
- Google packages with limited Linux ARM64 support (MediaPipe)

### NGC Container Strategy

For maximum Blackwell compatibility:
- **Best**: Use `nvcr.io/nvidia/pytorch:XX.YY-py3` as the base image. These containers include CUDA toolkit + cuDNN + PyTorch pre-built for all supported architectures including Blackwell.
- **Acceptable**: Use `nvcr.io/nvidia/cuda:13.0.0-devel-ubuntu24.04` and install PyTorch from NVIDIA's pip index.
- **Risky**: Use plain Ubuntu/Python images and install PyTorch from pytorch.org's generic index. sm_121 kernels will be missing.

The current stack uses the CUDA 13.0 runtime/devel images with PyTorch from the generic index. If sm_121 JIT compilation fails, switch to NGC PyTorch containers. The trade-off is much larger image sizes (15-20 GB vs 2-5 GB).
