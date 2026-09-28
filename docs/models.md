# Models and Runtime Compatibility

NeonForge keeps model weights outside the Git checkout. The default host roots are `/srv/ai/models` for explicitly managed weights and `/srv/ai/cache/hf` for the shared Hugging Face cache. Container paths are `/models` and `/cache/hf` respectively.

Sizes below are approximate. They describe either the audited installation or the upstream repository, not a guaranteed download size. Never copy weights into this repository.

## Active voice and utility models

| Workflow | Model/source | Approximate size | Expected path | Download behavior | Current DGX Spark result |
| --- | --- | ---: | --- | --- | --- |
| Voiceover | [F5-TTS](https://github.com/SWivid/F5-TTS) (runtime default) | 2–3 GB loaded | Shared HF cache | F5 runtime resolves weights on first load | Available; retained as the reliability-first default |
| Voiceover | [Fish Audio `s2-pro`](https://huggingface.co/fishaudio/s2-pro) | 11 GB installed | `/srv/ai/models/fish_speech/s2-pro` | Pre-provisioned image/model | Available; optional `voice-extras` profile |
| Voiceover | [MisoLabs/MisoTTS](https://huggingface.co/MisoLabs/MisoTTS) | Model-dependent; 8B class | Shared HF cache | Runtime resolves from `MISOTTS_MODEL_ID` | Available as an optional backend; first load can be slow |
| Voiceover | [BreezeBlue/Breeze-TTS-2](https://huggingface.co/BreezeBlue/Breeze-TTS-2) | 7.2 GB installed | `/srv/ai/models/breeze_tts/Breeze-TTS-2` | Automatic when `BREEZE_TTS_AUTO_DOWNLOAD=true` | Available and loaded during the audit; research/non-commercial terms apply |
| Voiceover | [openbmb/VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) | 4.7 GB installed | `/srv/ai/models/voxcpm2/openbmb/VoxCPM2` | Manual/local path | Available as an experimental backend |
| Transcription | [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper) / [`whisper-medium`](https://huggingface.co/openai/whisper-medium) | 1.5 GB installed | `/srv/ai/models/whisper` | Automatic through Faster-Whisper | Ready; audited runtime used CPU `int8` |

Relevant configuration:

- F5-TTS: `F5TTS_IDLE_TIMEOUT`, `F5TTS_SAMPLE_RATE`
- Fish Speech: `FISH_SPEECH_ENABLED`, `FISH_SPEECH_INTERNAL_URL`
- MisoTTS: `MISOTTS_MODEL_ID`, `MISOTTS_DEVICE`, `MISOTTS_CHECKOUT_DIR`, `MISOTTS_MIN_CUDA_FREE_GB`
- Breeze: `BREEZE_TTS_MODEL_ID`, `BREEZE_TTS_MODEL_PATH`, `BREEZE_TTS_AUTO_DOWNLOAD`, `BREEZE_TTS_SOURCE_REV`
- VoxCPM2: `VOXCPM2_MODEL_PATH`
- Whisper: `WHISPER_MODEL_SIZE`, `WHISPER_COMPUTE_TYPE`, `WHISPER_BEAM_SIZE`

F5-TTS, MisoTTS, and Wan may populate the shared Hugging Face cache during their first load. Breeze can populate its explicit model directory. First load can include network transfer, checkpoint deserialization, and CUDA kernel setup; a live service process is not proof that the model is ready.

Dependency and license notes:

- F5-TTS uses PyTorch, the `f5-tts` runtime, audio libraries, and FFmpeg. Its code is MIT, but the official pretrained base checkpoints are CC-BY-NC-4.0; a separately trained checkpoint can have different terms.
- Fish runs from a prebuilt local image, so its exact image dependency lock is not present in this repository. The installed `s2-pro` weights use the Fish Audio Research License: research/non-commercial use is permitted, while commercial use requires a separate license.
- MisoTTS uses the pinned source checkout plus PyTorch/Transformers-style dependencies in its service lock. The Hugging Face model declares its license as `other`; review the accompanying terms before distribution or commercial use.
- Breeze uses its pinned official source revision in an NGC PyTorch image. Source code is Apache-2.0, but weights, derivatives, and self-hosted outputs are research/non-commercial only.
- VoxCPM2 uses NGC PyTorch, `voxcpm`, Transformers, SoundFile, and the service's pinned requirements. Its Hugging Face model declares Apache-2.0.
- Faster-Whisper uses CTranslate2 plus FFmpeg/audio tooling. Faster-Whisper code is MIT; confirm the selected converted checkpoint and source-audio rights separately.

## Character and video

### Character — Wan2.2 Animate replacement

- Source: [`Wan-AI/Wan2.2-Animate-14B`](https://huggingface.co/Wan-AI/Wan2.2-Animate-14B) plus the ComfyUI-native workflow assets listed below.
- Service path: `/srv/ai/models/comfyui` mounted at `/models/comfyui`.
- Installed model directory size during this audit: approximately 77 GB, shared with other ComfyUI workflows.
- Auto-download: no. The gateway performs a read-only model scan and does not move or fetch files.
- Runtime: the `comfyui` Compose profile and the managed `gateway/templates/comfyui/wan-character-swap.workflow.json` template.
- First load: heavyweight. Keep at least `MEM_RESERVE_HEAVY_GB` available and expect graph/node initialization.

The managed template currently references:

- `Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors`
- Wan 2.2 high/low-noise diffusion weights
- `wan_2.1_vae.safetensors`
- UMT5 text encoder and CLIP Vision weights
- SAM2 base-plus checkpoint
- high/low-noise LightX2V LoRAs
- `yolox_l.torchscript.pt`
- `dw-ll_ucoco_384_bs5.torchscript.pt`

The last two pose-preprocessing files were missing during this audit, so Character is blocked before queueing. The existing input patching, model validation, output wiring, and opt-in debug artifacts remain intact.

Wan2.2 Animate upstream code and weights are Apache-2.0, but individual conversions, LoRAs, and custom nodes may have separate terms. Review every downloaded artifact before commercial use.

### Video Generation — Wan 2.1

- Sources: [`Wan-AI/Wan2.1-T2V-1.3B-Diffusers`](https://huggingface.co/Wan-AI/Wan2.1-T2V-1.3B-Diffusers) or [`Wan-AI/Wan2.1-T2V-14B-Diffusers`](https://huggingface.co/Wan-AI/Wan2.1-T2V-14B-Diffusers).
- Expected path: `/srv/ai/models/wan21/1.3B` or `/srv/ai/models/wan21/14B`.
- Auto-download: yes, through the shared HF cache when the explicit local path is absent.
- Configuration: `WAN21_MODEL_VARIANT`, `WAN21_MAX_VIDEO_LENGTH`, `WAN21_MAX_RESOLUTION`, `WAN21_IDLE_TIMEOUT`.
- First load: roughly 8–15 GB total for the 1.3B path and potentially 40–80 GB for 14B according to the service's current operating assumptions.
- Current result: no local Wan 2.1 checkpoint was present and no render was attempted under memory pressure.

The service is profile-gated, singleton, lazy-loaded, and unloads its model after inactivity. LTX is intentionally out of scope for this pass.

## Legacy media services

### Lip Sync — video-retalking fallback

- Expected runtime: `/opt/video-retalking/inference.py`.
- Expected model root: `/srv/ai/models/lipsync/video-retalking/checkpoints`.
- Auto-download: no.
- Configuration: `LIPSYNC_BACKEND`, `LIPSYNC_IDLE_TIMEOUT`, `VIDEO_RETALKING_DIR`, `SADTALKER_DIR`.
- Current result: both the runtime entry point and checkpoint directory were absent. The service now reports `Runtime error`/`Missing model` rather than Ready.

The backend is retained only as a Legacy fallback and is hidden from the default workflow choices. Its research dependencies and model assets retain their upstream terms.

### LivePortrait

- Source checkout expected at `/opt/LivePortrait`.
- Model root expected at `/srv/ai/models/liveportrait`.
- Auto-download: no.
- Configuration: `LIVEPORTRAIT_IDLE_TIMEOUT`, `LIVEPORTRAIT_SOURCE_DIR`.
- Current result: the source checkout exists but does not provide the `liveportrait.api` module used by the adapter; the model directory is also absent.

LivePortrait code is MIT, while the upstream project notes that InsightFace detection models are non-commercial research assets. NeonForge keeps this integration under the `legacy` profile until the adapter and weights are repaired and a real render passes.

## Evaluated candidates, not integrated

### LatentSync 1.6

- Upstream: [`ByteDance/LatentSync`](https://github.com/bytedance/LatentSync) and [`ByteDance/LatentSync-1.6`](https://huggingface.co/ByteDance/LatentSync-1.6).
- Upstream checkpoint size: approximately 9.64 GB.
- Published inference requirement: at least 18 GB GPU memory for the 1.6 pipeline.
- Published environment: PyTorch 2.5.1/CUDA 12.1-era packages plus Diffusers, Transformers, `decord`, MediaPipe, InsightFace, ONNX Runtime GPU, and DeepCache.
- License: OpenRAIL++ model terms; check the repository's component licenses as well.

Decision: not integrated. The current host had only 16.7 GB `MemAvailable`, below NeonForge's 40 GB heavy-workload floor, so a safe end-to-end DGX Spark test was impossible. The upstream native dependencies also need explicit Linux ARM64/GB10 validation inside an NGC-based service image. No API, container, or UI claims that LatentSync is available.

### LongCat-Video-Avatar 1.5

- Upstream: [`meituan-longcat/LongCat-Video`](https://github.com/meituan-longcat/LongCat-Video) and [`meituan-longcat/LongCat-Video-Avatar-1.5`](https://huggingface.co/meituan-longcat/LongCat-Video-Avatar-1.5).
- Architecture: 13.6B family; upstream provides distilled/int8 examples.
- Upstream checkpoint repository size: approximately 74.9 GB.
- Published environment: Python 3.10, PyTorch 2.6/CUDA 12.4, and FlashAttention 2.7.4.post1 plus avatar-specific dependencies.
- License: MIT for the published repository/model card; third-party encoders and user-provided media remain separately governed.

Decision: not integrated. The model was not installed, the host lacked the 40 GB safe reserve, and the published FlashAttention/native stack has not been verified on ARM64/GB10. The Avatar page therefore communicates an honest disabled state and exposes no fake generation controls.

## DGX Spark rules

- Use `/proc/meminfo` and `MemAvailable` for admission decisions; DGX Spark uses unified memory.
- Do not replace vendor-tuned NGC PyTorch layers with generic wheels just to match an upstream requirements file.
- Do not start a heavyweight model below 40 GB available memory.
- Keep optional services profile-gated and load one heavyweight workflow at a time.
- Use each service's checked-in lockfile where present. Do not install packages interactively into a running container.

## License boundary

This repository currently grants no NeonForge source license. That is separate from third-party licenses: an upstream MIT or Apache-2.0 model does not license NeonForge, and NeonForge cannot broaden an upstream non-commercial restriction. Confirm code, weights, voices, reference media, and output-use rights independently.
