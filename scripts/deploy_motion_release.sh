#!/usr/bin/env bash
# Recover dependencies first, then activate only the verified Motion control plane.
# Existing model services, host authentication, volumes and outputs are untouched.
set -euo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")/.."
uv lock --check --offline
release="${1:?Usage: scripts/deploy_motion_release.sh RELEASE_ID}"
[[ "$release" =~ ^[a-zA-Z0-9_.-]+$ ]]
override=".motion-runtime/images/$release/images.json"
[[ -f "$override" ]]
# The explicit up matters: unless-stopped does not undo a deployment's stop.
# Do not run compose down. The persistent Redis volume is reused.
uv run --locked --no-sync python - <<'PY'
import httpx
from pathlib import Path
kb=int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))
print(f'MemAvailable: {kb} kB ({kb/1048576:.1f} GiB)')
if kb/1048576<40:raise SystemExit('Deployment blocked below 40 GiB available UMA')
queue=httpx.get('http://127.0.0.1:8189/queue',timeout=15).raise_for_status().json()
if queue.get('queue_running') or queue.get('queue_pending'):raise SystemExit('H3 is active; deploy after its accepted work finishes')
cap=httpx.get('http://100.103.129.82:8080/api/v1/motion-director/capabilities',timeout=60).raise_for_status().json()
if (cap.get('resources') or {}).get('exclusive_claims'):raise SystemExit('A GPU claim is active; preserve the current deployment')
PY
docker compose -f docker-compose.yml -f docker-compose.motion.yml -f "$override" config --quiet
# Migrate the existing Redis keyspace to AOF before recreating its container.
# Enabling appendonly only at next boot can skip an RDB-only keyspace.
if docker inspect ai-redis >/dev/null 2>&1; then
  if [[ "$(docker inspect ai-redis --format '{{.State.Running}}')" != true ]]; then
    docker start ai-redis
  fi
  for attempt in {1..30}; do
    if docker exec ai-redis redis-cli ping | rg -q PONG; then break; fi
    sleep 1
  done
  docker exec ai-redis redis-cli SAVE
  docker exec ai-redis redis-cli CONFIG SET appendonly yes
  ready=false
  for attempt in {1..30}; do
    persistence="$(docker exec ai-redis redis-cli INFO persistence)"
    if [[ "$persistence" == *"aof_rewrite_in_progress:0"* && "$persistence" == *"aof_last_bgrewrite_status:ok"* ]]; then ready=true; break; fi
    sleep 1
  done
  [[ "$ready" == true ]]
fi
docker compose -f docker-compose.yml -f docker-compose.motion.yml -f "$override" up -d --no-deps --wait redis
# Images are prepared by build_motion_images.sh using existing dependencies.
docker compose -f docker-compose.yml -f docker-compose.motion.yml -f "$override" up -d --no-deps --wait gateway frontend
