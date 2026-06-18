#!/usr/bin/env sh
# Applies database migrations, then launches the passed command (the bot).
set -eu

echo "[entrypoint] applying Alembic migrations..."
alembic upgrade head

echo "[entrypoint] starting: $*"
exec "$@"
