"""Best-effort, file-based debug artifacts for Librarian queries."""

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
    """Return a useful JSON-facing representation for models and test doubles."""
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    if hasattr(value, "__dict__"):
        return vars(value)
    return value


class LibrarianDebugArtifacts:
    """Write ordered input/output snapshots without affecting query execution."""

    def __init__(self, output_dir: Path | None):
        self.output_dir = output_dir
        self._sequence = 0
        self._files: list[str] = []

    @classmethod
    def create(cls, *, enabled: bool = True) -> "LibrarianDebugArtifacts":
        if not enabled:
            return cls(None)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        run_name = f"querry_{timestamp}"
        data_root = os.getenv("SHRECKNET_DATA_DIR", "/data")
        module_root = Path(__file__).resolve().parents[3]
        candidates = [
            Path(data_root) / "local_tests" / "librarian" / run_name,
            module_root / "databases" / "local_tests" / "librarian" / run_name,
            Path.cwd() / "shrecknet" / "databases" / "local_tests" / "librarian" / run_name,
            Path.cwd() / "databases" / "local_tests" / "librarian" / run_name,
        ]
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=False)
                return cls(candidate)
            except OSError:
                continue

        fallback = Path("local_tests") / "librarian" / run_name
        fallback.mkdir(parents=True, exist_ok=True)
        return cls(fallback)

    def write(self, step: str, *, input: Any, output: Any) -> str | None:
        """Persist one stage. Serialization and I/O errors are non-fatal."""
        if self.output_dir is None:
            return None
        self._sequence += 1
        safe_step = re.sub(r"[^a-z0-9]+", "_", step.lower()).strip("_") or "step"
        path = self.output_dir / f"{self._sequence:02d}_{safe_step}.json"
        payload = {
            "step": step,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "input": input,
            "output": output,
        }
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            self._files.append(path.name)
            return str(path)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("librarian_debug_artifact_write_failed path=%s error=%s", path, exc)
            return None

    def write_manifest(self, **values: Any) -> str | None:
        """Write a stable run index after all numbered stage artifacts."""
        if self.output_dir is None:
            return None
        path = self.output_dir / "manifest.json"
        payload = {
            "pipeline_version": "v2",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "artifacts": list(self._files),
            **values,
        }
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            return str(path)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("librarian_debug_manifest_write_failed path=%s error=%s", path, exc)
            return None
