# NeonForge v0.2 state

Branch: `neonforge-v0.2`, based on tested MisoTTS `994fc92`. Updated 2026-09-30.

Motion Director v0.1 is implemented on `motion-director-v0.1` (2026-10-12),
without production deployment. See [implementation, host audit and activation
blockers](docs/motion-director-v0.1.md): private routing to the existing host H3,
90 GiB render safety, missing first-frame weights, and unverified Hermes launcher/
Grok OAuth. Its reference-to-video slice is verified with mocked inference.

Continuation: private H3 networking now works through Docker-bridge port 18189.
Authenticated host resource/Claude adapters and immutable planning cache are
prepared; 162 regressions and phone/mock/image checks pass. Production remains
unchanged pending audio-memory recovery and idle cross-project coordination
approval, 90 GiB admission and real H3 acceptance. Claude Pro is verified but
paid extra usage is enabled, so Opus execution is refused. See
[activation preparation](docs/motion-director-activation.md).

Primary surfaces: Voiceover, Video, Character, Avatar, Lip Sync, System Info.
Voiceover UI is preserved. Video, Character and Lip Sync have focused input workspaces, shared large results and compact settings. The three duplicate creator routes are deleted.

Active media paths remain HunyuanVideo 1.5, Wan2.2 Animate Replace and LatentSync 1.6. H3 awaits applicable licensing and isolated proof. EchoMimicV3-Flash has an unresolved ARM64 dependency lock and no real host render; Avatar is unavailable. Character Animate remains unavailable pending a distinct validated graph.

The existing supervisor now enforces bounded frame/resolution admission and checks warm containers. Character's 17-frame preview requires 64 GiB, Hunyuan requires 40–60 GiB by configuration, Miso requires 40 GiB based on measured consumption, and LatentSync requires 32 GiB. Protected services and unrelated workloads cannot be reclaimed.

LivePortrait, Wan2.1, ReActor placeholder, old preset APIs, cloud Wan UI Compose definition and the unused host idle manager are removed. Old containers and weights remain recoverable. User diagnostic scripts and nested checkouts are untouched.

See [real acceptance measurements](docs/acceptance-v0.2.md), [model matrix](docs/models.md), [audit and storage](docs/pivot-audit.md), and [historical baseline](docs/legacy/baseline-2026-09-29.md).
