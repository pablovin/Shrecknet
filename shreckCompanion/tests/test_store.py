from __future__ import annotations

from app.core.config import Settings
from app.schemas import PersonalCompanionAgentCreate
from app.persistence.store import CompanionStore


def test_companion_and_session_persistence(tmp_path):
    store = CompanionStore(Settings(data_dir=str(tmp_path)))

    companion = store.create_companion(
        12,
        PersonalCompanionAgentCreate(
            name="Fiona",
            writing_style="Concise and grounded.",
            active=True,
        ),
    )

    session = store.create_session(
        user_id=12,
        companion_id=companion.id,
        ontology_id=99,
        title="New chat",
        allocated_tools={
            "elder": [{"id": "elder-1", "name": "Elder", "job": "elder", "ontology_ids": [99]}],
            "librarian": [],
        },
    )
    job_id = store.create_turn_job(
        user_id=12,
        session_id=session["session_id"],
        ontology_id=99,
        companion_id=companion.id,
        query="Who is Shrek?",
        payload={"status": "queued"},
    )
    store.update_turn_job(job_id, status="done", payload={"status": "done", "final": {"text": "Grounded answer."}})

    assert store.get_companion(12).id == companion.id
    assert store.get_session(12, session["session_id"]) is not None
    assert store.get_turn_job(12, job_id)["status"] == "done"
    assert (tmp_path / "local_tests/personal_companion/orchestrator/frontend_response_example.json").exists()


def test_session_crud_and_limits(tmp_path):
    store = CompanionStore(Settings(data_dir=str(tmp_path), companion_chat_session_limit_per_ontology=10))
    companion = store.create_companion(
        12,
        PersonalCompanionAgentCreate(name="Fiona", writing_style="Concise and grounded.", active=True),
    )
    created_ids = []
    for index in range(10):
        session = store.create_session(
            user_id=12,
            companion_id=companion.id,
            ontology_id=99,
            title=f"Chat {index}",
            allocated_tools={"elder": [], "librarian": []},
        )
        created_ids.append(session["session_id"])
    assert len(store.list_sessions(12, ontology_id=99, companion_id=companion.id)) == 10
    renamed = store.update_session_title(12, created_ids[0], title="Renamed chat")
    assert renamed is not None
    assert renamed["title"] == "Renamed chat"
    counts = store.count_sessions_by_ontology(12)
    assert counts == [{"ontology_id": 99, "count": 10, "limit": 10}]
    try:
        store.create_session(
            user_id=12,
            companion_id=companion.id,
            ontology_id=99,
            title="Overflow",
            allocated_tools={"elder": [], "librarian": []},
        )
    except ValueError as exc:
        assert "limit" in str(exc).lower()
    else:
        raise AssertionError("Expected chat session limit error")
    deleted = store.delete_session(12, created_ids[0])
    assert deleted is not None
    assert store.get_session(12, created_ids[0]) is None
    replacement = store.create_session(
        user_id=12,
        companion_id=companion.id,
        ontology_id=99,
        title="Replacement",
        allocated_tools={"elder": [], "librarian": []},
    )
    assert replacement["title"] == "Replacement"
