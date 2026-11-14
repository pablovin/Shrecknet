"""Legacy Scene paragraph-evidence parsing and compatibility helpers.

The former startup reconciliation is no longer called automatically. New
Scene/Milestone Source references are managed by the explicit operator flow in
``scene_source_link_migration``; these helpers retain historical Architect
evidence recovery and compatibility tests.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)
_MAX_DETAILS_BYTES = 24 * 1024 * 1024


def _paragraphs_from_proposal(proposal: dict[str, Any]) -> dict[str, Any] | None:
    """Accept only explicit, internally consistent Architect paragraph IDs."""
    raw = proposal.get("source_paragraphs_absolute") or []
    try:
        ids = [int(n) for n in raw] if isinstance(raw, list) else []
        if not ids or ids != sorted(set(ids)) or ids[0] <= 0:
            # Older records may have a range but no explicit paragraph list.
            start = int(proposal.get("start_paragraph") or 0)
            end = int(proposal.get("end_paragraph") or 0)
            if not (1 <= start <= end):
                return None
            ids = []
        else:
            start = int(proposal.get("start_paragraph") or ids[0])
            end = int(proposal.get("end_paragraph") or ids[-1])
            if not (1 <= start <= ids[0] <= ids[-1] <= end):
                return None
        if isinstance(raw, list) and len(raw) > 10000:
            return None
        if end - start > 100_000:
            return None
        return {"start_paragraph": start, "end_paragraph": end,
                "source_paragraphs_absolute": ids}
    except (TypeError, ValueError, OverflowError):
        return None


def _historical_scene_ranges(path: Path) -> tuple[dict[tuple[str, str, str], dict], int]:
    """Recover exact Architect identities from live jobs, durable archives, and artifacts.

    Background jobs may be removed by Shrecknet startup cleanup. The Architect
    pipeline also writes persistent pipeline_output.json files in local_tests.
    Only exact instance + scene ID + original entity ID matches are accepted.
    """
    unique: dict[tuple[str, str, str], dict] = {}
    conflicting: set[tuple[str, str, str]] = set()
    scanned = 0

    def add_pipeline(pipeline: object, extra_instance: str = "") -> None:
        nonlocal scanned
        if not isinstance(pipeline, dict):
            return
        instance = str(pipeline.get("ontology_instance_id") or extra_instance or "").strip()
        outputs = pipeline.get("outputs") or {}
        scenes = outputs.get("scenes") or outputs.get("scene_proposals") or []
        if not instance or not isinstance(scenes, list):
            return
        scanned += 1
        for proposal in scenes:
            if not isinstance(proposal, dict):
                continue
            scene_id = str(proposal.get("scene_id") or "").strip()
            derived = proposal.get("derived_from") or {}
            entity_id = str(proposal.get("source_entity_instance_id") or (
                derived.get("entity_instance_id") if isinstance(derived, dict) else ""
            ) or "").strip()
            if not scene_id or not entity_id:
                continue
            span = _paragraphs_from_proposal(proposal)
            if span is None:
                continue
            key = (instance, scene_id, entity_id)
            if key in conflicting:
                continue
            if key in unique and unique[key] != span:
                unique.pop(key, None)
                conflicting.add(key)
            else:
                unique[key] = span

    def load_json(raw: str) -> None:
        try:
            details = json.loads(raw)
            if not isinstance(details, dict):
                return
            pipeline = details.get("pipeline_output") or details
            if isinstance(pipeline, str):
                pipeline = json.loads(pipeline)
            add_pipeline(pipeline, str(details.get("ontology_instance_id") or ""))
        except (ValueError, TypeError):
            return

    if path.is_file():
        conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='background_jobs'").fetchone():
                for row in conn.execute("""
                    SELECT details FROM background_jobs
                    WHERE job_type = 'architect_analysis' AND details IS NOT NULL
                    ORDER BY id DESC
                """):
                    raw = row["details"]
                    if raw and len(raw) <= _MAX_DETAILS_BYTES:
                        load_json(raw)
        finally:
            conn.close()

    # Preserved by Shrecknet's startup job cleanup.
    archived_dir = path.parent / "architect_provenance"
    if archived_dir.is_dir():
        for artifact in archived_dir.glob("analysis-job-*.json"):
            try:
                if artifact.stat().st_size <= _MAX_DETAILS_BYTES:
                    load_json(artifact.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                logger.warning("Could not read historical Architect evidence artifact")

    # Existing Architect analysis artifacts predate the safe cleanup archive.
    # The pipeline uses the historical 'arhictect' spelling on disk.
    artifacts_dir = path.parent / "local_tests" / "arhictect" / "Analyses"
    if artifacts_dir.is_dir():
        for artifact in artifacts_dir.glob("*/pipeline_output.json"):
            try:
                if artifact.stat().st_size <= _MAX_DETAILS_BYTES:
                    load_json(artifact.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                logger.warning("Could not read Architect pipeline output artifact")

    if conflicting:
        logger.warning("Skipping %d ambiguous Architect paragraph matches", len(conflicting))
    return unique, scanned
