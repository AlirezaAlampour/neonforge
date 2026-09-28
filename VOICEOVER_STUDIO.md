# Voiceover Studio

Voiceover Studio is the isolated long-form cloned voiceover path in NeonForge.

It is intentionally separate from the older Creative Studio / Voice Studio F5-TTS flow.

## Why it exists

The legacy TTS flow is useful for shorter direct generations. Voiceover Studio adds a higher-level narration workflow for reusable profiles and longer scripts without rewriting the older architecture.

## Core workflow

1. Create a **Voice Profile** from a short reference clip.
2. Choose a voiceover backend.
3. Select a voice profile when the chosen backend needs one, or optionally for MisoTTS prompt-audio conditioning.
4. Choose a model-specific mode when Breeze TTS 2 or VoxCPM2 is selected.
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

### misotts
- integrated as an optional containerized runtime
- supports plain TTS with no saved voice profile
- supports prompt-audio conditioning when you select a saved voice profile that has an exact transcript
- first request downloads and loads the upstream `MisoLabs/MisoTTS` weights
- upstream-generated audio is watermarked by default
- should be treated as GPU-heavy and still somewhat experimental on constrained hosts

### breeze_tts
- dedicated optional service using the official `BreezeBlue/Breeze-TTS-2` eager PyTorch inference path
- `design`: script + natural-language voice description; no profile
- `clone`: saved profile audio + exact reference transcript; no instruction
- `direction`: saved profile audio + exact reference transcript + natural-language direction
- CFG Scale is shown only for Design and Direction and defaults to the upstream-recommended starting value of `4`
- seed is explicit, stable across script edits, and changed only with the Randomize action or direct editing
- English vocal events such as `(laugh)`, `(sigh)`, `(cough)`, and `(clears throat)` can be inserted from the script editor
- missing profile transcripts can be generated with the existing Whisper service, reviewed, edited, and saved back to the profile
- model weights, derivatives, and self-hosted outputs are limited to research/non-commercial use

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
- MisoTTS appears in the backend picker when its optional runtime is reachable
- Breeze reports disabled, unavailable, loading, ready, and runtime-error states through gateway model discovery
- controls are backend- and mode-aware; advanced seed/CFG/transcript details stay collapsed until needed
- completed output audio appears beside job progress immediately after rendering
- voice profile preview/download from the stored normalized WAV master
- speed control in the Voiceover Studio UI
- recent outputs list with playback, download, and delete
- active job restore after refresh/navigation
- multiple active jobs tracked in the UI
- human-usable output filenames

Output naming format:

`{model_id}_{voice_profile_name}_{script_slug}_{YYYY-MM-DD_HHMMSS}.{ext}`

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
- optional prompt-conditioned voice work: **MisoTTS**
- designed or directed creator voices: **Breeze TTS 2**
- experimental testing: **VoxCPM2**

## Breeze runtime and long-form behavior

- Service: `services/breeze_tts/`
- Enable: `BREEZE_TTS_ENABLED=true`
- Start/rebuild: `docker compose --profile breeze up -d --build breeze_tts gateway frontend`
- Model: `BreezeBlue/Breeze-TTS-2`
- Default persistent model path: `/models/breeze_tts/Breeze-TTS-2`
- Shared Hugging Face cache: `/cache/hf`
- Runtime: NGC PyTorch on ARM64/GB10, eager attention, no `--fast-all`
- Output contract: mono 24 kHz signed 16-bit PCM is wrapped into WAV by the service before the gateway receives it
- Long scripts use NeonForge's normal sentence/paragraph chunking and standard WAV stitching. Breeze does not use Vox-specific trimming or crossfades.
