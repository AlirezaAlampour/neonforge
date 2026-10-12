# NeonForge v0.2 state

Base: `neonforge-v0.2`, based on tested MisoTTS `994fc92`. Current feature branch:
`motion-director-v0.1`, unmerged. Updated 2026-10-12.

Motion Director is privately active at `http://100.103.129.82:3000/motion-director`.
The longest real validated H3 output is **10.031 seconds**, 384×672 H.264/AAC,
two continued 5-second clips. The phone flow is upload → describe → optional
drawing → duration/engine → Generate, with automatic professional direction.
5/10/15/30-second outputs use bounded sequential clips, motion context and a
boundary-frame guide; original images and editable drawings stay separate.

Retained H3 model cache caused post-render availability to fall to ~58 GiB.
Verified-idle cleanup now restores ~97.3 GiB automatically before another clip
and after generation. Every clip still needs 90 GiB available; the existing
2000 MHz cap remains. The real test reached 96% GPU/51°C and restored the idle
pipeline worker. Both audio processes were preserved in this continuation.
The private socket directory now survives adapter restarts without stale mounts.

Claude CLI and VS Code share the existing Pro login. Extra usage is now verified
off; one real Opus scene plan passed after fixing the CLI streaming format.
Automatic planning uses Opus once when available and local direction otherwise.
Hermes's existing Pinokio xAI video plugin was found, but its launcher is offline
and no Grok OAuth is saved. Grok generation stays unavailable; manual MP4 import/
compare works. 176 backend regressions, frontend builds and phone/browser checks
pass. No paid API calls, models, subscriptions or profiles were added. First-frame
weights and Blender remain unactivated. See [continuation evidence and actions](docs/motion-director-continuation.md)
and [initial activation evidence](docs/motion-director-activation.md).

Primary surfaces: Voiceover, Video, Character, Avatar, Lip Sync, System Info.
Voiceover UI is preserved. Video, Character and Lip Sync have focused input workspaces, shared large results and compact settings. The three duplicate creator routes are deleted.

Active media paths include HunyuanVideo 1.5, Wan2.2 Animate Replace, LatentSync 1.6 and the privately validated H3 reference-to-video slice; applicable model terms still apply. EchoMimicV3-Flash has an unresolved ARM64 dependency lock and no real host render; Avatar is unavailable. Character Animate remains unavailable pending a distinct validated graph.

The existing supervisor now enforces bounded frame/resolution admission and checks warm containers. Character's 17-frame preview requires 64 GiB, Hunyuan requires 40–60 GiB by configuration, Miso requires 40 GiB based on measured consumption, and LatentSync requires 32 GiB. Protected services and unrelated workloads cannot be reclaimed.

LivePortrait, Wan2.1, ReActor placeholder, old preset APIs, cloud Wan UI Compose definition and the unused host idle manager are removed. Old containers and weights remain recoverable. User diagnostic scripts and nested checkouts are untouched.

See [real acceptance measurements](docs/acceptance-v0.2.md), [model matrix](docs/models.md), [audit and storage](docs/pivot-audit.md), and [historical baseline](docs/legacy/baseline-2026-09-29.md).
