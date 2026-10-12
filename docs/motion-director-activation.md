# Motion Director private activation (2026-10-12)

Continues `ad258f8` and `8592e14` on the unmerged `motion-director-v0.1` branch.
The prepared frontend, gateway and corrected supervisor are privately active
after one successful real H3 test. Original images remain tagged
`neonforge-motion-base-{frontend,gateway,supervisor}:motion-v01-ready` for rollback.
No paid model call, model download or second H3 instance was used.

## Verified networking and hardware

The installed `systemd-socket-proxyd` forwards **only**
`172.18.0.1:18189` (NeonForge's `ai-net` Docker bridge) to the existing H3 process
at `127.0.0.1:8189`. A narrow UFW rule allows TCP 18189 only from
`172.18.0.0/16` on `br-2979b412bd20` to `172.18.0.1`. Gateway and supervisor
reach H3's empty queue through this route. Trellis continues to use
`100.103.129.82:8189`; neither H3 nor Trellis was restarted or duplicated.
`scripts/setup_motion_host.sh` installs the user socket/service definitions and
prints the required firewall rule if setting up another host.

Audit: 121.7 GiB shared UMA; approximately **85.1 GiB available**, 8.3/16 GiB
swap used; GPU 0%, 32–33°C, ~5.9 W, 1989 MHz, existing 2000 MHz cap active;
CPU about 97% idle in vmstat. The substantial resident consumers are two
WirePlumber processes (~15.4 and ~14.6 GiB). H3 has ~1.0 GiB resident before
rendering; the other containers are small, and the managed NeonForge model
containers are stopped. Whisper runs on CPU/int8 and stays protected.

The owner approved restarting managed WirePlumber and pausing the pipeline worker
only when idle. Managed WirePlumber was restarted (PID 1705555 → 3545175),
recovering ~14.7 GiB; the login-session process PID 91552 was untouched and remains
running. Managed audio is active. No swap reset, system cache-drop, process kill,
threshold reduction or GPU-cap change was performed. H3 admission remains gated
by the documented **90 GiB** floor and confirmed idle/cool hardware.

## Authenticated coordination and optional Opus

`neonforge-motion-host.service` uses the existing repository uv environment and
the existing host Claude Code binary/login. It exposes only a private Unix
socket, `/run/user/1000/neonforge-motion/control.sock`. No provider credentials,
profiles, executables or unrestricted host-command API are mounted into Docker.
Random role keys live in `.motion-runtime/` with 0600 files/0700 directory and
are ignored by Git. Each container receives only its own read-only capability
key. HMAC-SHA256 binds method, path, timestamp, nonce and body digest; requests
outside role scope, modified bodies, stale signatures and replays are rejected.
The initial unauthenticated socket-mount proposal was rejected by automatic
review; this authenticated design and its preflight mounts were accepted.

The gateway role can read resource/auth status and request cinematic planning.
The supervisor role can read resources and acquire/release a bounded GPU slot
or release idle H3 cache while it owns that slot. Every managed GPU generation
joins the existing Forge3D exclusive lease. CPU Whisper remains available.
Active/queued ComfyUI, Trellis and content-factory jobs, busy Hunyuan and unknown
Ollama request activity block admission. Idle Neo services are allowlisted and
reclaimed only when needed; deliberately paused services are tracked and
restored. Shared leases renew while jobs run and persist across adapter restart;
supervisor restart adopts host claim/paused-service records. H3 queue activity
prevents premature restoration. Cached H3 models are released only when needed
for safe restoration, with an empty queue.

The approved user-unit drop-in sets `NEONFORGE_ALLOW_IDLE_EXTERNAL_PAUSE=true`.
The existing helper paused only `pipeline-worker.service` after verifying empty
queues, no current job, no resident Ollama models and no loaded Hunyuan pipeline.
It restored the worker after the render and again after idle-cache maintenance;
worker health is `ok`. No active or unrelated project job was interrupted.

Claude Code **2.1.289** is installed at `~/.local/bin/claude` and is authenticated
as `claude.ai`/first-party **Pro**. No API environment/key helper was detected.
Read-only OAuth quota verification found **paid extra usage enabled**. The
adapter therefore refuses Opus until the owner disables Usage credits/Extra
usage in Claude Settings → Usage. It checks auth, quota and disabled overflow
before each model invocation; never logs tokens/account identifiers and never
changes auth, billing, profiles or provider. An initial zero-budget CLI
validation failed before any model request because that CLI requires a positive
budget; zero-budget is not used as a false billing guarantee.

Once enabled, Opus uses one print invocation/one agent turn, tools and MCP off,
safe/restricted mode, an 80-second timeout and bounded output. Existing FFmpeg
provides small vision inputs from validated upload paths. Original images and
full vector annotations stay intact. Output decisions must satisfy schema,
timing, immutable reference IDs and hardware bounds. Plans persist in the
existing SQLite database and are reused on rendering; unchanged planning
requests do not repeat the call. Invalid/unavailable Opus produces a clearly
labelled deterministic fallback. Grok and Blender remain extension interfaces.

CLI/auth behavior was checked against the installed CLI and the official
[CLI reference](https://code.claude.com/docs/en/cli-reference) and
[subscription authentication guidance](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan).

## Tested, reviewable activation artifacts

- 164 backend regressions pass with mocked inference, including HMAC scope,
  tamper/replay protection, billing refusal, one-call plan reuse, protected
  dimensions, strict H3 floor, lease restoration and active-H3 release guards.
- Activation caught misplaced shared-lease acquisition in the Docker stats parser.
  The original supervisor was restored before inference, the guard moved before
  workload readiness, and actual status/acquisition regressions added. Read-only
  status never acquires GPU leases; both H3 and managed models acquire before load.
- The private HTTP origin lacks `crypto.randomUUID`; drawing IDs now use
  `crypto.getRandomValues` to construct UUID v4. Browser coverage disables
  `randomUUID` even on localhost to reproduce this phone-origin behavior.
- TypeScript/Next production builds pass. Phone-sized Chromium acceptance
  passes upload, pointer/touch drawing, undo/clear, persistence, multiple
  references, planning, mocked rendering, playable MP4, download and comparison.
- Prepared gateway/supervisor images import on their actual Python version and
  successfully make signed, read-only host requests through mounted sockets.
- Prepared frontend image serves `/motion-director` with HTTP 200. Its needed
  Next webpack runtime was copied from the installed dependency trace; existing
  Alpine native dependencies are preserved.

`scripts/build_motion_images.sh` reuses the exact running images and existing
Node dependencies: no npm/pip/apt/model downloads. Prepared tags:
`neonforge-motion-{frontend,gateway,supervisor}:motion-v01-ready`.
The image override is
`.motion-runtime/images/motion-v01-ready/images.json`. The production build's
gateway URL is the existing `http://gateway:8000`; it is not the mock test URL.
`docker-compose.motion.yml` supplies authenticated role mounts/private H3 route.

## Real acceptance and activation

One single-image reference test used the existing `older_man.png`, local
Professional Cinematic Director, a subject marker and the installed ref2va
checkpoint/4-step LoRA. It passed upload → persisted plan → supervisor/shared
reservation → existing H3 → retrieval → MP4 validation. Admission was 99.4 GiB.

- Job: `e49f0c44-8ad6-4dcc-bffb-b72e2a10dfa1`.
- H3 prompt: `4bf5910e-2417-4ddc-895c-6bbe12799c06` (submitted once).
- H3 execution: 46.19 seconds; full acceptance/retrieval/probe: 53.2 seconds.
- H.264 video/AAC audio, 384×672, 24 fps, 56 frames, 153175 bytes.
- Requested 2-second draft is padded by the inspected graph to 2.333 seconds.
- Sampled peak GPU: 96%, 44°C, maximum 1989 MHz; minimum sampled available
  UMA during rendering: 58.36 GiB. The 90 GiB requirement is an admission floor.
- Completed artifact/metadata were imported into existing gateway storage;
  this did not resubmit inference. The phone gallery contains this real take.
- Idle H3 cache retained ~41 GiB afterward. A private exclusive maintenance
  claim verified all queues idle, released only H3 cache, and restored the idle
  pipeline worker. Available UMA recovered to ~97.6 GiB for the next admission.
- Final live private-HTTP Chromium acceptance at 390×844 passed upload,
  mouse/touch drawings, undo/clear, persisted vector data, local planning,
  actual H.264 playback/download and resource status. Desktop width also passed;
  Opus/Grok were disabled, no page errors occurred and no extra render was sent.
  All eight existing frontend routes returned 200; frontend/gateway/supervisor,
  Redis and CPU Whisper remained healthy. Both WirePlumber processes and the
  restored pipeline worker were verified running, with no GPU claim left held.

Activation uses the existing Compose project and no builds during promotion:

```sh
docker compose -p dgx-ai-stack -f docker-compose.yml -f docker-compose.motion.yml \
  -f .motion-runtime/images/motion-v01-ready/images.json \
  up -d --no-build --no-deps supervisor gateway frontend
```

Live phone URL: `http://100.103.129.82:3000/motion-director` → preview/download
the completed take, or upload a GPT reference → prompt → Plan shot → Generate
H3 video. Local planning works; Opus remains disabled until Claude extra usage
is off and read-only subscription checks pass. Grok/first-frame mode stay gated.
No merge is authorized. Preserve the floor/cap and recheck queues before any
additional heavy test. Prepared backups and unrelated files remain intact.
