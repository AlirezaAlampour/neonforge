# NeonForge v0.2 state

Base: `neonforge-v0.2`. Current feature branch: `motion-director-v0.1`,
unmerged. Updated 2026-10-19.

Motion Director now connects original reference images → existing Claude Code Pro
vision/structured scene specification → trusted Blender Python compiler → actual
rigged 3D animation and independent camera → preview/corrections → H3 reference
video conditioning → paired output comparison. This uses the existing ARM64
Blender and H3 processes, with no new project, model service or Hermes profile.

Real browser acceptance passed for a running character with a camera behind it,
including a natural-language correction from four to six metres. A real
10.031-second H3 output (240 H.264 frames, AAC, two clips) passed. Every sampled
Blender environment transform stayed stationary, but the final AI video moves
bystanders and drifts in distance/framing; reference conditioning does not enforce
exact choreography. Reusable clips are stylized; realistic mocap, detailed image
reconstruction and complex rigged interactions remain unsupported.

The H3 draft gate requires 90 GiB before each bounded clip. Historical inference
memory stayed ≥68.96 GiB and clocks ≤1989MHz under the existing 2000MHz cap. Qwen
was temporarily unloaded with explicit owner approval and restored with its
original context/pinned residency after verified-idle H3 cleanup. Resident Qwen
continues to block H3 admission; no automatic model eviction was added.

The quality follow-up separates Opus's Blender scene from its resolved H3
appearance context, adds optional AI Shot Plan editing and reference-derived
character/environment geometry, and introduces a 768×1344 native profile with
105 GiB per-clip admission. The 384×672 draft and 90 GiB minimum are preserved.
See the [resolution and blur trace](docs/motion-quality/AUDIT.md) and
[A/B/C/D measurements and visual limits](docs/motion-quality/RESULTS.md).

Forge3D GLB retrieval/import and an actual orbit render passed. The optional uv
`blender` group supplies a missing OpenBLAS library only to Blender's subprocess.
Existing installations remain untouched. The Hermes plugin uses the xAI API even
with subscription OAuth, so automatic Grok stays disabled. Exported MP4/PNG/prompt
and manual import/comparison are supported; a real Grok browser render remains
untested. See [audit](docs/motion-3d/AUDIT.md),
[workflow and limitations](docs/motion-3d/WORKFLOW.md) and
[real verification](docs/motion-3d/VERIFICATION.md).

Primary surfaces: Voiceover, Video, Character, Avatar, Lip Sync, System Info.
Voiceover UI is preserved. Video, Character and Lip Sync have focused input workspaces, shared large results and compact settings. The three duplicate creator routes are deleted.

Active media paths include HunyuanVideo 1.5, Wan2.2 Animate Replace, LatentSync 1.6 and the privately validated H3 reference-to-video slice; applicable model terms still apply. EchoMimicV3-Flash has an unresolved ARM64 dependency lock and no real host render; Avatar is unavailable. Character Animate remains unavailable pending a distinct validated graph.

The existing supervisor now enforces bounded frame/resolution admission and checks warm containers. Character's 17-frame preview requires 64 GiB, Hunyuan requires 40–60 GiB by configuration, Miso requires 40 GiB based on measured consumption, and LatentSync requires 32 GiB. Protected services and unrelated workloads cannot be reclaimed.

LivePortrait, Wan2.1, ReActor placeholder, old preset APIs, cloud Wan UI Compose definition and the unused host idle manager are removed. Old containers and weights remain recoverable. User diagnostic scripts and nested checkouts are untouched.

See [real acceptance measurements](docs/acceptance-v0.2.md), [model matrix](docs/models.md), [audit and storage](docs/pivot-audit.md), and [historical baseline](docs/legacy/baseline-2026-09-29.md).
