"""Incremental local diagnostics for Novelist runs, including failed attempts."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class NovelistDebugArtifacts:
    """Keep an ordered run trace outside public media; I/O failures are non-fatal."""

    def __init__(self, output_dir: Path | None):
        self.output_dir = output_dir
        self.files: list[str] = []
        self._manifest_values: dict[str, Any] = {}

    @classmethod
    def create(cls, run_id: str | None) -> "NovelistDebugArtifacts":
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", run_id or "run")[:100]
        name = f"{safe_id}_{timestamp}"
        package_root = Path(__file__).resolve().parents[3]
        roots = [Path(os.getenv("SHRECKNET_DATA_DIR", "/data")), package_root / "databases"]
        for root in roots:
            candidate = root / "local_tests" / "novelist" / name
            try:
                candidate.mkdir(parents=True, exist_ok=False)
                logger.info("novelist_debug_artifacts path=%s", candidate)
                return cls(candidate)
            except OSError:
                continue
        logger.warning("novelist_debug_artifacts_unavailable run_id=%s", run_id)
        return cls(None)

    def _write(self, filename: str, payload: Any) -> bool:
        if self.output_dir is None:
            return False
        try:
            (self.output_dir / filename).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            return True
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("novelist_debug_write_failed file=%s error=%s", filename, exc)
            return False

    def write(self, step: str, **values: Any) -> None:
        safe_step = re.sub(r"[^a-z0-9]+", "_", step.lower()).strip("_")
        filename = f"{len(self.files) + 1:03d}_{safe_step}.json"
        if self._write(filename, {"step": step, **values}):
            self.files.append(filename)
            if self._manifest_values:
                self.manifest()

    def manifest(self, **values: Any) -> None:
        self._manifest_values.update(values)
        self._write("manifest.json", {
            "pipeline_version": "v5",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "files": list(self.files),
            **self._manifest_values,
        })
