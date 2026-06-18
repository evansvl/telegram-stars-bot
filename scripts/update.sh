#!/usr/bin/env bash
# Pull the latest code and redeploy. Run from inside the project directory.
set -euo pipefail

log() { printf '\033[1;32m[update]\033[0m %s\n' "$*"; }

if [ ! -f "docker-compose.yml" ] || [ ! -d "app" ]; then
  echo "Run this from the project root (where docker-compose.yml lives)." >&2
  exit 1
fi

log "Current version: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

log "Pulling latest changes ..."
git pull --ff-only

log "New version:     $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

log "Rebuilding and restarting (migrations run on start) ..."
docker compose up -d --build

log "Pruning dangling images ..."
docker image prune -f >/dev/null || true

log "Done. Tail logs with: docker compose logs -f bot"
