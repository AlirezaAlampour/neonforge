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

Legacy unsubmitted jobs become interrupted, individually retryable takes.
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
