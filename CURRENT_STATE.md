# Current state

## Product surfaces

NeonForge currently exposes these primary UI surfaces:

- System Status
- Creative Studio
- Voice Studio
- Voiceover Studio
- B-Roll Studio
- Lip Sync Studio

## Important architecture split

The older Creative Studio / Voice Studio F5-TTS flow remains separate from Voiceover Studio.

- **Do not collapse them together by default.**
- Legacy F5-TTS routes and UI behavior should remain stable.
- Voiceover Studio is the isolated long-form narration path.

## Voiceover Studio summary

Voiceover Studio currently supports:

- reusable voice profiles
- voice profile uploads from `.wav`, `.mp3`, and `.m4a`
- long-form script rendering
- sentence-boundary-first chunking
- VoxCPM2 mode selection:
  - `design`
  - `clone`
  - `continuation`
- Breeze TTS 2 mode selection:
  - `design`
  - `clone`
  - `direction`
- backend-aware controls with progressively disclosed advanced settings
- editable profile transcripts with Whisper fill for Breeze references
- speed control
- recent outputs with play/download/delete
- active job restore after refresh
- multiple tracked jobs in the UI
- human-usable output naming:
  - `{model_id}_{voice_profile_name}_{script_slug}_{YYYY-MM-DD_HHMMSS}.{ext}`

## Voice profile ingest and storage

- accepted voice profile uploads are decoded once with `ffmpeg` and saved as a PCM WAV master
- new saved voice profiles always persist as `.wav`, even if the source upload was MP3 or M4A
- ingest does **not** force `24 kHz` mono; keep the highest-quality practical master and do runtime-specific conversion later if needed
- reference clips longer than 30 seconds are rejected when duration tools are available
- older already-saved MP3/WAV profile assets should remain readable for backward compatibility
- the gateway image now depends on `ffmpeg` for safe voice-profile ingest normalization

## Voiceover backends

### f5tts
- working
- safest / most boring default
- preferred when reliability matters most

### fish_speech
- working
- higher-quality alternative
- higher-maintenance runtime than F5
- should be treated honestly as less boring than F5

### misotts
- optional plain and prompt-conditioned local backend
- uses a dedicated lazy-loading runtime

### breeze_tts
- first-class optional Voiceover Studio backend using `BreezeBlue/Breeze-TTS-2`
- dedicated persistent service under `services/breeze_tts/`
- official eager PyTorch runtime on DGX Spark; H100-oriented `--fast-all` is disabled
- Design requires a voice description and no profile
- Clone requires saved profile audio + exact transcript and sends no instruction
- Direction requires profile audio + exact transcript + natural-language direction
- seed is reproducible and CFG Scale defaults to `4` for Design/Direction
- service health distinguishes disabled, unavailable, loading, ready, and error
- weights, derivative models, and self-hosted outputs are research/non-commercial only

### voxcpm2
- integrated and selectable
- default behavior is now normal `clone` mode, not prompt-style continuation
- normal clone mode sends only reference audio
- continuation mode is explicit and requires the exact transcript of the saved reference clip
- voice design mode can run without a saved voice profile
- usable for experimentation and some medium-form testing
- should still be treated as experimental for longer cloned narration quality
- do not oversell it as solved for all long scripts

## Practical recommendation

- Production/reliable voiceover: **F5-TTS**
- Higher-quality alternative: **Fish Speech**
- Optional prompt-conditioned voice work: **MisoTTS**
- Designed and directed voices: **Breeze TTS 2**
- Experimental testing: **VoxCPM2**

## Environment/config currently relevant to voiceover

- `ASSETS_DIR`
- `OUTPUTS_DIR`
- `FISH_SPEECH_ENABLED`
- `FISH_SPEECH_INTERNAL_URL`
- `BREEZE_TTS_ENABLED`
- `BREEZE_TTS_INTERNAL_URL`
- `BREEZE_TTS_MODEL_ID`
- `BREEZE_TTS_MODEL_PATH`
- `BREEZE_TTS_AUTO_DOWNLOAD`
- `VOXCPM2_ENABLED`
- `VOXCPM2_INTERNAL_URL`
- `VOXCPM2_MODEL_PATH`

## Constraints for future coding passes

- preserve old Creative Studio TTS flow
- treat Voiceover Studio as isolated
- prefer small, reversible changes
- avoid broad refactors unless necessary
- be careful with Fish runtime maintenance
- treat Vox quality tuning as experimental
