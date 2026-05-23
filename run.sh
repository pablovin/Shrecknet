#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GPU_MODE="${SHRECKNET_OLLAMA_GPU_MODE:-auto}"
COMPOSE_FILES=(-f docker-compose.yml)

has_nvidia_gpu() {
	command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1
}

enable_gpu="false"
case "$GPU_MODE" in
	on)
		enable_gpu="true"
		;;
	off)
		enable_gpu="false"
		;;
	auto)
		if has_nvidia_gpu; then
			enable_gpu="true"
		fi
		;;
	*)
		echo "[run.sh] Invalid SHRECKNET_OLLAMA_GPU_MODE='$GPU_MODE'. Use: auto | on | off" >&2
		exit 1
		;;
esac

if [[ "$enable_gpu" == "true" ]]; then
	COMPOSE_FILES+=(-f docker-compose.gpu.yml)
	echo "[run.sh] Ollama GPU mode enabled (mode=$GPU_MODE)"
else
	echo "[run.sh] Ollama CPU mode enabled (mode=$GPU_MODE)"
fi

docker compose --env-file configs/compose.env --env-file configs/neo4j.env "${COMPOSE_FILES[@]}" up --build
