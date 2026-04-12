# Voiceover Studio

Voiceover Studio is the isolated long-form cloned voiceover path in NeonForge.

It is intentionally separate from the older Creative Studio / Voice Studio F5-TTS flow.

## Why it exists

The legacy TTS flow is useful for shorter direct generations. Voiceover Studio adds a higher-level narration workflow for reusable profiles and longer scripts without rewriting the older architecture.

## Core workflow

1. Create a **Voice Profile** from a short reference clip.
2. Select a voice profile.
3. Choose a voiceover backend.
4. Paste a long-form script.
5. Set output format and speed.
6. Submit a voiceover job.
7. Track active jobs in the page UI.
8. Play, download, or delete completed outputs from Recent Voiceovers.

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
- useful for experimentation and some medium-form tests
- should still be treated as experimental for longer cloned narration quality

## Current behavior

- sentence-boundary-first chunking
- paragraph-aware pause preservation
- model-specific chunk tuning where needed
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
