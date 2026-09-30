# Changelog

All notable NeonForge changes are recorded here. The project has not published a tagged release.

## Unreleased

### Product

- Reduced primary navigation to Voiceover, Video Generation, Character, Avatar, Lip Sync, and Utilities & Status.
- Preserved the Voiceover Studio UX and hid duplicated legacy creator routes from navigation.
- Rebuilt Video Generation around HunyuanVideo 1.5 and Character around the managed Wan2.2 Replace graph.
- Kept Character Animate and Avatar disabled until their distinct runtimes pass real generation.
- Replaced the legacy lip-sync adapter with LatentSync 1.6.

### Resource lifecycle

- Added an allowlist-only, claim-based supervisor resource manager using `/proc/meminfo`.
- Added automatic idle/conflict reclamation, targeted Compose start/stop, readiness checks, failure cleanup, and configurable warm windows.
- Added workflow-specific cold-start minimums, runtime duration/UMA metrics, and lifecycle visibility in Utilities & Status.
- Protected frontend, gateway, Redis, supervisor, Whisper, unrelated containers, and arbitrary host processes from reclamation.
- Disabled automatic ComfyUI restart so OOM and lost prompts remain visible.

### Models and validation

- Installed and validated the two missing DWPose preprocessors for Character.
- Completed real DGX renders with Wan2.2 Character Replace, LatentSync 1.6, and HunyuanVideo 1.5.
- Added an ARM64 inference patch and uv-locked service environment for LatentSync while preserving NGC PyTorch.
- Selected EchoMimicV3-Flash for Avatar, but did not integrate it because the official TensorFlow/decord dependency set is not reproducible on Linux ARM64.
- Documented current Hunyuan, Wan, LTX, MiniMax, LatentSync, MuseTalk, EchoMimic, LiveAvatar, LongCat, SoulX, and AptAvatar research and license boundaries.

### Engineering

- Added focused tests for candidate reclamation, protected services, admission retry, failure cleanup, workflow state, workflow-specific minima, and navigation deduplication.
- Converted the supervisor, gateway, and LatentSync package installation paths to uv-only locked builds.
- Added a bounded ComfyUI queue-to-history grace to prevent successful renders being marked failed.
- Verified the preserved Voiceover path with a real MisoTTS render through the new supervisor claim lifecycle.
- Fixed the frontend workload-status proxy so Lip Sync on-demand readiness and lifecycle diagnostics render correctly.
- Updated deployment, architecture, model, current-state, troubleshooting, and acceptance documentation.
