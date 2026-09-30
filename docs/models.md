# Model matrix

Research/selection date: 2026-09-30. Weights stay outside Git. Availability, passing tests and successful real generation are distinct states.

| Workflow | Backend | Version | Status | Why selected | DGX tested | Typical UMA / admission | License |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Voiceover | Breeze TTS | 2 | Active | Design, clone and direction in the preserved studio | Real voice render | Warm model; 24 GiB admission | [Research/noncommercial weights](https://huggingface.co/BreezeBlue/Breeze-TTS-2) |
| Voiceover | F5-TTS | Installed checkpoint | Retained | Reliability/reference-clone fallback pending comparison | Baseline workflow | 12 GiB admission; measure per checkpoint | [MIT code; checkpoint terms include CC-BY-NC](https://github.com/SWivid/F5-TTS) |
| Voiceover | Fish Speech | Adapter identifies 1.5 | Retained | Compare existing working clone path before removal | Prior installation; no fresh comparison | 24 GiB admission | [Checkpoint-specific research terms](https://huggingface.co/fishaudio/fish-speech-1.5) |
| Voiceover | VoxCPM | 2 | Retained | Design/clone/continuation | Prior installation; no fresh comparison | 16 GiB admission | [Apache-2.0 model card](https://huggingface.co/openbmb/VoxCPM2) |
| Voiceover | MisoTTS | 8B | Retained | Distinct baseline implementation; compare before removal | Real baseline audio | ~31 GiB observed change; 40 GiB admission | [Custom model terms](https://huggingface.co/MisoLabs/MisoTTS) |
| Video | HunyuanVideo | 1.5, 480p CFG-distilled FP8 | Active fallback | Proven local path while successor licensing/proof is unresolved | Real 5-second 832×480 clip | ~35 GiB observed change; 40–60 GiB admission by size | [Tencent Hunyuan community terms](https://github.com/Tencent-Hunyuan/HunyuanVideo-1.5) |
| Character Replace | Wan Animate | 2.2, 14B FP8 | Preview | Specialized motion-preserving replacement | Real 17-frame 1280×720 output | ~54 GiB observed change; 64 GiB admission for 17 frames | [Apache-2.0 upstream](https://huggingface.co/Wan-AI/Wan2.2-Animate-14B); node/LoRA terms also apply |
| Character Animate | — | — | Unavailable | Requires a distinct validated graph | No | — | — |
| Avatar | EchoMimicV3-Flash | Flash | Proof candidate; unavailable | Quality/deployment balance | No real host render yet | Independent deployment benchmark ~33.7 GiB; unmeasured here | [Apache-2.0 code](https://github.com/antgroup/echomimic_v3); base checkpoints have separate terms |
| Lip Sync | LatentSync | 1.6 | Active | Proven synchronization path | Real H.264/AAC output | ~20 GiB observed change; 32 GiB admission | [Apache-2.0](https://github.com/bytedance/LatentSync) |
| Transcription | Faster-Whisper | Configured medium | Protected utility | Voice reference transcription | Existing Voiceover workflow | Shared resident footprint | [MIT implementation](https://github.com/SYSTRAN/faster-whisper) |

UMA values are host MemAvailable deltas, not isolated allocations. They vary with warm caches and other processes. See [real measurements](acceptance-v0.2.md). Existing voice choices remain until meaningful comparisons establish redundancy; they are not five equally recommended defaults.

## Current selection evidence

- [MiniMax H3 official weights](https://huggingface.co/MiniMaxAI/MiniMax-H3) exist and support native audio. The [community license](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) excludes the US, EU, UK and Korea; location or separate rights must be confirmed before proof here. Download size for all repository variants is not the footprint of one inference setup. Existing ~48 GiB isolated quantized files do not establish runtime memory or reproducibility.
- [Official H3 inference paths](https://github.com/MiniMax-AI/MiniMax-H3) include ComfyUI and other runtimes. ComfyUI fits NeonForge's managed generation infrastructure. No H3 integration or new real-render claim is made.
- [LTX-2.5](https://huggingface.co/Lightricks/LTX-2.5) has Blackwell-oriented quantization, but its [community license](https://github.com/Lightricks/LTX-2/blob/main/LICENSE-2_x) includes product/revenue conditions requiring review. It is not silently substituted.
- [Independent talking-avatar comparison](https://github.com/tight-studio/open-source-talking-avatar-benchmark) gives LiveAvatar a detail advantage at higher memory cost and EchoMimicV3-Flash a practical deployment balance. Official unmodified EchoMimic requirements still include TensorFlow 2.15 and decord, which block the current Python/ARM64 wheel combination; unused imports may be patchable, but patchability is not a successful render.
- [Wan Animate-2](https://huggingface.co/Wan-AI/Wan2.2-Animate-2-14B) is newer. It has not passed this machine's memory/render acceptance, so the validated specialized Wan2.2 graph remains.
- [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) and [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) deserve capability comparison. Cached local weights alone are not a working tracked service.

## Installed media files

### HunyuanVideo 1.5

Managed template: `gateway/templates/comfyui/hunyuan-video-15-t2v.manifest.json`.

Installed under `/srv/ai/models/comfyui`:

- `diffusion_models/hunyuanvideo1.5_480p_t2v_cfg_distilled_fp8_scaled.safetensors` — 8,330,399,746 bytes
- `text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors` — 9,384,670,680 bytes
- `text_encoders/byt5_small_glyphxl_fp16.safetensors` — 438,643,184 bytes
- `vae/hunyuanvideo15_vae_fp16.safetensors` — 2,521,292,758 bytes

The UI offers 832×480 landscape, 480×832 portrait, and 640×640 square framing; 2/3/5 seconds; 20-step Draft or upstream-recommended 50-step Studio quality. A 20-step, five-second DGX acceptance render passed. Model details stay secondary to the task-oriented UI.

### Wan2.2 Character

Managed template: `gateway/templates/comfyui/wan-character-swap.manifest.json`. The principal Wan Animate, VAE, text/vision encoder, SAM2, and LoRA files share `/srv/ai/models/comfyui`.

Installed preprocessing files:

- `controlnet_aux/hr16/yolox-onnx/yolox_l.torchscript.pt` — SHA-256 `80bc14b13c260c24b3014cd42c02994bf52296ab8fa2d80a60b6afe08c93ef42`
- `controlnet_aux/hr16/DWPose-TorchScript-BatchSize5/dw-ll_ucoco_384_bs5.torchscript.pt` — SHA-256 `d86a0b2b59fddc0901a7076e9f59c9f8602602133ed72511c693fd11eea23d91`

The 17-frame Replace path is validated. The distinct Animate mode remains disabled. Individual conversions, LoRAs, and custom nodes may have terms beyond Wan upstream's Apache-2.0 license.

### LatentSync 1.6

Runtime source is pinned to commit `a229c3948406bc2cf6eaf4873e662e70c6a04746` inside the service image. Installed checkpoints:

- `/srv/ai/models/latentsync/checkpoints/latentsync_unet.pt` — 5,072,222,488 bytes, SHA-256 `0a478e89eb660f82da4c35dbdde8a5adfb27f99d1b4e50edd03729e1e98316d3`
- `/srv/ai/models/latentsync/checkpoints/whisper/tiny.pt` — 75,572,083 bytes, SHA-256 `65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9`

The official dependency list cannot resolve unmodified on Linux ARM64 because MediaPipe, `onnxruntime-gpu`, and `decord` lack the required wheels. NeonForge's checked-in inference-only patch removes unused MediaPipe/decord paths, uses OpenCV/audio fallbacks, and uses CPU ONNX Runtime for face detection while diffusion remains CUDA-backed. The vendor NGC PyTorch stack is preserved.

The adapter bounds local previews to 10 seconds, 1080p-equivalent source pixels and 30 fps before starting inference. This ceiling is a conservative guardrail; the real acceptance evidence currently covers a shorter clip.


## Legacy and storage

Removed product runtimes: LivePortrait, Wan2.1, ReActor placeholder and cloud Wan UI. Their old outputs, containers and weights are preserved for owner review; none is listed as supported. The disabled Premium Clone scaffold is removed from selection.

See [storage audit](pivot-audit.md#storage-audit-initial-snapshot) for measured directory sizes and orphan candidates. Shared HF/ComfyUI files may serve other applications; no large model weights are deleted automatically.

## DGX rules

Use host MemAvailable, bounded frame/resolution rules, allowlisted reclamation and real readiness. Preserve NVIDIA's tuned ARM64 PyTorch/CUDA stack. Use uv and verify locks. Do not provision checkpoints into Git or Docker image layers. Keep advanced model details in System Info.
