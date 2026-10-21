# NeonForge product and maintenance proposal

Audit: 2026-10-20. This is a non-destructive proposal, not a model migration.
Evidence: current routes/Compose/adapters/tests; retained SQLite history; live
Motion acceptance; [measured media acceptance](acceptance-v0.2.md) and
[controlled H3 comparisons](motion-quality/RESULTS.md). History is incomplete
telemetry: missing records do not establish that a model is unused.

| Component | Classification | Evidence and next action |
| --- | --- | --- |
| Motion Director | **Keep / Improve** | Eight retained completed H3 histories before this session; real Opus/Blender/H3 artifacts. Keep the simple reference → prompt → plan → preview → generate → review flow. Durable recovery is now implemented. |
| General Video / HunyuanVideo 1.5 | **Keep / Consolidate** | Four completed retained generations, including measured 121-frame video. Keep as the proven text-to-video backend; share take/history presentation with Motion later. Do not replace it solely because H3 is newer. |
| Voiceover Studio, cloning, profiles | **Keep / Improve** | F5 has ten retained history records; Breeze and other voice evidence also lives in separate acceptance artifacts. Preserve user profiles, recordings, chunking, save-profile flow and all working adapters. Compare the same scripts/references before selecting defaults. |
| Breeze / F5 / Fish 1.5 / VoxCPM2 / Miso | **Keep pending comparison** | Capabilities and costs differ; an old checkpoint is not proof of redundancy. Record intelligibility, speaker likeness, languages, direction, latency and UMA with identical inputs. Fish UI/docs must continue identifying the installed 1.5 adapter correctly. |
| Qwen3-TTS / CosyVoice3 stopped containers and cached weights | **Deprecate candidate as unconnected runtimes** | Not in current tracked Compose services or Voiceover selection. Upstream supports useful voice capabilities, but a cached checkpoint is not a verified replacement. Preserve weights and other branches; compare in isolation before consolidation. |
| Character Replace / Wan2.2 | **Keep / Improve** | Five completed and ten failed template rows; existing tested limit is 17 frames with 64 GiB admission. Retain specialized replacement and honest limits. These counts are observations, not a controlled failure-rate estimate. |
| Character Animate tab | **Deprecate candidate UI placeholder** | Distinct upstream Animate and Replace modes exist; only Replace is validated locally. Keep disabled until its own graph and render pass, or hide the empty tab after product approval. |
| Avatar | **Deprecate candidate UI placeholder** | No enabled backend or verified host render. EchoMimicV3-Flash remains an isolated proof candidate; upstream availability does not resolve ARM64/dependency acceptance. Do not advertise a working avatar workflow. |
| Lip Sync / LatentSync 1.6 | **Keep** | Two retained completed histories and measured 1080×1920 acceptance. Keep input bounds and readiness checks. Source video size does not imply the model internally processes full-frame 1080p. |
| B-roll | **Consolidate** | `/broll` is already absent; represent B-roll as a Video/Motion use case, not another app or route. |
| Shared GPU / UMA / host lease | **Keep / Improve** | One supervisor admission authority, external H3 queue inspection, authenticated Forge3D lease, thermal/clock/memory bounds. Keep Docker lifecycle authority out of the gateway. Add cross-service restart/lease evidence before broader changes. |
| Docker services | **Consolidate** | Four control-plane containers plus on-demand model services and existing external H3. Old stopped LivePortrait/Wan UI/Wan2.1/Cosy/Qwen containers are inventory candidates, not garbage to remove automatically. |
| UI routes | **Consolidate** | Current seven routes work; `/studio`, `/voice`, `/broll` return 404. Consider three creator groups: Scenes (Video + Motion), Voice, Performance (Character + Lip Sync), with System Info separate. No new editor or application. |
| Docs/scripts/tests | **Improve / Consolidate** | Preserve historical benchmark reports as dated evidence; correct active installation/architecture docs. Consolidate repeated browser assertions and diagnostic entry points around the release verifier. Remove dead Premium Clone branches only after callers/tests are mapped. |

## Current upstream capability checks

Checked official sources on the audit date, without installing or upgrading:
[H3](https://github.com/MiniMax-AI/MiniMax-H3) documents multimodal reference
workflows; [HunyuanVideo 1.5](https://github.com/Tencent-Hunyuan/HunyuanVideo-1.5)
remains an available local model. [Wan2.2](https://github.com/Wan-Video/Wan2.2)
documents separate animation and replacement preprocessing.
[VoxCPM2](https://github.com/OpenBMB/VoxCPM),
[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS), and
[CosyVoice3](https://github.com/QwenAudio/CosyVoice) expose overlapping but distinct
cloning/design capabilities. [EchoMimicV3](https://github.com/antgroup/echomimic_v3)
has Flash weights; [LatentSync](https://github.com/bytedance/LatentSync) documents
1.6 at a 512×512 training resolution. None of these statements proves GB10
performance or justifies replacing a working installed backend.

## Proposed architecture and cleanup sequence

Keep Next.js + FastAPI + SQLite + supervisor + existing model adapters. Extend
the proven Motion durable ledger pattern to other job types in a separately
reviewed milestone; Redis should remain disposable observation/cache state.
Keep the external H3 process and managed ComfyUI separate until real coexistence
measurements justify sharing a runtime. Consolidate product navigation and
saved-take presentation before changing model execution.

1. Export inventory of container/image IDs, model paths/hashes, workflow references,
   voice-profile dependencies and branch/worktree owners. Preserve online DB backups.
2. Mark unsupported routes/backends in docs; hide approved empty UI placeholders.
   Map dead imports and callers before removing small unreachable code branches.
3. Run matched voice comparisons and a single bounded Avatar proof separately.
   Choose defaults from evidence; retain alternate profiles until equivalent.
4. After integrated deployment and rollback are verified, propose removal of
   specific stopped containers/images and **internal builder cache only**. Obtain
   review of the exact inventory first. No volume prune or bulk system prune.

No models, weights, datasets, outputs, voice profiles, volumes or unrelated code
were removed. Hermes, Forge3D, all feature branches and the separate iPhone
worktree remain outside this cleanup. Measured storage includes 96 GiB ComfyUI,
16 GiB voice, 11 GiB Fish, 7.2 GiB Breeze, 5.4 GiB LatentSync and 4.7 GiB VoxCPM;
these are shared assets, not proposed deletion sizes.
