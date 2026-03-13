# Acceptance Tests -- DGX Spark AI Stack

## Prerequisites

- [ ] `.env` file created from `.env.example` and reviewed
- [ ] Host directories exist: `/srv/ai/{models,cache/hf,outputs,logs}`
- [ ] Docker daemon running with NVIDIA runtime available
- [ ] At least 80 GB free memory (check with `free -h`)
- [ ] Internet access for initial model downloads

---

## Phase 0: Infrastructure

### T0.1 -- Host Verification
```bash
uname -m                          # expect: aarch64
nvidia-smi                        # expect: GB10, driver 580.x, CUDA 13.0
grep MemTotal /proc/meminfo       # expect: ~131 GB (128 GB UMA)
docker info | grep Runtime        # expect: nvidia in list
```

### T0.2 -- Directory Setup
```bash
sudo mkdir -p /srv/ai/{models,cache/hf,outputs,logs}
sudo chown -R $USER:$USER /srv/ai
ls -la /srv/ai/
```

### T0.3 -- Docker Network
```bash
cd ~/dgx-ai-stack
docker compose config --quiet     # should exit 0
```

---

## Phase 1: Always-On Tier

### T1.1 -- Redis
```bash
docker compose up -d redis
docker compose ps redis           # expect: running, healthy
docker exec ai-redis redis-cli ping   # expect: PONG
```

### T1.2 -- Supervisor
```bash
docker compose up -d supervisor
sleep 3
curl -s http://localhost:8000/healthz 2>/dev/null || \
  docker exec ai-supervisor python -c \
    "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/healthz').read().decode())"
# expect: {"status": "alive", "managed": ["f5tts", "lipsync", "liveportrait", "wan21"]}
```

### T1.3 -- Gateway
```bash
docker compose up -d gateway
sleep 5
curl -s http://localhost:8080/healthz | python3 -m json.tool
# expect: {"status": "alive", "timestamp": "..."}

curl -s http://localhost:8080/readyz | python3 -m json.tool
# expect: {"status": "ready", "redis": true}

curl -s http://localhost:8080/memory | python3 -m json.tool
# expect: used_pct < 80, thresholds with reserves
```

### T1.4 -- Whisper
```bash
docker compose up -d whisper
docker logs -f ai-whisper   # watch for "Model loaded in X.Xs"

curl -s http://localhost:8080/services/status | python3 -m json.tool
# expect: whisper.alive=true, whisper.ready=true

# Functional test
curl -X POST http://localhost:8080/api/v1/whisper/transcribe \
  -F "audio=@test_audio.wav" | python3 -m json.tool
```

**Creating a test audio file** (if you don't have one):
```bash
ffmpeg -f lavfi -i "sine=frequency=440:duration=3" -ar 16000 /tmp/test_tone.wav
```

---

## Phase 2: Warm Tier

### T2.1 -- F5-TTS
```bash
docker compose up -d f5tts
docker logs -f ai-f5tts

# Should show idle (model not yet loaded)
curl -s http://localhost:8080/services/status | python3 -m json.tool

# Trigger model load via synthesis
curl -X POST http://localhost:8080/api/v1/tts/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello, this is a test of the text to speech system."}' \
  | python3 -m json.tool
# expect: {"output_path": "tts/UUID.wav", ...}

ls -la /srv/ai/outputs/tts/
```

### T2.2 -- LivePortrait
```bash
docker compose up -d liveportrait
docker logs -f ai-liveportrait

docker exec ai-liveportrait python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/smoke').read().decode())"
```

### T2.3 -- Lip-sync
```bash
docker compose up -d lipsync
docker logs -f ai-lipsync

docker exec ai-lipsync python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/healthz').read().decode())"
# expect: {"status": "alive", "backend": "video-retalking"}
```

---

## Phase 3: Lazy-Start Tier

### T3.1 -- Wan 2.1 (On-Demand via Supervisor)
```bash
# Verify wan21 is NOT running
docker compose ps wan21   # expect: no container or exited

# Trigger via gateway (supervisor auto-starts the container)
curl -X POST http://localhost:8080/api/v1/wan21/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "a cat walking in a garden",
    "num_frames": 8,
    "width": 256,
    "height": 256,
    "num_inference_steps": 10,
    "seed": 42
  }' | python3 -m json.tool
# NOTE: First run downloads ~3 GB. May take 5-10 min.

ls -la /srv/ai/outputs/wan21/

# Monitor UMA during generation
watch -n 2 'grep -E "MemAvailable|SwapFree" /proc/meminfo'
```

### T3.2 -- Wan 2.1 Idle Shutdown
```bash
# Wait for idle timeout (default 300s)
# Check container stops:
sleep 330
docker compose ps wan21   # expect: exited or gone

# Verify model was truly destroyed (check logs):
docker logs ai-wan21 --tail 20
# expect: "Wan 2.1 destroyed (UMA: X -> Y GB, freed ~Z GB)"
```

---

## Phase 4: Integration Tests

### T4.1 -- Job Queue
```bash
JOB_RESPONSE=$(curl -s -X POST http://localhost:8080/api/v1/whisper/transcribe \
  -F "audio=@/tmp/test_tone.wav")
JOB_ID=$(echo $JOB_RESPONSE | python3 -c "import sys,json; print(json.load(sys.stdin).get('job_id',''))")
echo "Job ID: $JOB_ID"
curl -s http://localhost:8080/jobs/$JOB_ID | python3 -m json.tool
# expect: status=completed
```

### T4.2 -- Memory Gate
```bash
curl -s http://localhost:8080/memory | python3 -m json.tool
# Verify hard_pct=80, reserves shown
# Gateway rejects GPU jobs when used_pct >= 80% or available < tier reserve
```

### T4.3 -- Concurrent Job Serialization (medium concurrency = 1)
```bash
# Submit two medium-tier jobs simultaneously
curl -X POST http://localhost:8080/api/v1/tts/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "concurrent job one"}' &
PID1=$!

curl -X POST http://localhost:8080/api/v1/tts/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "concurrent job two"}' &
PID2=$!

wait $PID1 $PID2
# Second job should queue until first finishes (semaphore=1)
docker logs ai-gateway --tail 20
```

### T4.4 -- Supervisor Isolation
```bash
# Verify gateway has NO docker socket
docker exec ai-gateway ls /var/run/docker.sock 2>&1
# expect: No such file or directory

# Verify supervisor HAS docker socket
docker exec ai-supervisor ls /var/run/docker.sock 2>&1
# expect: /var/run/docker.sock
```

### T4.5 -- Model Destroy Verification
```bash
# Load a warm-tier model, then wait for idle timeout
curl -X POST http://localhost:8080/api/v1/tts/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "trigger load"}'

# Record memory
grep MemAvailable /proc/meminfo

# Wait for idle timeout (default 1800s, or set F5TTS_IDLE_TIMEOUT=60 for testing)
# After timeout, check logs:
docker logs ai-f5tts --tail 10
# expect: "F5-TTS model destroyed (UMA: X -> Y GB, freed ~Z GB)"

# Verify memory freed
grep MemAvailable /proc/meminfo
# MemAvailable should increase by ~2-3 GB
```

---

## Phase 5: Operational Checks

### T5.1 -- Verify Script
```bash
python3 scripts/verify_dgx.py --gateway-url http://localhost:8080
# expect: all always-on services ALIVE/READY, memory gate OPEN

python3 scripts/verify_dgx.py --gateway-url http://localhost:8080 --smoke
# expect: smoke tests pass with latency percentiles

python3 scripts/verify_dgx.py --gateway-url http://localhost:8080 --json
# expect: JSON with host_memory, containers, gpu_processes, smoke_latency_percentiles
```

### T5.2 -- Idle Manager
```bash
sudo cp systemd/ai-idle-manager.service /etc/systemd/system/
sudo cp systemd/ai-idle-manager.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ai-idle-manager.timer
systemctl status ai-idle-manager.service
journalctl -u ai-idle-manager -n 20

# Dry-run
python3 scripts/idle_manager.py --once --dry-run
```

### T5.3 -- Log Verification
```bash
ls -la /srv/ai/logs/
docker logs ai-gateway --tail 10
docker logs ai-whisper --tail 10
docker logs ai-supervisor --tail 10
```

---

## Phase 6: Stress / Stability

### T6.1 -- Memory Monitoring Under Load
```bash
watch -n 1 'grep -E "MemTotal|MemAvailable|SwapFree" /proc/meminfo'
# During Wan 2.1 generation, MemAvailable should not drop below ~20 GB
# SwapFree should stay near SwapTotal (no significant swap usage)
```

### T6.2 -- Thermal Monitoring
```bash
watch -n 5 'nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,power.draw --format=csv'
# Temperature should stay below 80C
```

### T6.3 -- Container Restart Recovery
```bash
docker kill ai-whisper
sleep 10
docker compose ps whisper    # expect: restarting or running
curl -s http://localhost:8080/services/status | python3 -m json.tool
```

---

## Phase 7: Benchmark Matrix (Concurrent Workload Pairs)

These tests validate that specific service combinations run correctly
when sharing the single GPU. Run each pair and record:
- Total wall-clock time
- Peak MemAvailable drop during execution
- Whether SwapFree decreased (any swap = fail on UMA)
- Whether either job errored

### B7.1 -- Whisper + TTS (light + medium)
```bash
# Expected: both run concurrently, total time ~ max(whisper, tts)
# Memory: ~3-5 GB combined
time (
  curl -X POST http://localhost:8080/api/v1/whisper/transcribe \
    -F "audio=@/tmp/test_tone.wav" -o /tmp/b71_whisper.json &
  curl -X POST http://localhost:8080/api/v1/tts/synthesize \
    -H "Content-Type: application/json" \
    -d '{"text": "Benchmark seven point one test phrase for timing."}' \
    -o /tmp/b71_tts.json &
  wait
)
cat /tmp/b71_whisper.json /tmp/b71_tts.json | python3 -m json.tool
grep -E "MemAvailable|SwapFree" /proc/meminfo
```

### B7.2 -- TTS + LivePortrait (medium + medium)
```bash
# Expected: serialized by gpu_medium_sem (concurrency=1)
# Total time ~ tts_time + liveportrait_time
# Memory: peak ~5-7 GB (not additive since serialized)
time (
  curl -X POST http://localhost:8080/api/v1/tts/synthesize \
    -H "Content-Type: application/json" \
    -d '{"text": "Benchmark seven point two."}' \
    -o /tmp/b72_tts.json &
  curl -X POST http://localhost:8080/api/v1/liveportrait/animate \
    -F "source_image=@/tmp/test_face.png" \
    -F "driving_video=@/tmp/test_driving.mp4" \
    -o /tmp/b72_lp.json &
  wait
)
grep -E "MemAvailable|SwapFree" /proc/meminfo
```

### B7.3 -- LivePortrait + Lip-sync (medium + medium)
```bash
# Expected: serialized (concurrency=1)
# Tests that two face-processing models don't corrupt shared GPU state
time (
  curl -X POST http://localhost:8080/api/v1/liveportrait/animate \
    -F "source_image=@/tmp/test_face.png" \
    -F "driving_video=@/tmp/test_driving.mp4" \
    -o /tmp/b73_lp.json &
  curl -X POST http://localhost:8080/api/v1/lipsync/sync \
    -F "video=@/tmp/test_talking.mp4" \
    -F "audio=@/tmp/test_audio.wav" \
    -o /tmp/b73_ls.json &
  wait
)
grep -E "MemAvailable|SwapFree" /proc/meminfo
```

### B7.4 -- Wan 2.1 Alone (heavy, singleton)
```bash
# Expected: exclusive GPU access, 8-15 GB peak memory (1.3B)
# All other GPU jobs should queue while this runs
BEFORE=$(grep MemAvailable /proc/meminfo | awk '{print $2}')
time curl -X POST http://localhost:8080/api/v1/wan21/generate \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "a golden retriever running on a beach at sunset",
    "num_frames": 16,
    "width": 512,
    "height": 512,
    "num_inference_steps": 20,
    "seed": 42
  }' -o /tmp/b74_wan.json
AFTER=$(grep MemAvailable /proc/meminfo | awk '{print $2}')
echo "MemAvailable delta: $(( (BEFORE - AFTER) / 1024 )) MB"
cat /tmp/b74_wan.json | python3 -m json.tool
grep SwapFree /proc/meminfo
```

### Benchmark Matrix Summary

| Test  | Services           | Concurrency  | Expected Peak RAM | Serialized? | Swap OK? |
|-------|--------------------|--------------|-------------------|-------------|----------|
| B7.1  | whisper + tts      | light+medium | ~3-5 GB           | No (different sems) | Must be 0 |
| B7.2  | tts + liveportrait | medium+medium| ~5-7 GB peak      | Yes (sem=1) | Must be 0 |
| B7.3  | liveportrait + lip | medium+medium| ~5-7 GB peak      | Yes (sem=1) | Must be 0 |
| B7.4  | wan21 alone        | heavy        | ~8-15 GB (1.3B)   | Singleton   | Must be 0 |

---

## Pass Criteria

| Test | Required | Notes |
|------|----------|-------|
| T0.x Infrastructure | All pass | Blocking |
| T1.x Always-on tier | All pass | Blocking |
| T2.x Warm tier | F5-TTS + one other | LivePortrait/Lipsync may need ARM64 debugging |
| T3.x Wan 2.1 | 1.3B generates a short video | 14B is stretch goal |
| T4.x Integration | Job queue + memory gate + supervisor isolation | Core functionality |
| T5.x Operations | verify_dgx.py + idle manager | Required for production |
| T6.x Stress | No OOM kills, temp < 85C, no swap usage | Stability validation |
| B7.x Benchmarks | All 4 complete without OOM/swap | Concurrency validation |

---

## Known Issues to Watch For

1. **First-run CUDA JIT compilation**: PyTorch may JIT-compile kernels for sm_121 on first inference. Adds 5-10 minutes per service on first request. Cached in `/models/torch/`.

2. **Model download timeouts**: Wan 14B = 28 GB. Download manually to `/srv/ai/models/wan21/14B/` first.

3. **face-alignment build failure**: Requires `dlib` + `cmake` + `build-essential`. If Dockerfile build fails, check ARM64 dlib compilation.

4. **MediaPipe ARM64**: If LivePortrait fails, it likely needs an alternative face detection backend. See COMPATIBILITY_NOTES.md.

5. **Docker Compose profiles**: Wan 2.1 uses the `wan21` profile. Manual start: `docker compose --profile wan21 up -d wan21`.

6. **Model destruction verification**: After idle timeout, check logs for "destroyed" messages with actual GB freed. If freed < 1 GB, there may be a tensor leak.
