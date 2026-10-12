# Motion Director v0.1

Feature branch: `motion-director-v0.1`. No production services, profiles,
credentials or model installations were changed. The two existing untracked
diagnostic scripts remain untouched.

## Implemented slice

`/motion-director` inside the existing Next.js frontend accepts up to four
PNG/JPEG/WebP/BMP references (20 MB each), a cinematic prompt, Auto/Manual
Director, a reusable Professional Cinematic Director preset, and portrait or
landscape 2/3/5-second drafts. SVG annotations include arrows, paths, orbit
markers, ordered waypoints, subjects, entry and exit markers, labels, undo and
clear. Original image bytes remain in the existing ComfyUI asset store.
Normalized vector data and the draft persist in browser storage, can be edited
after refresh, and can be downloaded as JSON. Submitted shot plans and vector
data persist in the existing SQLite database, in an additive table.

The deterministic local planner validates references and geometry and produces
camera direction/targets, timed beats, composition, identity/action constraints,
staging, dimensions, frame count and an H3 prompt. Drawings are **prompt guidance**.
Dense paths retain all editable vertices while their text guidance samples
eight points, bounding annotation growth in the H3 text encoder.
Exact camera paths are not claimed. Fun ControlNet nodes were inspected, but a
validated size-matched control-video producer is outside this slice.

Rendering reuses the existing gateway heavy semaphore and supervisor claims.
The new external H3 policy cannot start/stop any H3 process/container, reclaim
other services, or participate in idle cleanup. Enabled external H3 queues also
guard admission of other managed GPU workloads. Prompt acknowledgements and
job status persist; accepted jobs reconnect after gateway restart without
resubmitting inference. Lost submission acknowledgements retain the reservation
for inspection. Completed MP4s are streamed from H3's SaveVideo node into the
existing output store and history; the page previews, downloads and compares
two takes. An explicit reconcile action retries retrieval/tracking, never inference.

`ShotPlanner`/`HermesDirector` and the renderer protocol/registry are extension
boundaries. Grok is always unavailable in v0.1 and makes no external calls.
Hermes is not invoked and no state/profile/credential changes are made.

## Host audit and real-render blockers

- H3 responds on **host** `127.0.0.1:8189`; its queue was empty. It is a host
  Python process under `~/opt/comfyui-h3`, separate from the unrelated Trellis
  container bound to the same port on the host's Tailscale address. Do not route
  NeonForge to that Trellis listener.
- The running NeonForge gateway/frontend use private Tailscale ports 8080/3000.
  Gateway and supervisor containers cannot reach host loopback. Set the same
  `H3_COMFYUI_URL` in both only after verifying a private route to this exact
  existing H3 process. A host-gateway alias alone does not expose a loopback
  listener. The default blank setting preserves current service behavior.
- The live schemas and existing `blockout-director` API workflow were inspected.
  Reference-to-video weights, Qwen encoder, video/audio VAEs and the 4-step
  reference LoRA are present. Single- and multi-image reference graphs are ready.
  The installed first-frame blueprint instead requires
  `minimax_h3_fl2va_pruned_int8_convrot.safetensors` and
  `minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors`; these are **absent**.
  Exact image-to-video is gated in both UI/API. No weights were downloaded.
- Existing `blockout-director/docs/INCIDENTS.md` records two H3 host power-offs
  and specifies a 90 GiB memory floor, an active 2000 MHz clock cap and no other
  heavy GPU workload. `gpu-clock-cap.service` was active. Audit MemAvailable was
  approximately 85.5 GiB, below the documented floor; no real H3 test was run.
  The supervisor enforces 90 GiB plus bounded 384×672/672×384 and 56/73/124 frames.
  Owner verification of clock cap and other GPU workload safety remains a
  prerequisite for enabling the route. Automatic service stops are excluded.
- Existing `~/.hermes` state/config is present and references Grok/xAI, but a
  working `hermes` launcher was not found in PATH, `~/.local/bin`, the state bin,
  uv tools, or installed user/system Hermes units. Checked content-factory unit
  templates reference a stale `/home/alireza` installation. There is no
  `~/.hermes/auth.json`; Grok OAuth video access is **unverified**, not inferred
  from a provider string. Only presence/boolean facts were emitted; no secrets
  were printed. Supply the actual existing launcher before implementing Hermes
  orchestration. Do not create another installation or profile.

## API

All routes share `/api/v1/motion-director` and the existing Next.js proxy:

| Route | Purpose |
| --- | --- |
| `POST /references` | Reuse existing image upload/storage |
| `GET /references/{id}` | Original image preview |
| `POST /plan` | Validate and generate structured shot spec |
| `GET /capabilities` | H3 models/workflows, scheduler, memory, Grok/planner gates |
| `POST /jobs` | Replan server-side, reserve existing H3, submit once |
| `GET /jobs` / `GET /jobs/{id}` | Persistent progress, plan, preview/download |
| `POST /jobs/{id}/reconcile` | Track an already acknowledged prompt; never resubmit |

## Verification and activation

Use `uv lock --check --offline` before executing project code. All Python
commands use `uv run --locked --no-sync`; existing Node dependencies are reused
through `uv run ... node`, without package installation.

Backend: run the full pytest suite with `PYTHONPATH=gateway:.` and temporary
`HISTORY_DB_PATH`, `OUTPUTS_ROOT`, `ASSETS_ROOT`. Tests mock all inference and
cover upload integrity, vector validation, planning, both correctly weighted
workflow graphs, scheduling, memory/bounds, errors, uncertain acknowledgements,
output retrieval, preview/download and unchanged existing routes.

Frontend: installed TypeScript and Next production build; the browser script
`frontend/scripts/test-motion-director.cjs` uses an already-installed Playwright
module/Chromium (`PLAYWRIGHT_MODULE`, `CHROMIUM_PATH`) and a localhost production
frontend at `MOTION_TEST_URL`. `tests/motion_browser_gateway.py` is a loopback-only
mock gateway on 18081 with temporary storage. Its video fixture is a tiny CPU
FFmpeg MP4 at `/tmp/neonforge-motion-ui-20261012.mp4`; no GPU/model is contacted.
The browser check covers phone pointer/touch annotations, undo/clear, draft
persistence, multiple references, planning, mocked rendering, decodable video,
download, comparison, disabled Grok, and mobile/desktop overflow.

Acceptance: 151 backend tests pass (mocked inference), TypeScript and Next.js
production build pass, and the browser test passes at 390×844 with touch and
1440×1000 desktop. The existing private gateway `/readyz` reports ready with
Redis, and the production Video/Voiceover pages still respond. The browser
preview is a CPU-generated mock fixture, not a real H3 acceptance render.

No merge or production deployment is performed while the real host route and
render safety prerequisites remain unresolved. Before activation, verify the
private H3 route from both existing containers, at least 90 GiB UMA availability,
the existing clock cap and absence of competing GPU work. Then promote tested
frontend/gateway/supervisor artifacts together and run one 2-second single-image
**reference-to-video** acceptance render. Do not start a second H3 or install
the absent first-frame weights. Production Next.js must be rebuilt with the
existing `http://gateway:8000` internal URL; the browser-test build embeds its
mock loopback URL and must not be deployed.

After activation, from the phone's existing Tailscale connection open
`http://100.103.129.82:3000/motion-director`, choose a GPT reference image, enter
a prompt, leave Auto Director/2 seconds/vertical/reference-to-video selected,
tap **Plan shot**, review the plan, and tap **Generate H3 video**. Currently the
production frontend remains unchanged and this route has not been deployed.
