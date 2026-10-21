# Generate failure: deployed reproduction and fix

Base: main `308cc54`. Investigation and acceptance: 2026-10-20 on the existing
private NeonForge deployment. No model, worker architecture or product-name change.

## Actual HTTP 409 reproduction

Playwright restored recorded plans and browser preview state, clicked the real
Generate button, and captured the outgoing JSON and server response. H3 was idle,
ready, about 110.5 GiB available and 35°C. The following requests were rejected
before H3 submission:

| Restored plan | HTTP response | Proven saved-state defect |
| --- | --- | --- |
| `c5778687-3489-48c8-8c8a-c899813f50f7`, desktop | 409: saved plan needs scene/canvas correction | Native request stored 384×672; camera edit also inherited a different prompt and reference set |
| `7e2abcad-bb11-40ee-8fad-71b2253c4d89`, mobile | 409: saved plan needs scene/canvas correction | Draft dimensions were correct, but the camera scene belonged to a different prompt/reference set |
| `853e5a08-f522-4246-b5bd-eee327adb275`, mobile | 409: wait for Blender preview | localStorage claimed completed; the server preview was missing |

All three buttons were enabled. After scrolling to Generate, the error was outside
the viewport at the page top. These are captured requests/responses, not inferred
causes from access logs. Separately, older stored request hashes omit defaults now
present in the schema; unchanged parsed requests must not be mistaken for edits.

## Focused changes

- Generate checks the authoritative saved request, dimensions, compatibility and
  physical server preview before enabling. Submission repeats the same checks.
- Canonically parsed requests support older schema defaults without trusting old
  hash strings or stale localStorage preview completion.
- One-click rebuild clones compatible saved scene data under a new plan ID,
  preserves original plans/artifacts, applies draft/native dimensions, validates
  geometry/timing and builds/reuses the CPU preview. It never invokes Claude.
- A changed prompt/reference set cannot be reinterpreted by copying old geometry.
  Such plans show an explicit new-planning requirement beside Generate, including
  the existing subscription availability reason. No safeguard was bypassed.
- Camera revisions synchronize visible controls and the submitted plan. Quality
  changes invalidate readiness while retaining scene data for rebuilding.
- A synchronous click guard plus persisted acknowledgement/idempotency receipt
  prevents double clicks and restored sessions from creating duplicate jobs.
- Submission/rebuild failures appear beside Generate. A temporary verification
  failure offers another check. Retry stays unavailable until worker cleanup ends.

## Real deployed acceptance

The browser exercised restored incompatible plans, an older hash, native → draft
→ native rebuilds, a new CPU Blender camera scene, reused takes and session reloads.
A preview was temporarily hidden only for the newly created test scene between
validation and submission: the real server returned 409, the file was restored in
finally, and the error appeared beside Generate. No job was created by that error.

One actual draft job, `01b2909b-8b1b-4ca0-999f-243f7408ec85`, received HTTP 202
while Redis was stopped and progressed to H3 execution. Redis was restored; a
controlled gateway restart recovered the first clip without resubmitting it.
That test exposed the cleanup/retry race, now covered by a regression. After
fixing it, the mobile retry returned 202 and only the unsubmitted second clip ran.
Both segments completed: `6b9a647f-9cbc-4fe1-b82d-64cf4c63770a` and
`84b1d53c-7c6f-4a2f-91a1-603ffe4f6b9f`. The video probes as 384×672, 24 fps,
10.031 seconds. Recorded resource samples peak at 57°C, stay under 2000 MHz and
observe at least 70.71 GiB available UMA. Sampling does not establish instantaneous
peaks. No native H3 benchmark was run.

Database ID-set comparison proves exactly one new generation job. H3 history
contains the two unique segment client IDs; the original completed first clip is
retained. Historical jobs/output files and unrelated container IDs/states match
the pre-session snapshot. Original four interrupted takes were not restarted.
The Claude invocation log is unchanged; fresh subscription planning remains
rate-limited. Wrong-story/reference scenes require fresh planning when available.

238 Python tests pass locally. TypeScript, optimized frontend build and Compose
validation pass. Deployed images: `motion-generate-fix-v2` gateway/frontend;
supervisor and external H3 are unchanged. The browser matrix was repeated after
deployment. Mobile lost-ack replay uses the original key and must return the same
completed job with HTTP 202; it cannot submit another H3 job.

Private evidence: `.motion-runtime/generate-fix/` contains the online DB backup,
`rejections.json`, original request/response traces (`browser-requests-live.json`),
post-deployment matrix traces, screenshots, `worker-resume.json`, `accepted.json`,
`result.json`, `resources.json`, the actual MP4 and build/test logs.

Recheck with verified `uv.lock` and the existing dependencies:

```sh
bash scripts/verify_motion_release.sh
MOTION_GENERATE_REPLAY=1 uv run --locked --no-sync node scripts/verify_motion_generate_browser.cjs
```

`MOTION_GENERATE_LIVE=1` permits one resource-gated real draft only if there is no
previous acceptance record; otherwise reconcile that accepted job. It must not
be used to repeatedly submit fresh native/draft benchmarks.
