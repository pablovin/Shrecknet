"""Ontology Studio operational telemetry and safe cancellation regression coverage."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from pathlib import Path

from app.services.ontology_run_reporting import cancel_run, progress, run_response


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
    payload = run_response(run)
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
    assert run_response(run)["activity"]["completed_windows"] == 259


def test_queued_cancel_is_terminal_and_running_cancel_is_cooperative():
    queued = stub_run(status="queued")
    queued_db = FakeDb(queued)
    assert cancel_run(queued_db, queued)
    assert run_response(queued)["status"] == "cancelled"
    assert queued.finished_at is not None
    assert not cancel_run(queued_db, queued)

    running = stub_run()
    running_db = FakeDb(running)
    assert cancel_run(running_db, running)
    assert run_response(running)["status"] == "cancel_requested"
    assert run_response(running)["stage"] == "cancelling"
    assert not progress(running_db, running, 27, "discovering")
    assert running.status == "cancelled"


def test_route_registration_contract():
    # Registration smoke test is source-based in the lightweight CI job:
    # importing the full app requires PDF parser and embedding dependencies.
    root = Path(__file__).resolve().parents[1] / "app"
    main = (root / "main.py").read_text()
    routes = (root / "api/routers/ontology_studio.py").read_text()
    assert "app.include_router(ontology_studio_router)" in main
    assert 'prefix="/api/v1/ontology-studio/worlds/{ontology_id}"' in routes
    for route in ('@router.get("/sources")', '@router.get("/runs")',
                  '@router.post("/runs/{run_id}/cancel")'):
        assert route in routes
