#!/usr/bin/env bash
# Build from the exact existing service images and installed Node dependencies.
# No npm/pip/apt/model downloads. Does not replace or restart production services.
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"
uv lock --check --offline
release_id="${1:-$(date -u +%Y%m%dT%H%M%SZ)}"
if [[ ! "$release_id" =~ ^[a-zA-Z0-9_.-]+$ ]]; then exit 1; fi
stage="$project_dir/.motion-runtime/images/$release_id"
mkdir -p "$stage/frontend/next" "$stage/gateway" "$stage/supervisor"
(
  cd frontend
  GATEWAY_INTERNAL_URL=http://gateway:8000 uv run --locked --no-sync node node_modules/next/dist/bin/next build --webpack
)
for service in frontend gateway supervisor; do
  base_id="$(docker inspect "ai-$service" --format '{{.Image}}')"
  docker tag "$base_id" "neonforge-motion-base-$service:$release_id"
done
cp -a frontend/.next/standalone/.next/. "$stage/frontend/next/"
cp -a frontend/.next/static "$stage/frontend/next/"
cp frontend/.next/standalone/server.js "$stage/frontend/server.js"
mkdir -p "$stage/frontend/next-compiled"
cp -a frontend/.next/standalone/node_modules/next/dist/compiled/. "$stage/frontend/next-compiled/"
cp -a gateway "$stage/gateway/gateway"
cp -a shared "$stage/gateway/gateway/shared"
cp supervisor/app.py supervisor/resource_manager.py "$stage/supervisor/"
cp -a shared "$stage/supervisor/shared"
printf 'FROM neonforge-motion-base-frontend:%s\nCOPY --chown=nextjs:nodejs next /app/.next\nCOPY --chown=nextjs:nodejs next-compiled /app/node_modules/next/dist/compiled\nCOPY --chown=nextjs:nodejs server.js /app/server.js\n' "$release_id" > "$stage/frontend/Dockerfile"
printf 'FROM neonforge-motion-base-gateway:%s\nCOPY gateway /app/gateway\n' "$release_id" > "$stage/gateway/Dockerfile"
printf 'FROM neonforge-motion-base-supervisor:%s\nCOPY app.py resource_manager.py /app/\nCOPY shared /app/shared\n' "$release_id" > "$stage/supervisor/Dockerfile"
for service in frontend gateway supervisor; do
  docker build --pull=false -t "neonforge-motion-$service:$release_id" "$stage/$service"
done
RELEASE_ID="$release_id" STAGE_DIR="$stage" uv run --locked --no-sync python - <<'PY'
import json,os
from pathlib import Path
tag=os.environ['RELEASE_ID']
Path(os.environ['STAGE_DIR'],'images.json').write_text(json.dumps({'services':{s:{'image':f'neonforge-motion-{s}:{tag}'} for s in ['frontend','gateway','supervisor']}},indent=2))
PY
printf 'Reviewable activation images: %s/images.json\nProduction has not been changed.\n' "$stage"
