# Voiceover Studio

Voiceover Studio is the isolated long-form cloned voiceover path in NeonForge.

It is intentionally separate from the older Creative Studio / Voice Studio F5-TTS flow.

## Why it exists

The legacy TTS flow is useful for shorter direct generations. Voiceover Studio adds a higher-level narration workflow for reusable profiles and longer scripts without rewriting the older architecture.

## Core workflow

1. Create a **Voice Profile** from a short reference clip.
2. Choose a voiceover backend.
3. Select a voice profile when the chosen backend needs one.
4. If VoxCPM2 is selected, choose the Vox mode that matches the job.
5. Paste a long-form script.
6. Set output format and speed.
7. Submit a voiceover job.
8. Track active jobs in the page UI.
9. Play, download, or delete completed outputs from Recent Voiceovers.

## Reference audio ingest

- voice profile uploads accept `.wav`, `.mp3`, and `.m4a`
- accepted uploads are decoded once with `ffmpeg` and stored as a PCM WAV master
- new saved voice profiles always persist as `.wav`
- ingest does **not** force `24 kHz` mono; keep a high-quality master reference and do model-specific conversion later when required
- clips longer than 30 seconds are rejected when duration tools are available
- older already-saved MP3/WAV reference files should remain compatible

## Current backends

### f5tts
- working
- safest default
- best reliability baseline

### fish_speech
- working
- high-quality alternative
- more runtime-specific integration complexity than F5
- should be treated as higher-maintenance

### voxcpm2
- integrated and selectable
- supports three explicit modes:
  - `design`: no reference audio, optional style/control text
  - `clone`: reference audio only, optional style/control text
  - `continuation`: reference audio plus the exact transcript of that clip
- defaults to normal `clone` behavior for backward compatibility
- does **not** auto-transcribe the saved reference clip during normal cloning anymore
- useful for experimentation and some medium-form tests
- should still be treated as experimental for longer cloned narration quality

### cosyvoice3
- optional local backend
- this integration pass exposes clone-only behavior
- requires:
  - saved voice profile
  - exact transcript of that saved reference clip
- no no-reference voice-design mode in this UI pass
- treat as experimental until you verify the upstream stack on your target ARM64/CUDA image

### qwen3tts
- optional local backend
- supports two explicit modes in this pass:
  - `clone`: saved voice profile plus exact transcript
  - `design`: no reference audio, style/control text required
- local voice cloning is only exposed when the reference transcript is available
- treat as experimental until you verify the upstream stack on your target ARM64/CUDA image

## VoxCPM2 modes

### Voice Design
- no saved voice profile is required
- Vox creates a voice from the text alone
- optional style/control text is prepended as Vox-style natural-language guidance

### Clone My Voice
- requires a saved voice profile
- uses `reference_wav_path` only
- optional style/control text is allowed
- this is the default Vox mode in Voiceover Studio

### Continue From Reference
- requires a saved voice profile
- requires the exact transcript of that reference clip
- uses Vox prompt semantics:
  - `reference_wav_path`
  - `prompt_wav_path`
  - `prompt_text`
- best when you want continuation-level nuance preservation from the reference clip
- style/control text is intentionally hidden in this mode to avoid mixing mental models

## Current behavior

- sentence-boundary-first chunking
- paragraph-aware pause preservation
- Vox prefers single-pass generation when the script is small enough and only falls back to larger semantic chunks when needed
- voice profile preview/download from the stored normalized WAV master
- speed control in the Voiceover Studio UI
- recent outputs list with playback, download, and delete
- active job restore after refresh/navigation
- multiple active jobs tracked in the UI
- human-usable output filenames

Output naming format:

`{model_id}_{voice_profile_name}_{YYYY-MM-DD_HHMMSS}.{ext}`

## Important constraints

- preserve old Creative Studio TTS flow
- treat Voiceover Studio as isolated
- prefer small, reversible changes
- avoid broad refactors unless necessary
- be careful with Fish runtime maintenance
- treat Vox quality tuning as experimental

## Current practical recommendation

- production / reliable narration: **F5-TTS**
- higher-quality alternative: **Fish Speech**
- experimental testing: **VoxCPM2**
- transcript-driven reference cloning experiments: **CosyVoice 3**
- optional prompt-only design + transcript-driven clone testing: **Qwen3-TTS**

## Manual model install

Expected host layout:

```text
/srv/ai/models/voice/
  cosyvoice3/
    Fun-CosyVoice3-0.5B-2512/
  qwen3tts/
    Qwen3-TTS-12Hz-0.6B-Base/
    Qwen3-TTS-12Hz-1.7B-VoiceDesign/
```

Official upstream repos:
- CosyVoice 3: `https://github.com/FunAudioLLM/CosyVoice`
- Qwen3-TTS: `https://github.com/QwenLM/Qwen3-TTS`

Official model snapshots:
- CosyVoice 3: `https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512`
- Qwen clone model: `https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base`
- Qwen voice design model: `https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign`

Download commands:

```bash
mkdir -p /srv/ai/models/voice/cosyvoice3 /srv/ai/models/voice/qwen3tts

uv run --with huggingface_hub python - <<'PY'
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
    local_dir="/srv/ai/models/voice/cosyvoice3/Fun-CosyVoice3-0.5B-2512",
    local_dir_use_symlinks=False,
)
snapshot_download(
    repo_id="Qwen/Qwen3-TTS-12Hz-0.6B-Base",
    local_dir="/srv/ai/models/voice/qwen3tts/Qwen3-TTS-12Hz-0.6B-Base",
    local_dir_use_symlinks=False,
)
snapshot_download(
    repo_id="Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
    local_dir="/srv/ai/models/voice/qwen3tts/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
    local_dir_use_symlinks=False,
)
PY
```

NeonForge reads `QWEN3TTS_MODEL_DIR` as a parent directory containing the official Base and VoiceDesign snapshots. A separate `Qwen3-TTS-Tokenizer-12Hz` download is optional, not required, because each official Qwen model snapshot already includes a `speech_tokenizer/` subdirectory.

Enable the backends in `.env`:

```bash
COSYVOICE3_ENABLED=true
COSYVOICE3_MODEL_DIR=/srv/ai/models/voice/cosyvoice3
QWEN3TTS_ENABLED=true
QWEN3TTS_MODEL_DIR=/srv/ai/models/voice/qwen3tts
```

Start the optional services:

```bash
docker compose up -d gateway cosyvoice3 qwen3tts
```

Health and smoke checks:

```bash
curl http://localhost:8080/api/v1/voiceover/models
docker compose exec cosyvoice3 curl -fsS http://localhost:8000/healthz
docker compose exec cosyvoice3 curl -fsS http://localhost:8000/readyz
docker compose exec cosyvoice3 curl -fsS http://localhost:8000/smoke
docker compose exec qwen3tts curl -fsS http://localhost:8000/healthz
docker compose exec qwen3tts curl -fsS http://localhost:8000/readyz
docker compose exec qwen3tts curl -fsS http://localhost:8000/smoke
```

Compatibility note:
- ARM64/CUDA 13 compatibility for these upstream projects is not verified in NeonForge yet. Keep them opt-in and confirm build/load/smoke behavior on your own machine before treating them as stable backends.
