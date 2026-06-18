#!/usr/bin/env bash
# Idempotent installer for the Telegram Stars bot.
# - installs Docker + Compose plugin if missing
# - clones the repo (if not already inside it)
# - creates .env from .env.example (with hints)
# - builds and starts the stack (migrations run on container start)
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/evansvl/telegram-stars-bot.git}"
TARGET_DIR="${TARGET_DIR:-telegram-stars-bot}"

log() { printf '\033[1;32m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[install]\033[0m %s\n' "$*"; }

install_docker() {
  if command -v docker >/dev/null 2>&1; then
    log "Docker already installed: $(docker --version)"
  else
    log "Installing Docker via get.docker.com ..."
    curl -fsSL https://get.docker.com | sh
    if command -v systemctl >/dev/null 2>&1; then
      sudo systemctl enable --now docker || true
    fi
  fi
  if docker compose version >/dev/null 2>&1; then
    log "Docker Compose plugin present: $(docker compose version)"
  else
    warn "Docker Compose plugin not found. Install it: https://docs.docker.com/compose/install/"
    exit 1
  fi
}

ensure_repo() {
  if [ -f "docker-compose.yml" ] && [ -d "app" ]; then
    log "Running inside the project directory."
  elif [ -d "${TARGET_DIR}/.git" ]; then
    log "Repo already cloned, entering ${TARGET_DIR}"
    cd "${TARGET_DIR}"
  else
    log "Cloning ${REPO_URL} -> ${TARGET_DIR}"
    git clone "${REPO_URL}" "${TARGET_DIR}"
    cd "${TARGET_DIR}"
  fi
}

ensure_env() {
  if [ -f ".env" ]; then
    log ".env already exists — leaving it untouched."
  else
    cp .env.example .env
    warn "Created .env from .env.example. EDIT IT before the bot can start:"
    warn "  - BOT_TOKEN     (from @BotFather)"
    warn "  - WATA_TOKEN    (WATA merchant dashboard)"
    warn "  - ADMIN_IDS     (your Telegram numeric ID)"
    warn "  - WEBHOOK_HOST  (your public domain behind the reverse proxy)"
    warn "Then re-run this script (or: docker compose up -d --build)."
    exit 0
  fi
}

main() {
  install_docker
  ensure_repo
  ensure_env
  log "Building and starting the stack ..."
  docker compose up -d --build
  log "Done. Migrations run automatically on container start."
  log "Logs:    docker compose logs -f bot"
  log "Status:  docker compose ps"
}

main "$@"
