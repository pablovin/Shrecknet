#!/usr/bin/env python3
"""Run the repository Compose stack with a Docker-accessible GPU when available."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
GPU_PROBE_IMAGE = "nvidia/cuda:12.6.3-base-ubuntu22.04"


def _dotenv_value(path: Path, key: str) -> str | None:
    """Read one simple Compose .env assignment without executing shell content."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    for line in lines:
        candidate = line.strip()
        if not candidate or candidate.startswith("#") or "=" not in candidate:
            continue
        name, value = candidate.split("=", 1)
        if name.strip() == key:
            return value.strip().strip("'\"")
    return None


def _effective_runtime_value(root: Path, env_key: str, json_key: str, default: str) -> str:
    environment_value = os.environ.get(env_key, "").strip()
    if environment_value:
        return environment_value
    dotenv_value = _dotenv_value(root / ".env", env_key)
    if dotenv_value:
        return dotenv_value
    config_path = root / "shrecknet" / "databases" / "service-config" / "shreckworld.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        config = {}
    return str(config.get(json_key, default))


def _docker_gpu_available() -> bool:
    probe = subprocess.run(
        [
            "docker", "run", "--rm", "--pull=missing", "--gpus", "all",
            GPU_PROBE_IMAGE, "nvidia-smi", "-L",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return probe.returncode == 0


def _require_cpu_compatible_settings(root: Path) -> None:
    parser_device = _effective_runtime_value(
        root, "SHRECKWORLD_PARSER_DEVICE", "parser_device", "auto"
    )
    embedding_device = _effective_runtime_value(
        root, "SHRECKWORLD_EMBEDDING_DEVICE", "embedding_device", "cpu"
    )
    if parser_device == "cuda" or embedding_device == "cuda":
        settings = []
        if parser_device == "cuda":
            settings.append("Parser device")
        if embedding_device == "cuda":
            settings.append("Embedding device")
        names = " and ".join(settings)
        raise SystemExit(
            f"No Docker-accessible NVIDIA GPU was detected, but {names} is explicitly set to CUDA. "
            "Choose Auto or CPU for the parser, and CPU for embeddings, in ShreckOS Admin before starting."
        )


def main(argv: Sequence[str] | None = None, *, root: Path = ROOT) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        args = ["--build", "-d"]

    docker = subprocess.run(
        ["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
    )
    if docker.returncode != 0:
        raise SystemExit("Docker is unavailable. Start Docker Desktop or the Docker daemon, then retry.")

    gpu_available = _docker_gpu_available()
    compose_args = ["docker", "compose", "-f", "docker-compose.yml"]
    if gpu_available:
        print("Docker can access an NVIDIA GPU; using the CUDA-enabled Compose build.", flush=True)
        compose_args.extend(["-f", "docker-compose.gpu.yml"])
    else:
        _require_cpu_compatible_settings(root)
        print("No Docker-accessible NVIDIA GPU detected; using the CPU Compose build.", flush=True)

    compose_args.extend(["up", *args])
    return subprocess.run(compose_args, cwd=root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
