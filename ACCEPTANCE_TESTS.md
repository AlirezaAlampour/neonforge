# Acceptance Tests

Run these checks on the target DGX Spark after reviewing [docs/installation.md](docs/installation.md). A responsive health endpoint is necessary but is not a successful model test.

## 1. Preflight

```bash
cd ~/neonforge
uv lock --check
docker compose config --quiet

awk '/^(MemTotal|MemAvailable|SwapTotal|SwapFree):/ {print}' /proc/meminfo
docker info --format '{{json .Runtimes}}'
docker compose ps
```

Do not start a heavyweight service when `MemAvailable` is below 40 GB. Resolve active workload pressure first; do not rely on `nvidia-smi` framebuffer figures on a unified-memory system.

## 2. Automated checks

```bash
uv sync --locked --dev
uv run pytest -p no:cacheprovider

cd frontend
npm ci --ignore-scripts
npm run build
cd ..
```

Expected: all Python tests pass and Next.js completes an optimized production build.

## 3. Base stack

```bash
docker compose up -d
docker compose ps

curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/readyz
curl --fail http://127.0.0.1:8080/memory
curl --fail http://127.0.0.1:8080/services/status
curl --fail http://127.0.0.1:3000/

uv run scripts/verify_dgx.py --gateway-url http://127.0.0.1:8080 --json
```

The gateway should be ready with Redis. Individual services may legitimately show Disabled or Missing model when their optional profiles/assets are absent.

If diagnosis is required, keep log reads bounded:

```bash
docker logs --tail 100 ai-gateway
docker logs --tail 100 ai-frontend
docker logs --tail 100 ai-whisper
```

## 4. Voiceover regression

Voiceover is the reference workflow and must remain usable before any media backend is accepted.

1. Open `http://127.0.0.1:3000/voiceover`.
2. Confirm the model picker loads and F5-TTS is selectable.
3. Create or select a non-sensitive test voice profile when the backend requires one.
4. Generate a multi-sentence script long enough to exercise chunking.
5. Confirm progress survives one page refresh.
6. Play and download the resulting audio, then verify the history item can be deleted.
7. Repeat with each optional engine being claimed as available.

Run the model smoke verifier only with sufficient available memory:

```bash
uv run scripts/verify_dgx.py --gateway-url http://127.0.0.1:8080 --smoke
```

The verifier refuses the smoke stage below 40 GB `MemAvailable`.

## 5. Readiness failure checks

### Lip Sync

With an incomplete legacy install, `/services/status` must report `Runtime error` or `Missing model`, and the Lip Sync Generate button must be disabled. The gateway must reject an upload with HTTP 503 before persisting input media.

When the runtime and checkpoints are deliberately installed, test with a disposable source video and speech track:

```bash
curl --fail-with-body -X POST http://127.0.0.1:8080/api/v1/lipsync/sync \
  -F 'video=@/tmp/neonforge-test-source.mp4' \
  -F 'audio=@/tmp/neonforge-test-speech.wav'
```

Acceptance requires a playable output whose mouth timing matches speech. `/healthz` alone does not count.

### LivePortrait

Until its adapter and model root are repaired, status must not report Ready. If repaired, acceptance requires a real source-image plus driving-video render, not an import check.

## 6. Character

Before enabling ComfyUI, confirm the model scan reports zero missing files for the managed `wan-character-swap` template and recheck the 40 GB reserve.

```bash
docker compose --profile comfyui up -d comfyui
docker logs --tail 100 ai-comfyui
curl --fail http://127.0.0.1:8080/api/v1/comfyui/templates
```

In the Character page:

1. Upload a disposable reference image and driving video.
2. Confirm the template validation is clean.
3. Submit one Replace job with default settings.
4. Confirm a tracked job, final playable video, and correct final output node.
5. Repeat once with debug artifacts enabled; confirm the patched graph and pose/mask/face-crop previews are produced.
6. Delete the uploaded fixtures and generated test media after review.

## 7. Video Generation

Only test Wan with at least 40 GB available and the intended model provisioned. The 1.3B variant is the conservative default.

```bash
docker compose --profile wan21 build wan21

curl --fail-with-body -X POST http://127.0.0.1:8080/api/v1/wan21/generate \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"a paper lantern drifting over calm water","num_frames":8,"width":256,"height":256,"num_inference_steps":10,"seed":42}'
```

Acceptance requires a valid video, job completion, no swap growth, and service teardown after the configured idle timeout.

```bash
docker logs --tail 100 ai-wan21
awk '/^(MemAvailable|SwapFree):/ {print}' /proc/meminfo
```

## 8. Future backend gates

LatentSync and LongCat Avatar remain unintegrated. Before either can be marked Experimental or Ready, record:

- a successful ARM64/GB10 image build without replacing the vendor PyTorch stack;
- an installed checkpoint and complete license review;
- preflight/readiness evidence;
- one real end-to-end render;
- output review for identity, lip timing, motion, and stability;
- a longer-than-trivial audio test;
- measured peak `MemAvailable` and swap behavior;
- idle unload/container-stop behavior.

For LongCat, test both a human portrait and a supported stylized character. For LatentSync, test generated and uploaded audio against a real source video.

## 9. Pass criteria

| Area | Required result |
| --- | --- |
| Code | Full CPU-safe test suite and frontend build pass |
| Compose | Configuration validates and base stack reaches gateway/UI readiness |
| Voiceover | Multi-sentence real audio generation passes |
| Status UX | Unavailable services fail closed with actionable state/detail |
| Character | Blocked until all files exist; then a real replacement render passes |
| Video | Blocked until model/memory prerequisites exist; then a real short render passes |
| Lip Sync / Avatar | Must not be claimed available without their real generation gates |
| Stability | No OOM kill, host freeze, or meaningful swap growth |
