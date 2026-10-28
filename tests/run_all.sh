#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/automation-server/docker-compose.yml"

cd "$ROOT_DIR"
python3 -m unittest discover -s tests -p 'test_*.py'
docker compose -f "$COMPOSE_FILE" run --rm --no-deps \
  -e DATABASE_PATH=/tmp/automation-tests.sqlite3 \
  -e PANEL_URL=http://127.0.0.1:1 -e REDIS_URL=redis://127.0.0.1:1/0 \
  -e ACCOUNT_DRIVER_URL=fixture://local \
  -v "$ROOT_DIR/automation-server/tests:/srv/app/tests:ro" automation-server \
  python -m unittest discover -s tests -p 'test_*.py'
docker compose -f "$COMPOSE_FILE" run --rm --no-deps \
  -v "$ROOT_DIR/account-driver/tests:/srv/app/tests:ro" account-driver \
  python -m unittest discover -s tests -p 'test_*.py'
