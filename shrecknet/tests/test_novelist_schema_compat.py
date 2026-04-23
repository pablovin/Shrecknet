from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.novelist import NovelistRunCreate, NovelistRunRead


def test_novelist_request_contract_is_unchanged() -> None:
    payload = NovelistRunCreate.model_validate(
        {
            "unstructured_text": "Raw session text",
            "language": "en",
            "instructions": "Keep names stable",
            "previous_session_id": "entity-123",
        }
    )

    assert payload.unstructured_text == "Raw session text"
    assert payload.language == "en"
    assert payload.instructions == "Keep names stable"
    assert payload.previous_session_id == "entity-123"


def test_novelist_response_exposes_scene_centric_fields_from_artifacts() -> None:
    now = datetime.now(timezone.utc)
    run = NovelistRunRead.model_validate(
        {
            "id": "run-1",
            "agent_id": "agent-1",
            "status": "completed",
            "stage": "done",
            "request_payload": {"previous_session_id": "entity-123"},
            "artifacts": {
                "inputs": {
                    "previous_session_summary": "- unresolved oath conflict",
                    "previous_session_lookup_status": "matched_entity_instance_id",
                },
                "step_outputs": {
                    "step_7": {
                        "final_rewritten_text": "<p>final html</p>",
                    }
                },
                "timings_ms": {
                    "scaffolding": 101.2,
                    "scene_package": 80.0,
                    "retrieval": 90.0,
                    "total": 540.7,
                },
                "scene_progress": {
                    "scene-001": {
                        "intent_done": True,
                        "prose_done": True,
                        "critic_issue_count": 1,
                        "revision_action": "merged",
                    }
                },
                "stages": {
                    "revision": {
                        "scenes": [
                            {
                                "scene_id": "scene-001",
                                "prose_html": "<p>scene</p>",
                            }
                        ]
                    }
                },
            },
            "draft_text": "<p>final html</p>",
            "critic_notes": "{}",
            "created_at": now,
            "updated_at": now,
        }
    )

    assert run.previous_session_id == "entity-123"
    assert run.previous_session_summary == "- unresolved oath conflict"
    assert run.scene_results is not None
    assert len(run.scene_results) == 1
    assert run.step_outputs is not None
    assert run.step_outputs["step_7"]["final_rewritten_text"] == "<p>final html</p>"
    assert run.stage_timings is not None
    assert run.stage_timings["total"] == 540.7
    assert run.timing_summary is not None
    assert run.timing_summary["scene_count"] == 1
    assert run.scene_progress is not None
