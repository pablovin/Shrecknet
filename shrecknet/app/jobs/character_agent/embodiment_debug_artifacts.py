"""Opt-in, local diagnostic artifacts for CharacterAgent embodiment runs.

Each enabled request receives its own directory.  A source bundle is written to
one append-only ``.log`` file so a reviewer can read every LLM request, raw
response, parsed response, correction, and checkpoint reuse in execution order.
The baseline and final orchestration state are kept in separate files.
"""

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
    """Convert Pydantic values and nested containers to JSON-safe diagnostics."""
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(value, list):
        return [debug_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): debug_value(item) for key, item in value.items()}
    return value


class EmbodimentDebugArtifacts:
    """Best-effort artifact writer which never changes embodiment execution."""

    def __init__(self, output_dir: Path | None):
        self.output_dir = output_dir

    @classmethod
    def create(
        cls, *, enabled: bool, draft_id: str, revision: int,
    ) -> "EmbodimentDebugArtifacts":
        if not enabled:
            return cls(None)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        run_name = f"request_{draft_id}_r{revision}_{timestamp}"
        data_root = Path(os.getenv("SHRECKNET_DATA_DIR", "/data"))
        module_root = Path(__file__).resolve().parents[3]
        candidates = [
            data_root / "local_test" / "character_embodiment" / run_name,
            module_root / "databases" / "local_test" / "character_embodiment" / run_name,
            Path.cwd() / "shrecknet" / "databases" / "local_test" / "character_embodiment" / run_name,
            Path.cwd() / "databases" / "local_test" / "character_embodiment" / run_name,
        ]
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=False)
                return cls(candidate)
            except OSError:
                continue
        logger.warning("character_embodiment_debug_directory_unavailable draft_id=%s", draft_id)
        return cls(None)

    @staticmethod
    def _safe_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "source"

    def _write(self, filename: str, record: dict[str, Any]) -> None:
        if self.output_dir is None:
            return
        path = self.output_dir / filename
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(debug_value(record), ensure_ascii=False, indent=2, default=str))
                handle.write("\n\n")
        except (OSError, TypeError, ValueError) as exc:
            logger.warning("character_embodiment_debug_write_failed path=%s error=%s", path, exc)

    def write_call(
        self, *, source_index: int | None, source_alias: str | None,
        stage: str, prompt: str, payload: dict[str, Any], raw_output: Any = None,
        parsed_output: Any = None, error: Any = None, model: Any = None,
        usage_tag: str, call_kind: str = "primary", response_metadata: Any = None,
    ) -> None:
        if source_index is None:
            filename = "baseline.log"
        else:
            filename = "bundle_{0:03d}_{1}.log".format(
                source_index + 1, self._safe_name(source_alias or "source"),
            )
        self._write(filename, {
            "record_type": "llm_call", "created_at": datetime.now(timezone.utc).isoformat(),
            "source_index": source_index, "source_alias": source_alias,
            "stage": stage, "call_kind": call_kind, "usage_tag": usage_tag,
            "model": str(getattr(model, "name", model)),
            "prompt": prompt, "input": payload, "raw_output": raw_output,
            "parsed_output": parsed_output, "error": error,
            "response_metadata": response_metadata,
        })

    def write_checkpoint(self, *, source_index: int, source_alias: str, checkpoints: dict[str, Any]) -> None:
        if checkpoints:
            self._write("bundle_{0:03d}_{1}.log".format(
                source_index + 1, self._safe_name(source_alias),
            ), {"record_type": "reused_checkpoints", "created_at": datetime.now(timezone.utc).isoformat(),
                "output": checkpoints})

    def write_final(self, *, input: Any, output: Any) -> None:
        self._write("final_pipeline.log", {
            "record_type": "final_pipeline", "created_at": datetime.now(timezone.utc).isoformat(),
            "input": input, "output": output,
        })
