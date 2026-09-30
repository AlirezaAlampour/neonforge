# Models, Selection Research, and Runtime Compatibility

Research date: 2026-09-29. NeonForge keeps weights under `/srv/ai/models` or `/srv/ai/cache/hf`; model binaries are never committed.

## Selection record

Selections use official repositories/model cards plus open or independent comparisons. Vendor claims are not the sole basis for a choice.

| Workflow | Candidate | License | Expected deployment size/memory | Decision |
| --- | --- | --- | --- | --- |
| General Video | [HunyuanVideo 1.5](https://github.com/Tencent-Hunyuan/HunyuanVideo-1.5) | Tencent Hunyuan Community License | 8.3B; official 14 GB offload claim; NeonForge model files total ~20.7 GB | **Selected.** Higher open-Arena score than Wan2.2 and LTX-2 at audit; official ComfyUI workflow; real DGX render passed |
| General Video | [Wan2.2](https://github.com/Wan-Video/Wan2.2) | Apache-2.0 code/model terms as published | A14B is substantially heavier | Retained for specialized Character work; not duplicated as the general engine |
| General Video | [LTX-2.3](https://huggingface.co/Lightricks/LTX-2.3) | LTX community license | 22B; native synchronized audio/video | Future fast/native-audio candidate; not a second equivalent engine in this pass |
| General Video | [MiniMax H3](https://github.com/MiniMax-AI/MiniMax-H3) | MiniMax community/open-weight terms | Repository variants total roughly 498 GB; official example uses four GPUs | Rejected for local default despite leading open-Arena quality: impractical Spark footprint and geographic/license restrictions |
| Character | [Wan2.2 Animate 14B](https://huggingface.co/Wan-AI/Wan2.2-Animate-14B) | Apache-2.0 upstream | ~77 GB shared Comfy model tree; observed very high peak UMA | **Selected.** Purpose-built Animate/Replace backend; real Replace render passed |
| Lip Sync | [LatentSync 1.6](https://huggingface.co/ByteDance/LatentSync-1.6) | Apache-2.0 repository; component/model terms apply | Official 18 GB inference minimum; 5.07 GB UNet plus Whisper | **Selected.** Quality-first diffusion lip sync; patched ARM64 real render passed |
| Lip Sync | [MuseTalk 1.5](https://github.com/TMElyralab/MuseTalk) | MIT code; model/component terms apply | Lower-cost alternative | Rejected as default because LatentSync passed and remains the quality-first choice |
| Avatar | [EchoMimicV3-Flash](https://github.com/antgroup/echomimic_v3) | Apache-2.0 | Official 12 GB VRAM claim; 1.3B, 8 steps, up to 768×768 | **Selected, not integrated.** Best quality/infrastructure balance in the independent deployment benchmark |
| Avatar | [LiveAvatar](https://github.com/Alibaba-Quark/LiveAvatar) | Upstream terms | Independent benchmark: roughly 61 GB deployment | Rejected for this pass: sharp identity but much heavier |
| Avatar | [LongCat-Video-Avatar 1.5](https://github.com/meituan-longcat/LongCat-Video) | MIT | Independent benchmark: roughly 46 GB deployment | Rejected for this pass: expressive motion, but heavier than EchoMimic |
| Avatar | [SoulX-FlashHead](https://github.com/Soul-AILab/SoulX-FlashHead) | Apache-2.0 | 1.3B; official Lite path targets a single RTX 4090 | Not selected because the quality target favors EchoMimic's stronger independent deployment balance |
| Avatar | [AptAvatar](https://github.com/TaoLiveAIGC/AptAvatar) | Apache-2.0 repository | Published weights were still marked TODO | Rejected: announcement/inference code is not a runnable checkpoint release |

Quality context: the [open Text-to-Video Arena](https://arena.ai/leaderboard/text-to-video?license=open-source&rankBy=labs) placed MiniMax H3 highest among inspected open-weight entries, then HunyuanVideo 1.5 above LTX-2 and Wan2.2 at audit time. The independent [open-source talking-avatar deployment benchmark](https://github.com/tight-studio/open-source-talking-avatar-benchmark) identified LiveAvatar for sharp identity, LongCat for expressiveness, and EchoMimicV3-Flash for deployment balance.

## Installed and validated media models

### HunyuanVideo 1.5

Managed template: `gateway/templates/comfyui/hunyuan-video-15-t2v.manifest.json`.

Installed under `/srv/ai/models/comfyui`:

- `diffusion_models/hunyuanvideo1.5_480p_t2v_cfg_distilled_fp8_scaled.safetensors` — 8,330,399,746 bytes
- `text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors` — 9,384,670,680 bytes
- `text_encoders/byt5_small_glyphxl_fp16.safetensors` — 438,643,184 bytes
- `vae/hunyuanvideo15_vae_fp16.safetensors` — 2,521,292,758 bytes

The UI offers 832×480 landscape, 480×832 portrait, and 640×640 square framing; 2/3/5 seconds; 20-step Preview or upstream-recommended 50-step Quality. A small 20-step DGX acceptance render passed. Model details stay secondary to the task-oriented UI.

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

## Avatar blocker

EchoMimicV3-Flash remains the selected backend, but it is not integrated. The official requirements fail reproducible resolution:

- TensorFlow 2.15 has no CPython 3.12 wheel matching the service base;
- `decord` has no Linux ARM64 wheel for the supported versions;
- `infer_flash.py` also imports `pyloudnorm`, which is absent from upstream `requirements.txt`;
- the complete runtime requires the ~19.8 GB Wan2.1-Fun base, 3.73 GB Flash transformer, and a separately hosted Chinese wav2vec model.

Some of these imports appear patchable or unused, but NeonForge does not expose Avatar generation until a locked ARM64 image and real image+audio result pass. This is intentionally a disabled surface, not a health-only integration.

## Voice and utility models

| Model | Role | Current state | License note |
| --- | --- | --- | --- |
| [F5-TTS](https://github.com/SWivid/F5-TTS) | Default voice synthesis | Preserved | Code MIT; official base checkpoints commonly CC-BY-NC-4.0 |
| [Fish Speech s2-pro](https://huggingface.co/fishaudio/s2-pro) | Optional voice | Managed on demand | Fish Audio Research License |
| [MisoTTS](https://huggingface.co/MisoLabs/MisoTTS) | Optional voice | Managed on demand | Model declares `other`; review terms |
| [Breeze TTS 2](https://huggingface.co/BreezeBlue/Breeze-TTS-2) | Optional voice | Managed on demand | Research/non-commercial weights |
| [VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) | Optional voice | Managed on demand | Apache-2.0 model card |
| [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) | Transcription | Protected/resident | MIT code; selected checkpoint terms apply |

## DGX Spark rules

- Use `/proc/meminfo` and `MemAvailable`; GPU and CPU share UMA.
- Preserve NGC's ARM64 PyTorch/CUDA stack. Never replace it with a generic wheel to satisfy upstream pins.
- Use uv and checked-in locks for package management.
- Provision model files in shared host storage, never in Git or an image layer.
- Let the supervisor reclaim allowlisted conflicts and start heavy services. Do not launch the `full` profile as a normal workflow.
