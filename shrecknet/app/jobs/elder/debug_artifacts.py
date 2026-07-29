"""Best-effort, ordered JSON debug artifacts for Elder queries."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def debug_value(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    if isinstance(value, list):
        return [debug_value(item) for item in value]
    if isinstance(value, dict):
        return {key: debug_value(item) for key, item in value.items()}
    if hasattr(value, "__dict__"):
        return vars(value)
    return value


class ElderDebugArtifacts:
    """Write Elder inputs, LLM responses, retrieval, and evidence without affecting execution."""

    def __init__(self, output_dir: Path | None):
        self.output_dir = output_dir
        self._sequence = 0
        self._files: list[str] = []

    @classmethod
    def create(cls, *, enabled: bool) -> "ElderDebugArtifacts":
        if not enabled:
            return cls(None)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        run_name = f"query_{timestamp}"
        data_root = os.getenv("SHRECKNET_DATA_DIR", "/data")
        module_root = Path(__file__).resolve().parents[3]
        candidates = [
            Path(data_root) / "local_tests" / "elder" / run_name,
            module_root / "databases" / "local_tests" / "elder" / run_name,
            Path.cwd() / "shrecknet" / "databases" / "local_tests" / "elder" / run_name,
            Path.cwd() / "databases" / "local_tests" / "elder" / run_name,
        ]
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=False)
                return cls(candidate)
            except OSError:
                continue
        fallback = Path("local_tests") / "elder" / run_name
        fallback.mkdir(parents=True, exist_ok=True)
        return cls(fallback)

    def write(self, step: str, *, input: Any, output: Any) -> str | None:
        if self.output_dir is None:
            return None
        self._sequence += 1
        safe_step = re.sub(r"[^a-z0-9]+", "_", step.lower()).strip("_") or "step"
        path = self.output_dir / f"{self._sequence:02d}_{safe_step}.json"
        payload = {
            "step": step,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input": debug_value(input),
            "output": debug_value(output),
        }
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            self._files.append(path.name)
            return str(path)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("elder_debug_artifact_write_failed path=%s error=%s", path, exc)
            return None

    def write_manifest(self, **values: Any) -> str | None:
        if self.output_dir is None:
            return None
        path = self.output_dir / "manifest.json"
        payload = {
            "pipeline_version": "elder-query-retrieval-v3",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "artifacts": list(self._files),
            **values,
        }
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            return str(path)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("elder_debug_manifest_write_failed path=%s error=%s", path, exc)
            return None

    def write_final_response(self, response: Any) -> str | None:
        """Write the exact client-facing Elder response as a standalone JSON payload."""
        if self.output_dir is None:
            return None
        path = self.output_dir / "final_response.json"
        model_dump = getattr(response, "model_dump", None)
        payload = model_dump(mode="json") if callable(model_dump) else debug_value(response)
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            if path.name not in self._files:
                self._files.append(path.name)
            return str(path)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("elder_debug_final_response_write_failed path=%s error=%s", path, exc)
            return None
