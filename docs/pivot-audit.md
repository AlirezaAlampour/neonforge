# NeonForge v0.2 audit

Selection date: 2026-09-30. Baseline: tested `MisoTTS` commit `994fc92`.
Implementation branch: `neonforge-v0.2`; never merge automatically into the baseline.

## Decisions before pruning

| Area | Classification | Decision / reference check |
| --- | --- | --- |
| Voiceover, profiles, Breeze, F5, Fish, VoxCPM2, Miso | KEEP | Distinct capabilities have not yet been compared sufficiently to remove working voices. Preserve UI and adapters. |
| System Info, model inventory, health and lifecycle events | KEEP | Canonical operator surface. |
| Video, Character, Lip Sync creator components | REPLACE | Keep job/upload infrastructure; simplify presentation and settings. |
| Avatar | KEEP unavailable | Require an isolated real render before enabling generation. |
| `/studio`, `/voice`, `/broll` | DELETE | Duplicated legacy creator routes; no primary navigation references. |
| `history-pane`, `audio-recorder`, `studio-store` | DELETE | Imported only by removed pages. Shared recording hook remains used by Voiceover. |
| Gateway Redis jobs, SQLite output history, validation, managed templates | KEEP | Used by canonical workflows and acceptance tooling. |
| Supervisor claims, allowlist, idle unload, readiness | KEEP | Extend bounded memory admission; do not replace architecture. |
| LivePortrait, ReActor, Wan2.1, cloud Wan UI | LEGACY → DELETE after references removed | No canonical workflow requires these backends. Preserve untracked nested checkout and weights. |
| HunyuanVideo 1.5 | KEEP temporarily | Real local generations exist; replacement is blocked by licensing/validation. |
| Wan2.2 Animate Replace | KEEP | Real 17-frame render exists. 85-frame run OOMed; hard server limits and admission headroom required. |
| Character Animate | KEEP unavailable | No validated distinct graph. |
| LatentSync 1.6 | KEEP | Real local render and manageable memory use. |

## Fresh model research

- [MiniMax H3 official weights/license](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE): downloadable weights and native audio exist. The August 2, 2026 community license excludes the USA, EU, UK and Korea. Deployment is blocked pending applicable rights/location confirmation; a Los Angeles timezone alone does not establish physical deployment location. No new H3 generation is claimed. Existing isolated files are preserved.
- [H3 inference](https://github.com/MiniMax-AI/MiniMax-H3): official local paths include ComfyUI, Diffusers, SGLang and vLLM. ComfyUI fits existing generation infrastructure; serving frameworks alone do not prove GB10 compatibility or memory safety.
- [LTX 2.5](https://huggingface.co/Lightricks/LTX-2.5): Blackwell quantization support makes it technically promising, but [license conditions](https://github.com/Lightricks/LTX-2/blob/main/LICENSE-2_x) require review for a creative-studio product. No replacement is enabled without real proof.
- [EchoMimicV3-Flash](https://github.com/antgroup/echomimic_v3) remains the Avatar proof candidate. [Independent reproducible comparison](https://github.com/tight-studio/open-source-talking-avatar-benchmark) favors its deployment balance, while LiveAvatar has stronger detail at higher memory cost. ARM64 dependencies and a real render remain gates.
- [Wan Animate-2](https://huggingface.co/Wan-AI/Wan2.2-Animate-2-14B) is newer; it has not displaced this host's validated Wan2.2 path. Specialized Character quality is evaluated separately from general video.
- [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) and CosyVoice3 merit voice comparison, but local cached weights are not a working tracked adapter. Breeze weights have noncommercial restrictions; do not imply all workflows share the source license.

## Measured safety evidence

All memory values below are GiB from host MemAvailable, not discrete VRAM.

| Workload | Configuration | Before | Lowest | Outcome |
| --- | --- | ---: | ---: | --- |
| LatentSync | 1080×1920, 52 frames | 59.8 | 40.0 | Real output; 59.5 after unload |
| Hunyuan | 512×288, 17 frames | 58.8 | 26.7 | Real output |
| Wan Character | 1280×720, 17 frames | 58.1 | 3.8 | Real output, very little headroom |
| Wan Character | 85 frames | — | — | Linux OOM; unsupported |
| MisoTTS | 6-second voice | 57.2 | 26.6 | Real output |

These are baseline observations, not fresh v0.2 acceptance results. See the [fresh acceptance report](acceptance-v0.2.md) for measured Voice, 121-frame Video, Character Replace and Lip Sync generations on the deployed branch.

## Storage audit (initial snapshot)

No weights deleted. Shared caches may be used outside NeonForge.

| Directory / group | Size | Classification |
| --- | ---: | --- |
| `/srv/ai/models/comfyui` | 96 GiB | ACTIVE plus unreferenced candidates; inventory required per file |
| Breeze / Fish / VoxCPM2 | 7.2 / 11 / 4.7 GiB | ACTIVE voice candidates |
| LatentSync / Whisper | 5.4 / 1.5 GiB | ACTIVE |
| `voice/qwen3tts`, `voice/cosyvoice3` | 6.6 / 9.1 GiB | LEGACY local installs; no tracked adapter |
| shared HF cache | 35 GiB | KEEP shared cache |
| isolated `opt/comfyui-h3` | 48 GiB | LEGACY experiment, licensing gate |
| Wan2.2 I2V high/low files | ~14 GiB each | ORPHANED candidate in NeonForge; verify other apps before deletion |

User-owned untracked diagnostic scripts, ignored service directories, nested Wan checkout, media and model weights remain untouched.

## Completed pruning

Deleted 15 tracked files: three duplicate route pages; their audio recorder, history pane and Zustand store; LivePortrait's three service files; Wan2.1's three service files; the old idle-manager script and its two systemd templates. Git retains their history. The two stopped legacy containers remain recoverable; no volumes, weights or user media were deleted.

Removed Compose services: `liveportrait`, `wan21`, `wan-ui`. Profiles now describe `voice`, `video`, `character` and `lip-sync`. F5 remains an optional Voiceover backend. Base services are frontend, gateway, Redis, supervisor and Whisper.

Removed API families: standalone F5 synthesis, LivePortrait, Wan2.1, placeholder ReActor, obsolete preset profiles and old voice/LoRA asset pickers. Existing SQLite rows and shared output history remain intact. Voiceover uses its own profile API and is preserved.

Removed configuration references: `WAN21_URL`, `WAN21_MODEL_VARIANT`, `WAN21_MAX_CONCURRENT`, `WAN21_IDLE_TIMEOUT`, `WAN21_MAX_VIDEO_LENGTH`, `WAN21_MAX_RESOLUTION`, `LIVEPORTRAIT_URL`, `LIVEPORTRAIT_IDLE_TIMEOUT`, `LIVEPORTRAIT_SOURCE_DIR`, `WAN_UI_IDLE_TIMEOUT`, `WAN_UI_URL`, `IDLE_CHECK_INTERVAL`, `IDLE_MANAGER_GATEWAY_URL`, `VOICE_ASSETS_DIR`, `LORA_ASSETS_DIR`. The owner's existing `.env` is preserved except for `LIPSYNC_IDLE_TIMEOUT`, reduced from the legacy 1800 seconds to the product's 300-second warm window. Secrets and unrelated settings are unchanged; `.env` remains untracked.

The disabled Premium Clone scaffold was removed after confirming it always returned unavailable and had no implementation. Working voice backends were not pruned without comparison. The preexisting Fish adapter identifies version 1.5; old documentation incorrectly labeled it s2-pro and has been corrected.

Historical baseline acceptance is preserved in `docs/legacy/baseline-2026-09-29.md`; current operational documentation no longer recommends removed workflows.
