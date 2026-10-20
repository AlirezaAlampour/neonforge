# Focused iteration audit — 2026-10-19, before changes

Branch `motion-director-v0.1`, baseline `5e71329`. The existing native/draft
benchmarks and installed graph were inspected before changing inference.

## Actual Opus usage

`planned_shot` makes one authenticated host request and one `claude -p`
invocation for a fresh plan or revision. There is no application retry or paid
fallback. The print invocation requests one bounded turn, but that flag does
not prove one provider HTTP request: structured output/internal CLI behavior
can use additional model responses. Hidden provider retries are not measured.
Subscription verification uses a separate auth-status CLI command and a cached
read-only quota request; those are not video-planning model calls.

Exact matching requests return a persisted plan with zero new Opus invocations.
Every generation segment reuses that plan and makes zero Opus calls. Seed and
quality are currently part of the full request hash, so changing them causes an
unnecessary new plan. Every camera correction also invokes Opus even when only
a distance/height/lens changes. Rendering never asks Opus to plan again.

The CLI terminal result exposes usage/timing when supplied by the installed
CLI, but `plan_with_opus` currently discards it. Stored plans and prior benchmark
JSON contain no token totals. Historical per-generation input/output totals,
planning wall time and internal provider request/retry counts cannot be
reconstructed honestly. Do not estimate tokens from text length or infer them
from the number of tests/segments. This uses the existing Pro subscription,
with paid extra usage verified disabled; per-request API pricing does not apply.

## Quality and runtime baseline

The same-reference A/B/C/D results in `RESULTS.md` remain the baseline. Native
768×1344/24fps uses the existing Ref2VA INT8-convrot model, four-step Turbo LoRA,
NVFP4/AWQ Qwen encoder, INT8 video VAE, PyTorch attention, original image and
124-frame Blender reference at384×672. First-frame and continuation guides are
connected. Draft384×672 uses22 motion frames. No missing control weights are
assumed available. No sampler, checkpoint, step count or output size needs to
change to improve planning/camera/iteration.

Native ten-second H3 inference took843.087s; draft139.61s end-to-end. Native
minimum free shared UMA66.116GiB, maximum clock1989MHz under the existing2000MHz
cap. One attempt was interrupted at70°C; its resumed segment peaked69°C. Keep
90GiB draft and105GiB native admission,40GiB runtime benchmark guard, native
idle start≤50°C, and70°C benchmark stop. Query memory via `/proc/meminfo`.

The current Blender camera is recomputed directly from instantaneous character
heading every frame. Quaternion sign compatibility prevents flips but provides
no damping, acceleration control or independent follow rig. Curves are sampled
in Python; follow mode ignores explicit camera paths. Straight paths are already
stable, so a turn/acceleration fixture is needed to test smoothing fairly.

The native Blender preview renders240 full-resolution PNGs then encodes them,
even though H3 motion conditioning uses384×672. Cache identity is tied to the
plan UUID and does not include compiler version or asset content. Identical
scene revisions cannot share rendered artifacts, and cached camera changes can
leave H3 text inconsistent if reused without rebuilding its spatial contract.

Appearance/geometry can change during an Opus revision despite an intended
camera-only edit. In H3, stationary objects and exact costume detail still drift;
reference conditioning is not strict identity/trajectory control. This pass
must separately assess preserved scene data and actual generated frames.
