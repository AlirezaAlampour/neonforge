# NeonForge v0.2 state

Branch: `neonforge-v0.2`, based on tested MisoTTS `994fc92`. Updated 2026-09-30.

Motion Director v0.1 is privately activated from `motion-director-v0.1`
(2026-10-12), without merging. One real local H3 reference-to-video test passed:
384×672 H.264/AAC, requested 2 seconds (56 padded frames, 2.333 seconds),
46.19 seconds of H3 execution. The real take is available for preview/download
at `http://100.103.129.82:3000/motion-director`.

The owner-approved managed WirePlumber restart recovered ~14.7 GiB; the separate
login-session audio process was untouched. H3 admitted at 99.4 GiB, observed
96% GPU utilization/44°C, and retained the 2000 MHz cap and 90 GiB admission
requirement. The verified idle pipeline worker was paused under the shared lease
and restored. H3 idle cache was released under a maintenance lease to recover
~97.6 GiB for the next render. 164 backend regressions and frontend build pass.
Private HTTP drawing UUID compatibility and shared-lease acquisition were fixed
during activation. Claude extra usage remains enabled, so Opus stays disabled;
the local planner works. First-frame weights, Hermes/Grok and Blender remain
unactivated. See [activation evidence](docs/motion-director-activation.md).

Primary surfaces: Voiceover, Video, Character, Avatar, Lip Sync, System Info.
Voiceover UI is preserved. Video, Character and Lip Sync have focused input workspaces, shared large results and compact settings. The three duplicate creator routes are deleted.

Active media paths include HunyuanVideo 1.5, Wan2.2 Animate Replace, LatentSync 1.6 and the privately validated H3 reference-to-video slice; applicable model terms still apply. EchoMimicV3-Flash has an unresolved ARM64 dependency lock and no real host render; Avatar is unavailable. Character Animate remains unavailable pending a distinct validated graph.

The existing supervisor now enforces bounded frame/resolution admission and checks warm containers. Character's 17-frame preview requires 64 GiB, Hunyuan requires 40–60 GiB by configuration, Miso requires 40 GiB based on measured consumption, and LatentSync requires 32 GiB. Protected services and unrelated workloads cannot be reclaimed.

LivePortrait, Wan2.1, ReActor placeholder, old preset APIs, cloud Wan UI Compose definition and the unused host idle manager are removed. Old containers and weights remain recoverable. User diagnostic scripts and nested checkouts are untouched.

See [real acceptance measurements](docs/acceptance-v0.2.md), [model matrix](docs/models.md), [audit and storage](docs/pivot-audit.md), and [historical baseline](docs/legacy/baseline-2026-09-29.md).
