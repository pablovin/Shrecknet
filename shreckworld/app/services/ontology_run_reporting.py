"""Persistent, bounded Ontology Studio job reporting.

Uses existing run columns, so old SQLite installations require no migration.
Keep HTTP/worker dependencies out of this module to allow focused CI tests.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.ontology_studio import OntologyLearnRun


def progress(db, run: OntologyLearnRun, percent: int, stage: str,
             activity: dict | None = None) -> bool:
    """Save progress and return false if cancellation was requested."""
    db.refresh(run)
    if run.status == "cancel_requested":
        run.status, run.stage = "cancelled", "cancelled"
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        return False
    run.progress, run.stage = max(0, min(100, percent)), stage
    if activity is not None:
        event = {"kind": "progress", "stage": stage, "progress": run.progress,
                 "at": datetime.now(timezone.utc).isoformat(), **activity}
        run.diagnostics = [*(run.diagnostics or []), event][-250:]
    db.commit()
    return True


def record_llm_usage(db, run: OntologyLearnRun, *, input_tokens: int | None,
                     output_tokens: int | None) -> None:
    """Persist usage for one completed model response; missing provider usage stays zero."""
    run.llm_calls += 1
    run.input_tokens += max(0, input_tokens or 0)
    run.output_tokens += max(0, output_tokens or 0)
    db.commit()


def cancel_run(db, run: OntologyLearnRun) -> bool:
    """Immediately cancel queued work; running jobs stop after an LLM call.

    Returns false for terminal/non-cancellable runs. The HTTP adapter owns the
    client-facing 409 response.
    """
    if run.status not in {"queued", "running"}:
        return False
    if run.status == "queued":
        run.status, run.stage = "cancelled", "cancelled"
        run.finished_at = datetime.now(timezone.utc)
    else:
        run.status, run.stage = "cancel_requested", "cancelling"
    db.commit()
    return True


def run_response(run: OntologyLearnRun) -> dict:
    """Public run status and chapter activity without model credentials."""
    records = run.diagnostics or []
    activity_log = [row for row in records if row.get("kind") == "progress"]
    return {
        "id": run.id, "ontology_id": run.ontology_id, "status": run.status,
        "stage": run.stage, "progress": run.progress,
        "input_sources": run.input_sources, "base_revision": run.base_revision,
        "proposal_revision": run.proposal_revision, "proposals": run.proposals,
        "diagnostics": [row for row in records if row.get("kind") != "progress"],
        "activity": activity_log[-1] if activity_log else None,
        "activity_log": activity_log,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "llm_calls": run.llm_calls,
        "extraction_contract": next(
            (event["contract_version"] for event in reversed(activity_log)
             if event.get("contract_version") in {"ontology-schema-v3", "ontology-compact-v2"}),
            "legacy",
        ),
        "model_summary": {
            key: {"provider": value.get("provider"), "name": value.get("name")}
            for key, value in (run.model_targets or {}).items()
            if key in {"discovery", "reconciliation"} and isinstance(value, dict)
        },
        "created_at": run.created_at, "finished_at": run.finished_at,
    }
