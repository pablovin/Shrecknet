#!/usr/bin/env python3
"""Run the repository Compose stack with a Docker-accessible GPU when available."""
from __future__ import annotations

import argparse
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


def _mineru_profile_enabled(root: Path) -> bool:
    profiles = os.environ.get("COMPOSE_PROFILES") or _dotenv_value(root / ".env", "COMPOSE_PROFILES") or ""
    return "mineru" in {profile.strip() for profile in profiles.split(",") if profile.strip()}


def _require_cpu_compatible_settings(root: Path) -> None:
    if _mineru_profile_enabled(root):
        raise SystemExit(
            "The MinerU Compose profile requires a Docker-accessible NVIDIA GPU. "
            "Enable NVIDIA Container Toolkit and verify `docker run --rm --gpus all nvidia-smi`, "
            "or remove mineru from COMPOSE_PROFILES."
        )
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
    raw_args = list(argv if argv is not None else sys.argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mineru", action="store_true",
        help="start the optional MinerU PDF parser service (requires Docker GPU access)",
    )
    parser.add_argument(
        "--apply_source_migration", "--apply-source-migration",
        action="store_true",
        help=(
            "start the stack detached, then apply the ShreckWorld entity-to-Source "
            "and Scene/Milestone Source-reference migrations"
        ),
    )
    parser.add_argument(
        "--migrate-shrecktables", "--migrate_shrecktables",
        action="store_true",
        help=(
            "start the stack detached, preview ShreckRPG table/session coverage, "
            "then ask for backup acknowledgement and explicit confirmation"
        ),
    )
    options, args = parser.parse_known_args(raw_args)
    if not raw_args:
        args = ["--build", "-d"]
    mineru_enabled = options.mineru or _mineru_profile_enabled(root)

    docker = subprocess.run(
        ["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
    )
    if docker.returncode != 0:
        raise SystemExit("Docker is unavailable. Start Docker Desktop or the Docker daemon, then retry.")

    gpu_available = _docker_gpu_available()
    compose_args = ["docker", "compose", "-f", "docker-compose.yml"]
    if mineru_enabled:
        compose_args.extend(["--profile", "mineru"])
    if gpu_available:
        print("Docker can access an NVIDIA GPU; using the CUDA-enabled Compose build.", flush=True)
        compose_args.extend(["-f", "docker-compose.gpu.yml"])
    else:
        if options.mineru:
            raise SystemExit(
                "The --mineru option requires a Docker-accessible NVIDIA GPU. "
                "Enable NVIDIA Container Toolkit and verify `docker run --rm --gpus all nvidia-smi`."
            )
        _require_cpu_compatible_settings(root)
        print("No Docker-accessible NVIDIA GPU detected; using the CPU Compose build.", flush=True)

    compose_args.extend(["up", *args])
    if (options.apply_source_migration or options.migrate_shrecktables) and not any(
        arg in {"-d", "--detach"} for arg in args
    ):
        compose_args.append("-d")
    if (options.apply_source_migration or options.migrate_shrecktables) and "--wait" not in args:
        compose_args.append("--wait")
    compose_env = os.environ.copy()
    compose_env.setdefault("COMPOSE_BAKE", "true")
    startup = subprocess.run(compose_args, cwd=root, env=compose_env, check=False)
    if startup.returncode != 0:
        return startup.returncode

    compose_files = ["docker", "compose", "-f", "docker-compose.yml"]
    if mineru_enabled:
        compose_files.extend(["--profile", "mineru"])
    if gpu_available:
        compose_files.extend(["-f", "docker-compose.gpu.yml"])
    if options.migrate_shrecktables:
        print(
            "Previewing ShreckRPG tables and sessions. Applying requires a "
            "restorable backup acknowledgement and typed confirmation.",
            flush=True,
        )
        table_migration = subprocess.run(
            [
                *compose_files,
                "exec", "shreckworld_api",
                "python", "-m", "scripts.migrate_shrecktables",
                "--rpg-db", "/legacy-shreckrpg-data/shreckrpg.db",
                "--shrecknet-db", "/legacy-shrecknet-data/shrecknet.db",
                "--target-db", "/data/shreckworld.db",
                "--interactive",
            ],
            cwd=root,
            env=compose_env,
            check=False,
        )
        if table_migration.returncode != 0:
            return table_migration.returncode

    if options.apply_source_migration:
        print(
            "Applying the ShreckWorld entity-to-Source and Scene/Milestone Source migrations...",
            flush=True,
        )
        source_migration = subprocess.run(
            [
                *compose_files,
                "exec", "-T", "shreckworld_api",
                "python", "scripts/migrate_graph_scene_sources.py", "--apply",
            ],
            cwd=root,
            env=compose_env,
            check=False,
        )
        return source_migration.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
