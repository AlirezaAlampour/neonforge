# Acceptance procedures

A workflow is supported only after real generation on the target machine. Unit tests, file presence and a health response are insufficient.

## Preflight

```bash
uv lock --check
docker compose config --quiet
awk '/^(MemTotal|MemAvailable|SwapTotal|SwapFree):/ {print}' /proc/meminfo
uv run --locked pytest -p no:cacheprovider
cd frontend
npm run lint
npm run build
```

Confirm no active workload before rebuilding control-plane services. Never use discrete GPU memory figures to admit work on UMA. The supervisor applies bounded workflow-specific launch floors and only reclaims idle allowlisted services.

## Real runs

The small recorder samples host MemAvailable once per second, submits through the gateway, polls the real job, probes and decodes the output, and optionally waits for automatic idle unload.

```bash
uv lock --check
uv run --locked python scripts/accept_creative.py voice --report /tmp/neonforge-voice.json
uv run --locked python scripts/accept_creative.py video --wait-unload --report /tmp/neonforge-video.json
uv run --locked python scripts/accept_creative.py character \
  --reference-id UPLOADED_IMAGE_ID --driving-id UPLOADED_VIDEO_ID \
  --wait-unload --report /tmp/neonforge-character.json
uv run --locked python scripts/accept_creative.py lipsync \
  --video /path/to/consented-demo.mp4 --audio /path/to/demo.wav \
  --wait-unload --report /tmp/neonforge-lipsync.json
```

Use `--gateway` for a configured non-loopback address. The recorder does not install models, change profiles or delete outputs. Review the generated media for quality; decode success alone does not prove lip timing or identity preservation.

Required evidence: backend/version, exact input settings, resolution, frame count/duration, wall runtime, MemAvailable before/lowest/after unload. Record failures as failures. See [v0.2 evidence](docs/acceptance-v0.2.md).

## Resource switching

Run a completed heavy workflow followed by another. Check `/workloads/status` for claims, targeted stops, prepared state and memory recovery. Unrelated containers and protected infrastructure must remain alive. A warm backend must not bypass a larger admission floor. Conflicting active claims must fail before model startup.

## Safety and readiness regressions

- Reject Character above 17 frames before startup, including API requests.
- Reject Video outside its bounded frame/resolution envelope.
- An unavailable readiness response must fail preparation and release its claim.
- Stopped Lip Sync with missing checkpoints must be unavailable.
- Failed requests must show actionable errors, not endless loading.
- Avatar and Character Animate must have no enabled Generate action until independently validated.
- Primary navigation contains only Voiceover, Video, Character, Avatar, Lip Sync and System Info.
- Voiceover retains profiles, editor, Breeze modes, direction, seed, CFG, Whisper transcription, output and history.

## Operator checks

```bash
curl --fail http://127.0.0.1:8080/readyz
curl --fail http://127.0.0.1:8080/workloads/status
docker logs --tail 100 ai-gateway
docker logs --tail 100 ai-supervisor
```

Old Creative Studio, standalone F5, B-Roll, LivePortrait, ReActor and Wan2.1 API paths are intentionally removed. Stored outputs and model files are preserved.
