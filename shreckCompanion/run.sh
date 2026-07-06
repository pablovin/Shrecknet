#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

CONFIG_FILE="${SHRECKCOMPANION_CONFIG_FILE:-configs/shreckcompanion.json}"
HOST_PORT="$(
  python -c "import json, pathlib; print(json.loads(pathlib.Path('$CONFIG_FILE').read_text())['port'])"
)"

export SHRECKCOMPANION_HOST_PORT="$HOST_PORT"
echo "Using ShreckCompanion host/container port from $CONFIG_FILE: $SHRECKCOMPANION_HOST_PORT"

exec docker compose up --build "$@"
