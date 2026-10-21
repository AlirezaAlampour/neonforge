# Quality bottlenecks and preserved behavior — 2026-10-20

Before changing quality settings, this session inspected the existing A/B/C/D
and iteration artifacts, measured runtimes, accepted graphs and raw-to-trim
PSNR/SSIM. Those results identify generation/conditioning and framing as the
remaining identity/background limits. Compression is a small contributor;
longer continuity text previously failed to show a robust improvement. No
prompt expansion, checkpoint swap, sampler change or native rerun was justified.

Two concrete plan-reuse defects were found in the live ledger and code:

- Cached plan `c5778687-3489-48c8-8c8a-c899813f50f7` requested **native** but
  stored **384×672**. Local camera revision copied a draft parent's canvas
  over the requested native profile. Planning/revision now retains the requested
  768×1344 native or 384×672 draft dimensions. Bad caches are rebuilt from
  eligible saved scenes; changed story intent requires fresh planning; submission/execution refuses inconsistent canvases.
- A camera-only revision could retain a parent after the user changed the main
  story prompt, silently reusing the wrong action. The live fighting request
  inherited a running scene. Local edits now require unchanged scene semantics;
  the UI clears obsolete revision lineage when the prompt/reference changes.
  Known stale local-edit caches are excluded from scene reuse. Changing the
  story requires an appropriate new plan; an unchanged camera edit still costs
  zero new Claude invocations. Historical plans and videos remain intact.

Regression tests exercise draft-to-native camera edits, inconsistent old cache
repair and changed-story intent. Existing tests continue checking original image
references, motion footage, native appearance guide, continuation boundary image,
stationary background roots, camera distance and scene identity under local edits.
The existing 90/105 GiB admissions, 40 GiB emergency runtime floor, 2000 MHz cap,
70°C stop and native idle cooling remain enforced.

A separate real ten-second **draft** acceptance used the existing saved scene and
references. It was accepted while Redis was stopped; its first clip completed.
A real capabilities bug (`releasable: null` under an active exclusive lease)
then stopped the sequence before the second inference. After fixing that bug,
mobile explicit retry reused the first saved clip and submitted only the second.
The final job `9082b4f7-3d86-4c14-974e-cde90a0a2acf` has exactly two acknowledged
H3 prompts, both completed; generation made zero new Claude calls. The video
probes as 384×672, 24 fps, 10.031 seconds. Recorded samples peak at 56°C with
clocks below 2000 MHz; the observed available UMA low is about 71.1 GiB.
Sampling is not a proof of the exact instantaneous peak.

The acceptance draft exposed stale running-scene inheritance rather than proving
the fighting direction was followed. Its frames retain broad costume/platform
identity but show changing signs, bystanders and small props. This is reliability
and decoding evidence, **not a controlled improvement in facial identity,
background retention or fighting choreography**. The new intent guard prevents
that silent reuse for future plans. No new visual consistency gain is claimed.
Prior measured camera/framing/native improvements remain in the integrated work.

Full-resolution outputs, frame samples, browser captures, accepted IDs, resource
samples and the original saved plans remain in `.motion-runtime/stabilization/`.
Four old interrupted generations were never restarted. Their inconsistent or
stale plans should be reused/reviewed and repaired before an explicit new render.
