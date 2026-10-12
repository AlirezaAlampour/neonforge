# Motion Director activation preparation (2026-10-12)

Continues `ad258f8` on `motion-director-v0.1`. Production frontend, gateway and
supervisor have not been replaced. Their original image IDs and behavior remain
unchanged. No H3 render or paid model call has been made in this continuation.

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

The managed WirePlumber service is PID 1705555 (at audit time); the separate
login-session process is PID 91552. Restarting the managed audio service may
briefly interrupt desktop audio and is **pending owner approval**. The second
process is not a target. No swap reset, cache-drop, process kill, threshold
reduction or GPU-cap change is performed. Actual H3 inference remains gated
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

The existing helper would pause `pipeline-worker.service` and restore it after
the lease. It currently reports no active job/queue, no Ollama models and no
loaded Hunyuan pipeline. Cross-project pausing is **pending owner approval**;
`NEONFORGE_ALLOW_IDLE_EXTERNAL_PAUSE=false` remains in the host unit. No other
project has been paused or unloaded. If approved, use a user-unit drop-in to set
that flag to true and reload only this idle adapter; do not reinstall the helper.

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

- 162 backend regressions pass with mocked inference, including HMAC scope,
  tamper/replay protection, billing refusal, one-call plan reuse, protected
  dimensions, strict H3 floor, lease restoration and active-H3 release guards.
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

## Next authorized sequence

1. Obtain the pending audio-restart and idle cross-project coordination approvals.
   Disable paid Claude extra usage before enabling optional Opus.
2. Restart only the approved managed audio service; verify it is running again,
   availability is at least 90 GiB, GPU cap/cooling hold, and every relevant
   generation queue/claim is idle. Do not touch the login-session audio process.
3. Enable only the approved idle-coordination flag. Recheck live billing/auth;
   keep local planning available if Opus remains blocked.
4. Use the prepared, tested supervisor to protect the real short acceptance
   job, preserving its original image for rollback. Run **one** 2-second,
   single-image reference-to-video H3 job through the existing process with
   telemetry and verify the MP4 and restoration. Do not download first-frame
   weights, start a second H3, bypass 90 GiB or interrupt unrelated active work.
5. Only after successful real acceptance, promote the three prepared images
   together using the existing Compose project `dgx-ai-stack`, base Compose,
   authenticated motion overlay and image override (`--no-build --no-deps`).
   If acceptance fails, return the control plane to its original image and
   leave production unchanged. No merge is authorized yet.
6. Verify actual phone upload/planning/progress/MP4 playback on the private
   frontend and recheck existing workflows, queues and restored worker health.

Currently live phone URL: `http://100.103.129.82:3000/status` (existing production).
After activation: `http://100.103.129.82:3000/motion-director` → upload a GPT
reference → prompt → Plan shot → Generate H3 video. The feature route is not
live yet; no mock preview is presented as a real H3 result.
