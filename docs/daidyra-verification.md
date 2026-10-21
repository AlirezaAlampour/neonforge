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

## Matched H3 comparison and selected handoff

Three real five-second H3 drafts used the same original reference image, seed 42,
384x672 generation canvas, 124 output frames, four sampling steps and identical
shot-plan text. The rendered Blender videos have identical SHA256. No prompt
length experiment or model/checkpoint change was involved.

| Trial | Motion reference | Extra first-frame guide | Real result |
| --- | --- | --- | --- |
| `af5a53ec-10ab-4303-94c7-da3b305f01ee` | Opening 22 frames | No | Best original alien face and character count; readable fighting, approximate timing |
| `57f70a15-62ed-462a-bb7e-206db089da5b` | Full 124 frames | Yes | Fight transfers; alien face becomes a stylized mask |
| `f5061e4b-32e0-4c6e-89d6-1bf3604e126b` | Full 124 frames | No | Face drift and extra alien-like figures remain |

Full proxy-video conditioning competes with image identity for this nonhuman
character. Removing the extra pose guide alone did not solve it. The final draft
therefore retains the appearance-preserving opening-window recipe; native keeps
its existing full-motion/appearance-guide contract. The complete validated
Blender timeline remains available in every scene preview. Draft H3 interprets
later choreography from the shot plan; it does not enforce every previewed pose.
This is a measured limitation, not a claim that full motion improved identity.

All trials visibly fight and retain tunic, tracksuit, train on the right, wet
platform, neon lighting and winged cat. H3 changes faces, hands, exact contact,
background people and some object detail. Blender itself has approximate geometry
and generic humanoid faces. A prior saved “fight” video actually ran a stale
running scene with another reference; it is shown only as context, not a matched
control. The new genuine Blender animation is a clear improvement over that
running-only reconstruction. General realistic interaction support is unproven.

All three MP4s are 384x672, 24 fps, about 5.03s. Recorded resource samples stayed
above 69.5 GiB available UMA, at or below 61°C and 1989 MHz. These samples do not
establish instantaneous peaks. Admission remained at 90 GiB; emergency stop
thresholds remain 40 GiB and 70°C. No other service was manually stopped.

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

## Fresh Claude planning and final verification

After the enforced cooldown, the saved OAuth token returned HTTP 401: its local
expiry was October 19, even though `auth status` still said logged in. The owner
refreshed it with the official `claude auth login --claudeai` flow. Real Pro
verification then returned ready with paid extra usage disabled. Expired-token
checks now explain that action without hitting the usage endpoint. No credential
refresh workaround, API billing, OpenRouter or provider fallback was used.

Three fresh planning attempts exposed invalid guard blending or box-style
character dimensions. A guard is already held continuously by the native rig and
does not need two clip crossfades. The structured output schema now separately
constrains character rig scale to 1 and expresses height in `character.height_m`.
One subsequent real Claude call for exactly “the characters start fighting”
returned a valid two-character timeline through the deployed API. Scene
`3638491e-6fbd-481f-8ff1-62b1c34b2146` rendered in CPU Blender, passed evaluated
contact/framing validation and was visually inspected. It reuses the supplied
validated choreography example while adapting reference analysis and environment.
This is distinct from the locally authored study used for the matched H3 trials.

Playwright decoded and played that real Claude preview and inspected its timeline
on desktop and phone. It also passed real reference upload, inline oversized-file
errors, automatic legacy reconstruction, native/draft quality transitions, local
camera edits, reload recovery, and incompatible-plan rejection. These checks use
real deployed APIs/media without replacement responses or new inference requests.
Planning validation errors now identify the needed scene correction beside its
control, instead of blaming subscription availability for a geometry error.

A preservation audit found startup reconciliation rewriting two timestamps on
four unacknowledged interrupted historical takes. Recovery is now idempotent for
those takes; their original timestamps were restored from the pre-work SQLite
snapshot. Accepted unfinished work still reconciles through the existing ledger.
The final audit requires unchanged previous plan/job rows, preserved output files,
three new completed H3 trial jobs and unchanged unrelated container IDs/states.

Review the [private comparison gallery](http://100.103.129.82:3000/api/v1/outputs/motion_director/scenes/b92ee3e1-e59c-4b24-8363-b61e2a73619e/acceptance/comparison.html).
It includes original reference, all three actual H3 videos, local and fresh-Claude
Blender videos, contact report, settings/seed hashes and resource sample summaries.
Private raw evidence is `.motion-runtime/daidyra/`: test/build/deploy logs,
`comparison-results.json`, `browser-results.json`, `claude-browser-results.json`,
`verification-refreshed.json`, `fresh-claude-plan.json`, real MP4/contact sheets,
and `preservation.json`. No generated user media is committed to Git.

Recheck with verified uv.lock and existing dependencies:

```sh
bash scripts/verify_motion_release.sh
uv run --locked --no-sync node scripts/verify_daidyra_browser.cjs
uv run --locked --no-sync python scripts/package_daidyra_evidence.py
```

`verify_daidyra_render.cjs` reconciles its existing per-trial receipt and refuses a
new render unless `DAIDYRA_RENDER=1` is explicitly set. Historical trials used
the recorded intermediate handoff builds; the final draft recipe is the first
row of the table. Do not delete receipts to repeat those benchmarks.

Final release checks: 258 Python tests, TypeScript, optimized Next production
build, Compose configuration and whitespace validation pass. Deployed image tag
is `daidyra-verified` for gateway/frontend; the private host adapter runs the same
verified source. Supervisor, external H3 and other services retain their existing
deployments. Final real Pro verification returned `ready` after the owner login.
The private gallery and application both pass desktop/phone video playback checks.
