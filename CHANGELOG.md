# Changelog

All notable NeonForge changes are recorded here. The project has not published a tagged release yet.

## Unreleased — recommended `0.1.0-alpha`

### Changed

- Made Voiceover the default landing workflow and simplified navigation around creator goals.
- Renamed B-Roll to Video Generation and Creative Studio to Character.
- Moved LivePortrait, the older F5 surface, and ReActor behind Legacy/experimental disclosure.
- Added normalized Ready, Loading, Disabled, Missing model, Runtime error, and In use service states.
- Added strict readiness preflight for the legacy Lip Sync and LivePortrait adapters.
- Profile-gated optional voice, legacy-media, ComfyUI, Wan, and cloud-experimental services.
- Defaulted public bindings to loopback and retained shared model/cache/output mounts.

### Documentation

- Rebuilt the README around real supported workflows, deployment, safety, and licensing.
- Added installation, model/runtime, and troubleshooting guides.
- Recorded the LatentSync 1.6 and LongCat-Video-Avatar 1.5 evaluation without claiming unsupported integrations.
- Added real application screenshots and a concise Mermaid architecture diagram.

### Engineering

- Added a root `uv.lock`, deterministic frontend lockfile, and lightweight CI.
- Switched the frontend image to `npm ci`.
- Updated Next.js to the supported 16.3.6 line after the 14.x dependency audit still reported critical advisories.
- Added CPU-safe tests for workflow navigation and media readiness behavior.
- Removed verified root-level scratch artifacts and obsolete scripts that overwrote Dockerfiles or installed crash-derived dependencies at runtime; expanded generated/model exclusions.
