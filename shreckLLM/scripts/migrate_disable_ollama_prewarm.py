#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


TARGET_KEY = "SHRECKLLM_OLLAMA_PREWARM_ON_STARTUP"
TARGET_VALUE = "false"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _compose_env_path() -> Path:
    return _repo_root() / "configs" / "compose.env"


def main() -> int:
    path = _compose_env_path()
    if not path.exists():
        raise FileNotFoundError(f"compose env file not found: {path}")

    original_lines = path.read_text(encoding="utf-8").splitlines()
    new_lines: list[str] = []
    found = False
    changed = False

    for line in original_lines:
        if line.startswith(f"{TARGET_KEY}="):
            found = True
            expected = f"{TARGET_KEY}={TARGET_VALUE}"
            if line != expected:
                new_lines.append(expected)
                changed = True
            else:
                new_lines.append(line)
            continue
        new_lines.append(line)

    if not found:
        new_lines.append(f"{TARGET_KEY}={TARGET_VALUE}")
        changed = True

    if changed:
        output = "\n".join(new_lines)
        if not output.endswith("\n"):
            output += "\n"
        path.write_text(output, encoding="utf-8")
        print(f"[updated] {path}: set {TARGET_KEY}={TARGET_VALUE}")
    else:
        print(f"[no-op] {path}: already has {TARGET_KEY}={TARGET_VALUE}")

    print("Next step: restart shreckllm container to apply startup env changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

