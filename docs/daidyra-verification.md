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
