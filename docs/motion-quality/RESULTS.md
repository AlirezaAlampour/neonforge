# Running-reference quality comparison — 2026-10-19

Branch `motion-director-v0.1`; baseline commit `138add8`. See the
[pre-change trace](AUDIT.md) for actual codecs, preprocessing, conditioning and
generation parameters. No upscaler or new checkpoint is involved.

## What changed

The existing authenticated Claude Code Opus vision integration expands the
simple prompt **“Character running and camera following.”** into two validated
outputs: metric Blender geometry/rig paths/camera/light and a separate six-part
H3 reference context. The H3 context resolves corrections without appending old
directions or passing mannequin implementation language. Following means a
camera behind the moving subject unless the user requests a leading view.
The optional editable AI Shot Plan is outside Advanced; the original prompt
stays unchanged. Its edits use the existing natural-language revision endpoint.

Blender now has reusable train/window/door, building, canopy and sign geometry,
reference colors and character proportions, hair/satchel proxies, smooth rigged
parts and improved framing. Semantic train/building roots are ground anchors.
Every vertex, including split cylinder caps, is weighted. Independent character
rigs and existing read-only Forge3D GLB retrieval remain available. CPU analytic
directional shading provides legible control footage without photorealistic
modeling. Single-image depth is an Opus estimate, not measured depth or exact
reconstruction; no new depth model is installed.

H3 retains both the original image and one Blender video. Native mode keeps the
image at a 1344px long edge, supplies 124 real-time motion frames at a bounded
384×672, and uses the installed AddGuide node to anchor the original image at
the first frame. Later clips retain their preceding generated boundary image.
The original frame supplies appearance; Blender supplies motion/layout only.
Native generation and Blender preview are 768×1344, 24fps; raw and trimmed H.264
use CRF16. Draft remains 384×672 with the prior short motion budget. Native mode
requires 105 GiB at admission and before each clip; the existing 90 GiB floor
is preserved. Resident models are never automatically evicted by the product.
Native admission also waits for an idle GPU at50°C or below under the same
exclusive lease, including between segments, without changing clocks or fans.

Only the existing Ref2VA INT8-convrot checkpoint, NVFP4/AWQ Qwen encoder,
INT8-convrot video VAE and four-step v0.1 Turbo LoRA are used. The installed H3
process reports **PyTorch attention, PyTorch2.14.0+cu130**, with dynamic weight
offloading; its DiT calls the existing optimized attention path. No attention
kernel or vendor stack was replaced. Adding steps to a four-step distilled
LoRA is not assumed to improve quality. FL2V/8-step and ControlNet weights are
absent. Depth/edge/pose/mask passes are therefore not exported as purported
controls. A styled proxy first frame was rejected in favor of the real image;
the model must not inherit mannequin appearance.

## Actual A/B/C/D experiment

Original running image, segment seeds42/43, two bounded five-second segments,
24fps, ten-second assembled output throughout:

| Case | Changed group | Native pixels | Runtime | Min available shared UMA |
| --- | --- | --- | --- | --- |
| A | Stored baseline | 384×672 | ~143.9s* | 68.96 GiB |
| B | Resolved Opus H3 plan; old scene footage/settings | 384×672 | 138.69s | 69.307 GiB |
| C | B plus improved Blender scene/framing | 384×672 | 139.61s | 70.106 GiB |
| D | C plus full motion, original first-frame guide, native canvas, CRF16 | 768×1344 | 843.087s successful H3 / 942.41s total active | 66.116 GiB |

*A's historical monitor starts after the submission acknowledgment; runtime is
not a controlled speed comparison. B–D include uploads, inference, retrieval and
assembly, excluding one-time Opus/Blender preparation. D changes several related
options together; this experiment does not isolate each one's causal effect.
The first D attempt completed segment1, then the benchmark interrupted segment2
at70°C while memory and clocks remained within their bounds. That attempt
overlapped the CPU packaging build. After verified terminal interruption and
cooling, only segment2 was explicitly resumed with seed43 and the saved native
boundary frame. No build ran during the resumed segment. The interrupted prompt,
history and samples are retained. Successful H3 execution time is separated from
total active time including the interrupted attempt and excludes offline cooling.
B/C peaks were61/62°C; the stopped D attempt reached70°C and the resumed segment
peaked at69°C. Every case stayed at or below1989MHz. D's peak shared-memory
consumption delta from admission was43.522GiB; this includes host effects and is
not a dedicated per-process VRAM measurement. Native105GiB admission passed at
about109GiB before both successful segments.
Shared UMA comes from `/proc/meminfo`, not a dedicated VRAM counter. The existing
2000MHz GPU cap and other services/models remain protected. The owner-authorized
temporary Qwen unload is benchmark-only, followed by verified-idle cache cleanup
and restoration of its original model/context/pinned residency.

## Visual findings and limits

Actual raw and final clips and six native PNGs per case are inspected, rather
than inferring quality from test counts. Compression is a minor measured
contributor: baseline raw-to-trim PSNR43.817dB and SSIM0.989753. Native canvas and
reference preprocessing limit spatial detail; generation already changes
identity detail and environment over time before export. Quantization and
distillation may contribute, but their individual effects are not isolated.
Native raw-to-trim SSIM0.992385 and PSNR46.827dB indicate little additional loss
during that export stage; CRF16 cannot recover details
that generation has already lost.

B alone still lets the runner recede. C keeps a much more constant subject size
and recognizable cream clothing/dark satchel through the clip. The train remains
on the right. All 240 improved Blender transforms hold the requested follow
distance at 3.600000±0.000001m, aim-vector error below2.9e-7, and all six background
roots remain stationary. H3 still moves commuters, changes signs/clothes and
omits small reference objects. The generated Opus scene includes train, canopy,
sign, towers and two commuters but omits the flying cat, alien and cube. Later
planning instructions explicitly require retaining recognized static props;
that improvement is not claimed as part of the recorded C/D scene.
D retains clearer cloth folds, satchel markings, train panels and wet-floor
reflections at native resolution. Its first-frame view transitions behind the
runner, then maintains a similar close follow composition through the segment
boundary. Fine costume details, signs and bystander behavior still change.

Approximate manual head-to-lowest-sole bounds at0.5/9.5 seconds on the same
384×672 display grid give A431→141px (33% retained height), B439→212px (48%),
and C365→354px (97%); D442→401px (91%). Bounds have about±10px uncertainty per edge and change with
gait/occlusion; this is a screen-scale follow proxy, not measured world distance
or a facial identity score. Coordinates are saved in `visual-review.json`.

| Native path | Actual delivered values |
| --- | --- |
| Blender preview | 768×1344, 240 frames,24fps,10.000s, H.264/yuv420p CRF16,3.390Mbit/s |
| Motion reference | Downsampled384×672,124 frames,24fps,CRF16; original-image reference remains separate at1344px long edge |
| H3 raw first segment | 768×1344,124 frames,24fps,5.167s, H.264 CRF16,13.625Mbit/s |
| Final D | 768×1344,240 frames,24fps,10.031s, H.264/yuv420p,10.665Mbit/s; trim CRF16 and concat video copy, no export resizing |

768×1344 is the highest validated native canvas in this experiment, matching the
installed local base's target. It costs about14 minutes of H3 inference per ten
seconds on this capped Spark. Higher untested canvases are not presented as a
practical improvement; the separate hosted2K module is absent. No upscaler was
introduced. Larger scenes and native30-second output were not benchmarked.

Temporal differences and Laplacian detail are measured at the same downsampled
192×336/4fps grid in `measurements.json`. These depend on scene motion, contrast
and subject scale: C's closer runner increases frame differences. They are not
identity scores or proof that flicker improved. Exact H3 camera distance, locked
backgrounds and deterministic choreography remain unsupported by reference-video
conditioning. Rear views also limit face-identity assessment.

## Artifacts and reproduction

Comparison directory:
`.motion-runtime/scene-outputs/motion_director/scenes/02c445d2-7d35-4498-b25e-d03ebfc033ef/`

It contains `comparison.html`, A–D MP4s, native PNG samples under `frames/`,
display-reduced contact sheets, both Blender previews, per-case validated plans,
accepted H3 graph/history, admission/memory/clock/temperature samples and
`measurements.json`. Display contact sheets are reductions, never generation
upscales. No benchmark secretly resubmits an uncertain accepted prompt.

Verify `uv lock --check --offline`, then use only the existing uv environment:

```sh
uv run --locked --no-sync python scripts/benchmark_motion_quality.py --release-qwen
uv run --locked --no-sync python scripts/analyze_motion_quality.py
uv run --locked --no-sync node scripts/verify_motion_quality_browser.cjs
```

Explicit reviewed recovery used `--only D --resume-verified-interruption` after
the original stopped prompt was verified terminal with `execution_interrupted`.
The default command refuses any prior accepted/uncertain incomplete prompt.

`--release-qwen` requires the owner's existing explicit unload/restore consent;
it only acts on `qwen3.6:35b-a3b`. The browser check replays the actual benchmark
plan and intercepts the edit response to test UI wiring without new inference;
the preview and final MP4 decoding use real artifacts.

Primary model documentation: [local native768p guidance](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/README.md),
[reference IR/video guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md),
[Turbo sampler contracts](https://github.com/ModelTC/Minimax-H3-Turbo/blob/main/COMFYUI_SETUP_AND_INFERENCE.md).
The hosted2K module is separate and not installed. 768×1344 is the validated local
native target; larger canvases are not claimed to be tested or useful.
The real reference benchmark is portrait; the transposed landscape profile has
the same pixel budget but was not separately benchmarked.

## Verification and local deployment

- `uv lock --check --offline` passed; no dependencies/weights were installed.
- 204 Python regressions passed, including native admission/recheck/cooling,
  retained image+video+boundary references and camera revision intent.
- TypeScript and the production Next.js build passed using existing dependencies.
- Live mobile/desktop browser checks passed for the optional editable plan,
  unchanged simple prompt, real native Blender/H3 decoding and synchronized
  A/B/C/D playback. Edit-response replay is explicitly identified in the script.
- The classic UI regression also passed upload, pointer/touch annotation,
  undo/clear, persistence, multi-reference, mocked H3, download and comparisons.
- Actual Opus planning, CPU Blender rendering and all final A/B/C/D clips were
  exercised separately against the existing authenticated/model integrations.

The existing private frontend, gateway and supervisor are healthy on images
`neonforge-motion-{frontend,gateway,supervisor}:motion-quality-v01-native`.
Only those three Compose services were recreated with `--no-deps`; unrelated
running/stopped services were preserved. The same H3 host process remains alive
(pid2722). The host adapter was restarted only between verified-idle claims.
Qwen is restored with262144 context and pinned residency; H3 correctly remains
blocked while that model is resident. Existing Hermes/Forge3D/auth are untouched.
The branch is unmerged and unpushed; prior images are retained for rollback.

[Private comparison page](http://100.103.129.82:3000/api/v1/outputs/motion_director/scenes/02c445d2-7d35-4498-b25e-d03ebfc033ef/comparison.html)
contains the saved clips, contact sheets and measurement links. Native PNGs and
mobile/desktop screenshots remain alongside them in the comparison directory.
