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
