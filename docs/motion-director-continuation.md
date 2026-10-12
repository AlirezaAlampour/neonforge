# Motion Director: long clips, phone flow and subscription direction

Continues `4f67300` on `motion-director-v0.1`, without merging. The tested images
are privately active at `http://100.103.129.82:3000/motion-director`.

## Real acceptance

One real **10.031-second** H.264/AAC video passed, assembled from two sequential
5-second clips at 384×672/24 fps. The extra 31 ms is audio/container padding.
Job `cf36a714-b5b9-4b87-8721-da6bbd4f755b` is in the phone gallery; its output
and durable segment records were imported without submitting another render.

| Measurement | Result |
| --- | --- |
| First/continuation H3 execution | 51.18 / 64.53 seconds |
| End-to-end acceptance | ~136.6 seconds |
| GPU utilization / temperature | Up to 96% / 51°C |
| GPU clock | At most 1989 MHz observed; existing 2000 MHz cap active |
| Lowest sampled available UMA during inference | 57.53 GiB |
| Available UMA after automatic idle cleanup | 97.28 GiB |
| Worker restoration | Active, health `ok`, no GPU claim left held |

The 90 GiB rule remains an **admission requirement before every clip**. A
running model consumes that headroom; the minimum during inference is not a
lowered admission threshold. No new H3 process, model download, first-frame
checkpoint, GPU-cap change, swap reset or system cache-drop was used. Neither
audio process was restarted in this continuation.

## Memory recovery and continuity

The memory drop after generation was retained ComfyUI/PyTorch model cache:
the installed H3 DiT (~20 GB), Qwen encoder (~15 GB), VAEs and runtime buffers
remain warm. An additional completed phone take reproduced ~58 GiB available.
Releasing only H3's idle cache restored ~97 GiB; the process itself stays running.

The private adapter's authenticated maintenance slot verifies H3/content-factory
queues, Trellis, other claims, GPU activity/cooling, the clock cap and existing
lease state before reclamation. Only the approved, verified-idle pipeline worker
may be paused; unrelated resident Ollama/Hunyuan models block admission. The
worker is restored after maintenance/rendering. Active H3 queues prohibit unload.

The UI and API offer **5, 10, 15 and 30 seconds**. Existing 2/3-second records
and API calls remain compatible. Longer generation uses 1/2/3/6 bounded clips,
each with 124 raw frames and the original safe resolution/90 GiB floor. There
is one scene plan for the entire generation, not a director call per clip.

Installed node source and live schemas were inspected before wiring continuity:
`MiniMaxH3ReferenceToVideo`, `LoadVideo`, `GetVideoComponents` and
`MiniMaxH3AddGuide`. The preceding clip's last 22 frames provide motion context;
its final image anchors frame zero of the next clip using the same reference
checkpoint/VAE. Originals, identity constraints, camera direction and timed
scene beats are preserved. The duplicated boundary frame is skipped and outputs
trimmed/joined with existing CPU FFmpeg. This is conditioning, not a guarantee
of perfectly identical pixels or exact drawn camera trajectories.

Segment intent, acknowledged prompt IDs, completion and artifact paths persist
in `motion_director_segments`. Reconciliation reuses accepted/completed clips.
Unknown acknowledgements and cancellation during submission retain the exclusive
reservation; no automatic duplicate prompt or active-job interruption occurs.

The socket directory is preserved across adapter restarts with
`RuntimeDirectoryPreserve=restart`. Live testing caught an earlier stale Docker
directory bind, refreshed the idle supervisor, and verified the directory inode
and authenticated connection survive restart. Missing supervisor host safety
status now disables Generate rather than displaying false readiness.

## Claude Code Opus

Both the existing `~/.local/bin/claude` and VS Code extension's native Claude
2.1.289 report the same `~/.claude` configuration, first-party `claude.ai` login
and **Pro subscription**. Read-only quota verification now confirms extra usage
is disabled and subscription quota is available. No auth/billing settings were
changed, provider credentials mounted into Docker or new subscriptions created.

The first planning attempt was rejected by the CLI parser **before any model
request**: streaming image input requires streaming output. The adapter now
uses stream JSON output with verbose framing and returns only the terminal
structured result, discarding other events. One independent real Opus planning
call passed: reference-specific camera/composition/identity decisions and five
validated timed beats covering ten seconds. The real H3 acceptance take used
the clearly labelled local fallback; it is not mislabeled as Opus-directed.

Generate automatically plans once. Automatic direction uses Opus when available,
with a labelled local fallback. API/provider environments, unverified/active
paid extra usage and exhausted quota still refuse Opus before execution. Tools,
MCP and settings overrides stay disabled; output, time and agent turns remain
bounded. Persisted plans are reused during rendering.

## Existing Hermes / Grok

Existing launcher: `~/pinokio/api/hermes-agent.pinokio.git/app/env/bin/hermes`.
The installed `plugins/video_gen/xai` provider uses the shared Hermes
`tools.xai_http` and `hermes_cli.auth` OAuth implementation. Its dependency lock
was checked without installing/updating packages. The plugin can also fall back
to `XAI_API_KEY`; NeonForge never enables that path or calls a Hermes chat agent.

No saved `~/.hermes/auth.json` Grok OAuth login was found. The old gateway PID is
stale, and both the local and configured Pinokio control plane are unreachable.
Therefore no Grok inference or authentication was attempted and no 403 is claimed.
The read-only connector status keeps the engine unavailable until existing
SuperGrok authorization and no-cost entitlement are verified. No duplicate
Hermes installation/profile or separate OAuth implementation was created.

Required action for live OAuth: bring up the existing Pinokio/Hermes launcher
and authorize its existing `xai-oauth` flow using **already-owned** SuperGrok
access. No new subscription or API key is requested. Phone authorization must
use that existing launcher's supported flow; there is no fabricated OAuth link
while it is offline. A phone/manual alternative is already available:
open Grok Imagine → download an MP4 → **Grok · import & compare** → import it.
This also works if future OAuth access returns 403. Original uploads are retained,
bounded/validated and converted to phone-playable H.264/AAC locally; no URL fetch
or paid inference is performed.

## Validation and rollback

176 final backend regressions pass with mocked inference, plus frontend
TypeScript/production builds and phone-sized Chromium tests. Coverage includes
all durations, one scene plan, continuity node wiring, exact CPU concatenation,
safe memory recovery, billing refusal, cancellation/uncertain acknowledgements,
manual import and original preservation. The private phone checks verify the
real 10-second preview/download, uploads/drawings, local planning, comparison,
duration/engine choices, resource status and responsive layouts. All existing
frontend routes and protected services remain healthy.

Activation uses the existing `dgx-ai-stack` Compose project, base Compose,
`docker-compose.motion.yml` and
`.motion-runtime/images/motion-long-v01-ready/images.json`, with
`up -d --no-build --no-deps supervisor gateway frontend`.
Rollback images are preserved as
`neonforge-motion-base-{frontend,gateway,supervisor}:motion-long-v01-ready`.
Private adapters/proxies, model instances, existing history/assets and unrelated
diagnostic scripts are preserved. No merge is authorized.
