# Daidyra verification and animation acceptance

Base: main `2735f1c`. Work performed October 20, 2026 (America/Los_Angeles).

## Claude verification

The previous host serialized checks under a lock and cached every outcome for
60 seconds. UI capability requests every 20 seconds (and additional quality,
validation and planning callers) kept rechecking forever once that cache expired.
The upstream 429 path discarded Retry-After and reported “wait a minute”.
This was an endpoint-verification failure, not evidence of exhausted model usage.
Gateway logs show concurrent desktop/phone capabilities requests; a static search
found one application caller of the OAuth usage endpoint. Historical upstream
request counts were not logged, so the original exact count cannot be recovered.

One real read-only check at 22:17:44 PDT received HTTP 429 and Retry-After 1654
seconds (27m34s). Next allowed check: 22:45:18 PDT. The old one-minute retry was
therefore much too early. New status polling is passive; verification is explicit
or needed by a fresh plan. Concurrent callers share a cancellation-shielded task.
Success is valid for five minutes; refusal never inherits a stale authorization.
429 backoff doubles from 5 to 80 minutes, respects longer numeric/date Retry-After,
and persists through host restart. Sanitized attempt records show when probes
actually happened. No token/account/auth output is written to those records.
API/provider environment checks and disabled-extra-usage requirements remain.
Existing saved plans, reconstruction and local camera edits need no Claude check.

Provider guidance explicitly distinguishes rate-limited usage verification from
work limits: https://code.claude.com/docs/en/costs#when-the-usage-request-fails .
This does not authorize bypassing the existing billing safeguard with stale data.

Phase 1 validation: 242 Python tests; TypeScript check. New concurrency tests use
31 simultaneous callers, caller cancellation, passive polls, restart cooldown,
numeric/date/malformed Retry-After, and expired success. Private evidence lives
in `.motion-runtime/daidyra/`, including `actual-verification.json` and test logs.

## Authored animation proof

Official free Standard Quaternius Universal Animation Libraries 1 and 2 are
CC0. The included GLBs retain only selected authored actions and their referenced
binary data (2.9 MiB combined); `animation_assets/manifest.json` records input/output
SHA256 and source URLs. `scripts/prepare_character_library.py` reproduces them
from the original ZIPs. Native matching rest skeletons avoid the distortion risk
of name-only retargeting; arbitrary user-rig retargeting is not supported.
Blender uses independent armatures/meshes, source actions in NLA with crossfades,
opponent facing independent of travel, and source-sampled ankle IK during blends.
The first render exposed quaternion/euler facing and a crouch transition foot arc;
both were fixed and visibly re-rendered, not just asserted in schema tests.

The explicit local five-second fight study shows approach, left jab/head reaction,
right counter/chest reaction, then a right cross/crouch dodge and return to guard.
This fixture is authored for acceptance, never a disguised Claude fallback.
120 evaluated frames retain camera margins, ground contact and synchronized
reaction cues. Hit-joint distances are 0.112/0.283 m and dodge clearance 0.597 m;
max camera speed is 0.05 m/s. These are approximate joint tests, not simulation
of body surface collisions or proof of general interaction capability.
A failed contact/framing report prevents a completed preview and H3 admission.
New planning must select only catalog clips and explicit ordered action/contact
cues. No compatible kick or unarmed block asset is bundled; neither is fabricated.
Library source/actions: https://quaternius.com/packs/universalanimationlibrary.html
and https://quaternius.com/packs/universalanimationlibrary2.html . NLA workflow:
https://docs.blender.org/manual/en/latest/editors/nla/properties_modifiers.html .

Phase 2 tests: 252 Python tests. Private CPU preview MP4, editable `.blend`,
`transforms.json`, contact sheet and `timeline-validation.json` are retained under
`.motion-runtime/daidyra/` and the scene ID named in `study-result.json`.

## Handoff correction (pending H3 visual acceptance)

Legacy draft passed only the opening 22 frames (0.917s) to H3, omitting later
choreography. Authored interactions now supply the full real-time 124-frame
window at a bounded 384x672 motion canvas, plus original image identity inputs
and a first-frame appearance guide. Existing non-interaction draft settings stay
unchanged for comparison. No resolution or UMA/temperature floor was reduced.

## Daidyra creation interface

Visible branding/metadata now use Daidyra; Motion Director remains the workflow
name. Existing repository, deployment, storage and localStorage identifiers stay
unchanged. A restrained gold labyrinth mark and thread/grid accents frame the
workspace. Desktop uses reference/action and preview columns; phone uses a full
width workspace with horizontal navigation. Advanced tools remain optional.
Compatible saved scenes reconstruct automatically after restore or quality/canvas
changes, with a bounded one-attempt guard and an explicit retry on failure.
Incompatible prompt/reference changes explain the new-planning requirement.
Upload, scene corrections and take actions display errors beside their controls.
Claude planning/next verification time and H3 resource readiness are separate.
The character timeline lists each actor's timed clips and links the evaluated
contact report. New sessions start with a five-second fast draft.

Staging Playwright inspected real desktop (1440x1000) and phone (390x844) views:
Daidyra title/navigation, no browser errors, no document overflow, usable controls,
and full-width mobile content. Screenshots: `staging-desktop.png` / `staging-phone.png`.
Optimized Next build, TypeScript and Python checks passed before activation.
