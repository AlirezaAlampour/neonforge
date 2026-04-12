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
- long-form script rendering
- sentence-boundary-first chunking
- speed control
- recent outputs with play/download/delete
- active job restore after refresh
- multiple tracked jobs in the UI
- human-usable output naming:
  - `{model_id}_{voice_profile_name}_{YYYY-MM-DD_HHMMSS}.{ext}`

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

### voxcpm2
- integrated and selectable
- usable for experimentation and some medium-form testing
- should still be treated as experimental for longer cloned narration quality
- do not oversell it as solved for all long scripts

## Practical recommendation

- Production/reliable voiceover: **F5-TTS**
- Higher-quality alternative: **Fish Speech**
- Experimental testing: **VoxCPM2**

## Environment/config currently relevant to voiceover

- `FISH_SPEECH_ENABLED`
- `FISH_SPEECH_INTERNAL_URL`
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
