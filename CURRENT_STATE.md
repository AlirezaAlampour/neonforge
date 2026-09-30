# Current Audited State

Audit and acceptance date: 2026-09-29. Evidence below comes from the current 128 GB DGX Spark and the deployed NeonForge stack.

## Product state

Primary navigation is exactly Voiceover, Video Generation, Character, Avatar, Lip Sync, and Utilities & Status. The old Creative Studio, B-Roll, and standalone voice surfaces are hidden from navigation but remain addressable for compatibility. Voiceover Studio was not redesigned.

| Workflow | State | Evidence |
| --- | --- | --- |
| Voiceover | Existing workflow preserved | Live MisoTTS job `7ecc8750-2c80-4ffa-9791-4342e126a9e4` completed through the gateway/supervisor path |
| Video Generation | HunyuanVideo 1.5 integrated | Gateway job `ce13fa45-f565-470b-85d6-a5de5c3f24ed` completed; 512×288, 17 frames, H.264 |
| Character Replace | Wan2.2 Animate integrated | Gateway job `7d716c8b-3c5e-4dec-a205-80de71be5a72` completed; 1280×720, 17 frames, H.264 |
| Character Animate | Not integrated | Its distinct graph remains disabled rather than reusing Replace incorrectly |
| Lip Sync | LatentSync 1.6 integrated | Gateway job `6636aaab-80d6-4a54-a3bc-3a631ce13225` completed; 1080×1920, H.264/AAC |
| Avatar | Selected but blocked | EchoMimicV3-Flash official dependency set does not resolve for ARM64; no service or fake generation claim |

## Automatic resource manager

The gateway now obtains a supervisor claim before managed work. The supervisor has an immutable service allowlist, model/workflow launch minimums, targeted conflict reclamation, readiness waits, claim tracking, measured metrics, and idle unloads.

Protected services are frontend, gateway, Redis, supervisor, and Whisper. They cannot be placed in the managed policy set. Unrelated containers and host processes are never candidates.

Current launch minimums are 48 GiB for Wan Character, 40 GiB for HunyuanVideo, 32 GiB for LatentSync, 12–24 GiB for voice services, and 12 GiB for legacy LivePortrait. Defaults are 300 seconds idle for heavy media and 900 seconds for voice, configurable through `.env`.

System Info exposes total/available UMA, loaded/running services, active claims, workload classes, launch minimums, idle windows, and lifecycle counters. Creator screens show Preparing GPU memory, Loading model, and Generating rather than manual stop instructions.

A post-deploy Hunyuan prepare check began at 27.3 GiB available. The supervisor stopped only idle MisoTTS, recovered to 57.8 GiB, started ComfyUI, issued the 40 GiB workflow-specific claim, and released it successfully. The five-minute idle sweep then stopped ComfyUI and recovered its remaining 1.1 GiB wrapper footprint. No protected or unrelated service was touched.

## Real generation evidence

### Character

Installed preprocessors:

- `yolox_l.torchscript.pt`: 207 MB, SHA-256 `80bc14b13c260c24b3014cd42c02994bf52296ab8fa2d80a60b6afe08c93ef42`
- `dw-ll_ucoco_384_bs5.torchscript.pt`: 128 MB, SHA-256 `d86a0b2b59fddc0901a7076e9f59c9f8602602133ed72511c693fd11eea23d91`

An 85-frame attempt proved DWPose, SAM2, T5, and Wan model loading but reached the Linux OOM killer at the first diffusion step, with approximately 3.9 GiB still reported before termination. NeonForge now leaves ComfyUI stopped after OOM (`restart: "no"`) and reports the lost prompt.

The bounded 17-frame run then passed:

- output: `/srv/ai/outputs/comfyui/output/Wanimate_00012.mp4`
- 1280×720, 16 fps, 17 frames, 1.0625 s, 368,194 bytes
- SHA-256: `847991bfdf58cdec217c4e7f16f8249b4ca8b62b1f05ad2a078de98f10ec740d`
- duration: 233.4 s
- `MemAvailable`: 58.1 GiB before, 3.8 GiB lowest observed
- idle unload: ComfyUI stopped after 300 s; 43.0 GiB recovered

### Lip Sync

LatentSync 1.6 uses official commit `a229c3948406bc2cf6eaf4873e662e70c6a04746` and official checkpoints. The inference-only ARM64 patch removes unused MediaPipe/decord requirements, uses CPU ONNX face detection, and keeps diffusion on CUDA.

- output: `/srv/ai/outputs/lipsync/84f9221e-9aa3-47cf-85ff-8339b2f2e44a_synced.mp4`
- 1080×1920, 25 fps, 52 video frames, 2.08 s, H.264/AAC, 832,864 bytes
- SHA-256: `25195d6a5c067850d5a9ddf4c266264d426626ec83f9b21b1c737f815803d9bc`
- duration: 111.0 s
- `MemAvailable`: 59.8 GiB before, 40.0 GiB lowest observed, 59.5 GiB on release
- idle unload: container stopped after 300 s

### General Video

HunyuanVideo 1.5 runs through a dedicated managed template using the official ComfyUI node graph and 480p CFG-distilled FP8 weights.

- accepted job: `ce13fa45-f565-470b-85d6-a5de5c3f24ed`
- output: `/srv/ai/outputs/comfyui/output/video/hunyuan_video_1.5_00002_.mp4`
- 512×288, 16 fps, 17 frames, 1.062 s, H.264, 40,923 bytes
- SHA-256: `022cfe7945384b6580a136f5ec3f644ac13a458bebdba5d5a7aeb91be3d3003e`
- cold run: 168.4 s; 58.8 GiB before and 26.7 GiB lowest observed
- warm run: 16.1 s

### Voiceover regression

The untouched creator workflow completed a live MisoTTS text-to-speech job after the lifecycle integration:

- job: `7ecc8750-2c80-4ffa-9791-4342e126a9e4`
- output: 24 kHz mono PCM WAV, 6.0 s, 288,044 bytes
- SHA-256: `56f955f70444f773714b9c6d2c4b5c8331f21fa6100bb59ac0677127497329bd`
- duration: 110.6 s
- `MemAvailable`: 57.2 GiB before, 26.6 GiB lowest observed, 27.4 GiB on release
- lifecycle: claim released successfully; MisoTTS remained loaded for its configurable voice warm window

## Known limits

- Character Animate has no validated graph.
- Character Replace deliberately processes the first 17 driving frames on this host; the longer attempt exhausted UMA.
- Avatar is disabled. EchoMimicV3-Flash remains selected, but upstream pins TensorFlow 2.15 and `decord`; the former has no CPython 3.12 match and the latter has no Linux ARM64 wheel. A patched build plus complete base/audio weights still needs an isolated real render.
- Legacy Wan 2.1 and LivePortrait code remains for compatibility but is not a primary creator backend.
- Swap use remained elevated from earlier pressure during acceptance; `/proc/meminfo` is the authoritative admission source.
