# Motion Director stabilization — 2026-10-20

## Incident and recovery design

The deployed SQLite ledger contained four queued ten-second jobs with no H3
prompt IDs. Redis's tailed log shows a SIGTERM and clean snapshot/shutdown at
2026-10-19 19:26:01 UTC (12:26 PDT); Docker's journal records stop events for
Redis and other services at that time. No OOM evidence was found. The initiating
operator/process cannot be attributed from retained events. `unless-stopped`
does not reverse an explicit deployment stop. The later deployment used
`--no-deps`, so it could leave Redis stopped despite Compose dependencies.

The old submission committed SQLite, then awaited Redis, then scheduled work.
A DNS/cache exception therefore stranded a durable row before task creation.
Restoring Redis did not revisit these rows. The database was backed up online
before changes; existing job/artifact rows are never deleted.

SQLite is now the authoritative durable outbox. Job and segment intents commit
in one transaction. An optional unique Idempotency-Key makes acknowledgement
replay safe (the UI persists its key until acknowledgement). Redis is a bounded,
best-effort status cache; reads of Motion status use SQLite. A supervised dispatch
loop revisits committed outbox work. The deployment remains one gateway worker;
it must not be scaled to multiple replicas without a distributed execution lease.

Legacy unsubmitted jobs become interrupted, individually recoverable takes.
A valid plan can be retried directly; inconsistent/stale plans show a reuse-and-
review instruction before generation.
Restart recovery only retrieves acknowledged work and pauses before unsubmitted
sequence tails. A submission intent with no acknowledgement is matched against
H3 queue/history client IDs; missing/ambiguous evidence retains the reservation
and requires inspection, never another inference POST. Explicit retry requires
confirmed H3 failure/interruption, preserves completed clips and archives old
prompt IDs. Reconciliation and repeated finalization reuse existing history.

Memory/admission blocks are visible and explicitly retryable. Tracking has a
five-minute reconnect bound and two-hour clip bound. An active prompt retains
its claim if tracking cannot establish completion. Targeted H3 interruption
uses the installed server's prompt_id API at the existing 70°C guard or emergency
40 GiB host floor. User cancellation preserves files and waits for accepted work
to terminate. Native/draft canvases, admission floors and clock limits are unchanged.

Redis enables AOF/everysec and noeviction alongside its existing named volume and
RDB snapshots. Motion durability does not depend on Redis persistence. Deployment
must use `scripts/deploy_motion_release.sh RELEASE_ID`: validate, verify idle H3
and UMA, bring Redis up and wait healthy, then activate gateway/frontend only.
Never use `compose down` for this update. Unrelated services are excluded.

## Verification

Failure-injection tests cover cache DNS/outage, replayed acknowledgements, four
legacy orphans, committed-outbox restart, accepted-worker restart, client-ID
recovery, unknown acknowledgement refusal, memory/thermal blocks, partial
sequence recovery, cancellation, bounded reconnect and output retrieval failure.
Existing media, voice, admission, scene and GPU coordination regressions are run
with the root uv.lock verified and `uv run --locked --no-sync`.

Deployment artifacts, online database backup, before/after jobs, browser captures
and live draft evidence are retained under `.motion-runtime/stabilization/`.

## Live acceptance follow-up

`bc0a515` is the durable-queue milestone. The deployed outage test accepted new
job `9082b4f7-3d86-4c14-974e-cde90a0a2acf` with HTTP 202 while Redis was stopped;
its generic and Motion status endpoints remained readable. Redis was restarted
in a finally-protected browser harness. A real null `releasable` value under an
active GPU lease exposed a capabilities 500; that handling now has a regression.
Explicit mobile continuation reused the completed first clip and finished the
second. The original four jobs remain interrupted, zero accepted prompts each.
Redis AOF was enabled against the live keyspace before container recreation,
preserving the existing RDB-only keys. The release helper performs this migration
before activation. Gateway/frontend only were redeployed; supervisor and external
H3 were not replaced. Earlier service images and the feature branch are retained.

Final local acceptance: 228 Python regressions pass, including safe retrieval of
an already-accepted legacy canvas that would now be refused for new submission.
TypeScript, production Next.js build and both Compose files validate. The
mobile/desktop deployed checks cover real CPU Blender rendering/decoding,
zero-new-call cached planning, saved takes, recovery controls, comparison playback,
reuse, all seven current routes and legacy-route 404s. Browser observations use
America/Los_Angeles for timestamps; serialized job timestamps now include UTC.

The integrated deployment uses `motion-stability-final` gateway/frontend images;
Redis is healthy with AOF and noeviction. Supervisor keeps its original image and
container; external H3 keeps its original process. Unrelated container IDs and
running/stopped states match the pre-session inventory. No volumes or artifacts
were pruned. The live draft's frame samples are evidence of playback/recovery,
not a new identity-quality claim. Fresh Claude planning was not required for this
acceptance; its existing subscription check may still report quota/rate limiting.
