"""Ontology Studio operational telemetry and safe cancellation regression coverage."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routers import ontology_studio as api
from app.tasks.architect_learn_ontology import progress


def stub_run(status="running", diagnostics=None):
    return SimpleNamespace(
        id="r1", ontology_id=7, owner_id="owner", status=status,
        progress=0, stage="queued", diagnostics=diagnostics or [],
        model_targets={"discovery": {"provider": "openrouter", "name": "example",
                                     "api_key": "should-never-appear"},
                       "reconciliation": {"provider": "openrouter", "name": "example"}},
        input_sources=[{"source_id": "s1", "title": "A Rulebook"}],
        base_revision=0, proposal_revision=0, proposals=[],
        created_at=datetime(2026, 11, 14, tzinfo=timezone.utc), finished_at=None,
    )


class FakeDb:
    def __init__(self, run):
        self.run = run
        self.commits = 0

    def get(self, model, run_id):
        return self.run if run_id == self.run.id else None

    def refresh(self, run):
        pass

    def commit(self):
        self.commits += 1


def test_progress_reports_window_activity_and_separates_errors():
    run = stub_run(diagnostics=[{"kind": "error", "code": "old_warning"}])
    db = FakeDb(run)
    assert progress(db, run, 23, "discovering", {
        "event": "window_completed", "source_title": "Rules",
        "chapter_title": "Combat", "window_index": 2,
        "completed_windows": 2, "total_windows": 10,
        "proposals_discovered": 12,
    })
    payload = api.run_response(run)
    assert payload["progress"] == 23
    assert payload["activity"]["chapter_title"] == "Combat"
    assert payload["activity"]["completed_windows"] == 2
    assert payload["diagnostics"] == [{"kind": "error", "code": "old_warning"}]
    assert len(payload["activity_log"]) == 1
    assert payload["model_summary"]["discovery"] == {
        "provider": "openrouter", "name": "example",
    }
    assert "api_key" not in str(payload)


def test_progress_events_are_bounded_for_large_rulebooks():
    run = stub_run()
    db = FakeDb(run)
    for i in range(260):
        assert progress(db, run, i % 100, "discovering", {"event": "window_completed", "completed_windows": i})
    assert len(run.diagnostics) == 250
    assert api.run_response(run)["activity"]["completed_windows"] == 259


def test_queued_cancel_is_terminal_and_running_cancel_is_cooperative(monkeypatch):
    monkeypatch.setattr(api, "auth", lambda ontology_id, token: None)
    queued = stub_run(status="queued")
    queued_db = FakeDb(queued)
    result = api.cancel(7, "r1", token="token", db=queued_db, _=SimpleNamespace())
    assert result["status"] == "cancelled"
    assert queued.finished_at is not None
    with pytest.raises(HTTPException) as error:
        api.cancel(7, "r1", token="token", db=queued_db, _=SimpleNamespace())
    assert error.value.status_code == 409

    running = stub_run()
    running_db = FakeDb(running)
    result = api.cancel(7, "r1", token="token", db=running_db, _=SimpleNamespace())
    assert result["status"] == "cancel_requested"
    assert result["stage"] == "cancelling"
    assert not progress(running_db, running, 27, "discovering")
    assert running.status == "cancelled"


def test_route_registration_contract():
    from app.main import app
    paths = {r.path for r in app.routes}
    assert "/api/v1/ontology-studio/worlds/{ontology_id}/sources" in paths
    assert "/api/v1/ontology-studio/worlds/{ontology_id}/runs" in paths
    assert "/api/v1/ontology-studio/worlds/{ontology_id}/runs/{run_id}/cancel" in paths
