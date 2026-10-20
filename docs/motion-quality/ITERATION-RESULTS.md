# Motion Director iteration pass — 2026-10-19

Branch `motion-director-v0.1`, preceding implementation `5e71329`.
The [pre-change audit](ITERATION-AUDIT.md) was completed before inference work.
No checkpoint, attention implementation, sampler, steps, quantization, export
CRF, frame rate or final generation canvas changed in this pass.

## Observed Opus usage

One fresh, real plan through the existing authenticated Claude Code integration:

| Exposed observation | Actual value |
| --- | --- |
| Application CLI invocations / application retries | 1 / 0 |
| Total planning / CLI / reported API time | 35.509 / 34.971 / 34.742 seconds |
| CLI-reported uncached input tokens | 2 |
| CLI-reported cache creation / cache read input tokens | 7,932 / 0 |
| CLI-reported output tokens | 3,519 |
| Observed assistant message IDs / reported turns | 1 / 2 |
| Provider requests / internal retry totals | Unknown |
| Per-request API cost | Not applicable: existing Pro subscription |

The two uncached tokens are **not** the total input: cache creation is reported
separately. No token counts are estimated from text. CLI turns are not provider
requests. Existing extra usage was disabled; applying public API prices or the
CLI's hypothetical API-equivalent cost to this subscription would be misleading.
Earlier saved plans contain no token metadata, so historical usage stays unknown.

Allowlisted observations are saved with an application invocation ID in the host's
private usage JSONL and the plan. Failed/timed-out calls retain unknown usage
where the CLI supplies none. Raw events, prompts, account IDs, stderr and
credentials are not logged. Optional collapsed usage details appear in the
existing Advanced plan area and saved takes. Rendering reports zero new Opus
invocations and labels inherited planning measurements as saved provenance.

Live endpoint checks measured saved-scene reuse at 0.036s, seed/quality reuse
at 0.017s and a camera-only revision at 0.018s: **zero new CLI invocations**,
verified against the unchanged usage log. Recognized local camera edits preserve entity,
appearance, lighting and world geometry data exactly. Mixed or ambiguous edits
still use the existing Opus path; there is no new API or fallback provider.

## Camera and iteration changes

Blender now uses a baked dolly and independent aim target with a tracking
constraint, critically damped heading/focus, and explicit world paths. Monotone
Hermite entity paths respect stationary intervals; stopped characters retain
their heading. Constant-speed straight running retains the requested 3.6m
horizontal follow distance within floating point tolerance. Damping allows small
distance variation during turns rather than forcing an instantaneous camera jump.

CPU previews use the actual 384×672 motion-conditioning canvas. Final native H3
generation stays **768×1344**, draft **384×672**, both 24fps. The UI displays
preview and generation dimensions separately. Existing native preview artifacts
remain immutable and downloadable. No old video is upscaled into a new result.

| Same ten-second scene | Wall time | Blender invocations |
| --- | --- | --- |
| Previous full native preview, frozen preceding compiler | 108.575s | 1 |
| New cold bounded motion preview | 42.142s | 1 |
| Cross-plan/quality cache reuse | 0.045s | 0 |

The cold native-workflow preview is 61.2% faster because it renders the motion
canvas actually consumed downstream. This does not make H3 inference faster.
Draft cold rendering remains roughly 44–47s. Cache signatures include normalized
scene, dimensions, duration, compiler source hashes and selected GLB contents.
New plan IDs can share completed render artifacts; prompts and export scripts
are rebuilt per plan. No model or other service is loaded for a cache hit.

## Controlled visual comparison

The running reference and preceding optimized scene specification are reused;
the newly observed Opus plan is a separate usage measurement, not a changed
geometry confound. Segment seeds are 42/43, two five-second segments, ten seconds
assembled, identical model/sampler/steps and native/draft settings. Before clips
are preserved C/D outputs from the preceding controlled experiment. After changes
are the camera compiler/motion preview. An additional experimental continuity
prompt was evaluated and rejected; the deployed H3 prompt/graph is unchanged.
Reference image, Blender motion and native first/continuation guides stay wired.

The rejected draft continuity trial completes in 139.61s → 151.85s end-to-end, minimum free
shared UMA 70.106 → 71.151GiB, both peaking at 62°C and 1989MHz. Inference is
not faster. The first accepted graphs differ only in the motion-video filename,
continuity text and output filename; model, sampler, steps and seed are identical.

Both draft clips retain the cream costume, dark diagonal strap, double satchel,
rear follow view and train on the right. Signs, small costume details and
bystanders still change, and reference props are not reliably retained. The
sampled frames do not demonstrate a robust appearance-consistency improvement.
The new common-grid detail proxy rises 0.13100 → 0.14864, while frame difference
rises 0.11098 → 0.11692. Motion/contrast/content affect both: neither proves
better identity or less flicker. Rear views do not establish facial identity.

The first native attempt was interrupted at the unchanged 70°C guard before
segment 1 completed. Its terminal `execution_interrupted` history, telemetry
and runtime are saved separately. A verified recovery uses a stricter 40°C idle
start before each segment; it changes no inference setting, clock or fan.
The cooler recovery also hit 70°C before completing segment 1. Both attempts
are terminal, with zero completed native segments, minimum free shared UMA
67.083GiB and maximum clock 1989MHz. Recorded time including the interruption
and conservative cooling is 705.07s; there is no successful new native runtime
or clip to claim. Qwen was restored after each verified-idle cleanup.
The previously validated 768×1344 native output and working configuration are
preserved; native generation remains thermally constrained under current
conditions. No inference or temperature limit was relaxed to complete a test.

The extra continuity text is removed because the draft trial showed no clear
appearance gain and slower inference. `after-draft.mp4` is explicitly labeled
as the rejected trial. `final-draft.mp4` tests the shipped camera/preview changes
with the preceding H3 prompt and settings. No new appearance-conditioning
method or identity model is claimed. The main appearance-consistency gain is
preserving the existing production specification across local camera and
seed/quality revisions; generative appearance drift remains unresolved.

The final draft completes in **139.50s**, versus 139.61s baseline: effectively
unchanged, with 133.956s successful H3 execution versus 133.046s before. It is
384×672, 240 frames at 24fps, 10.031s assembled, H.264/yuv420p at 1.942Mbit/s.
Minimum free shared UMA is 71.035GiB, consumption delta from admission 39.597GiB,
peak temperature 65°C and maximum clock 1989MHz. Its accepted first graph differs
from baseline only in motion-video and output filenames; even the prompt is
identical. All these generation tests invoke Opus zero times.

Final-frame inspection retains the recognizable cream costume, satchel, boots
and train, with continued fine-detail and background drift. Its opening tilted
three-quarter view transitions to rear follow and differs from baseline; strict
H3 camera adherence is not demonstrated. The final detail proxy is 0.13344 and
translation-compensated frame difference 0.11024 versus 0.13100/0.11081 before.
Those small descriptive differences do not establish an appearance improvement.
The final runtime supports restoring the simpler preceding prompt rather than
shipping the slower unproven continuity addition.

The same turn/stop fixture is rendered with the frozen preceding compiler and
current compiler, with the path in open space to avoid train occlusion. Its
purpose is to measure camera discontinuities and path overshoot; it is separate
from the straight-running H3 appearance comparison. Both full control videos,
240-frame transforms and six sample PNGs per case are saved.

| Turn/stop control measurement | Before | After |
| --- | --- | --- |
| Largest camera displacement in one 24fps frame | 7.198725m | 0.244271m |
| RMS camera position third difference | 1.145605m/frame³ | 0.002871m/frame³ |
| Character root drift after authored stop | 0.444445m | 0m |

The third difference falls 99.75% on this fixture; this combines the path and
rig changes, not an isolated damping-only comparison. After-turn horizontal
follow distance varies 3.5514–3.6257m while the rig settles. Saved frames show
the prior heading reset switching to a front view after the stop; the new rig
retains the rear follow view. This demonstrates Blender control stability,
not a promise that H3 will follow the camera exactly. No collision avoidance or
natural stop-pose blending is claimed by this camera fixture.

## Artifacts, checks and deployment

[Private before/after comparison](http://100.103.129.82:3000/api/v1/outputs/motion_director/scenes/857fca76-8d58-4eb3-a840-6674a849b28e/comparison.html)
contains draft comparison clips, the preserved native baseline, display-reduced contact sheets, original-size sample PNGs,
camera control videos and measured telemetry. Shared UMA is read from
`/proc/meminfo`; it is not a dedicated per-process VRAM measurement.

Reproduction uses only the existing verified uv environment:

```sh
uv lock --check --offline
uv run --locked --no-sync python scripts/benchmark_motion_iteration.py --run draft --release-qwen
uv run --locked --no-sync python scripts/analyze_motion_iteration.py
uv run --locked --no-sync node scripts/verify_motion_iteration_browser.cjs
```

`--release-qwen` requires the owner's existing temporary unload/restore consent;
only `qwen3.6:35b-a3b` is eligible. Incomplete accepted benchmarks are never
silently resubmitted. Keep 90GiB draft and 105GiB native admission, 40GiB runtime
guard, native idle start at or below 50°C, benchmark stop at 70°C and the existing
2000MHz GPU cap. Packaging builds run before GPU benchmarks.
The rejected trial's exact accepted graphs and interrupted native histories
are preserved; its extra prompt is deliberately absent from the shipped code.
Optional new native testing uses `--run native` and remains subject to the same
thermal guard; no completed new native comparison is implied by these commands.

210 Python regressions, TypeScript and the production frontend build pass.
Live mobile/desktop checks cover optional observed usage, explicit preview/final
dimensions, and the existing editable plan/legacy native playback. UI usage is
an explicit replay of the separately measured real invocation; it does not
submit new inference. Local deployment uses
`neonforge-motion-{frontend,gateway,supervisor}:motion-iteration-v02` with
`--no-deps`; the host adapter was restarted only with an idle queue/lease.
Hermes authentication, Forge3D and unrelated services remain untouched.
Final checks confirm all three services healthy, H3 still at pid2722 with an idle
queue, no exclusive claims, the 2000MHz cap active, and Qwen restored with its
262144 context and pinned residency. Every unrelated container retains its
original ID and running/stopped state. Prior images are retained for rollback.
No merge or push is authorized or performed.
