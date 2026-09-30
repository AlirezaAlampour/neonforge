# DGX Spark compatibility

Updated 2026-09-30. Target: NVIDIA DGX Spark GB10, ARM64 Linux, 128 GB shared UMA.

- Preserve NVIDIA/NGC's PyTorch and torchvision builds. Public wheel compatibility is not proof of Blackwell kernel support.
- Read host MemAvailable. See the supervisor's bounded workload rules and the measured [acceptance evidence](docs/acceptance-v0.2.md).
- Use uv and verify the relevant lock before running code. Package changes belong in a reproducible image, not a running model container.
- ComfyUI's managed Hunyuan and Wan Replace paths have real host evidence. Wan's 85-frame attempt OOMed; longer Character settings remain rejected.
- LatentSync 1.6 uses the checked-in inference-only ARM64 patch: CPU ONNX face detection, CUDA diffusion, OpenCV/audio fallbacks for unavailable packages. Its vendor CUDA stack is retained.
- Breeze's preserved service has real design-generation evidence. Voice models still need comparative quality/capability testing before pruning.
- EchoMimicV3-Flash's upstream TensorFlow 2.15 pin has no Python 3.12 wheel; decord also lacks the required ARM64 binary path. The fresh isolated uv resolver failure is recorded in the model audit. No Avatar inference runtime is claimed.
- MiniMax H3 has downloadable weights and local inference paths. Its community license excludes several territories, including the US. Existing quantized files alone prove neither deployment rights nor reliable local generation.

See [models and licenses](docs/models.md), [architecture](ARCHITECTURE.md), and [troubleshooting](docs/troubleshooting.md). Historical model experiments are not supported product options.
