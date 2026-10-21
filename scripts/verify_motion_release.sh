#!/usr/bin/env bash
# Repeatable lightweight checks. Uses existing dependencies only, managed by uv.
set -euo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")/.."
uv lock --check --offline
uv run --locked --no-sync python -m pytest -q -o cache_dir=.motion-runtime/pytest-cache
uv run --locked --no-sync node frontend/node_modules/typescript/bin/tsc --noEmit -p frontend/tsconfig.json
docker compose -f docker-compose.yml -f docker-compose.motion.yml config --quiet
git diff --check
