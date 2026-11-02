"""Built-in workshops work without persisted agents and retain world boundaries."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import create_engine, inspect, text

from app.api.deps import get_current_user, get_ontology_instance_service
from app.api.routers import architect, novelist
from app.db.migrations import migrate_world_workshop_runs
from app.main import app
from app.models.architect import ArchitectAnalysisRun
from app.models.novelist import NovelistRun, NovelistRunStatus
from app.models.ontology import Ontology
from app.models.user import UserRole
from app.tasks.novelist import _previous_run_context


def configure(monkeypatch, role=UserRole.ADMIN):
    actor = SimpleNamespace(id="operator", role=role)
    app.dependency_overrides[get_current_user] = lambda: actor
    for module in (novelist, architect):
        monkeypatch.setattr(module, "require_ai_agents_enabled", lambda: None)
        monkeypatch.setattr(module, "is_shreckllm_configured", lambda _: True)
    return actor


@pytest.mark.asyncio
async def test_novelist_creates_text_and_upload_runs_with_no_agents(
    client, session_maker, monkeypatch
):
    configure(monkeypatch, UserRole.WRITER)
    queue = Mock()
    monkeypatch.setattr(novelist.generate_draft, "apply_async", queue)
    async with session_maker() as session:
        session.add_all([Ontology(id=7, name="Seven"), Ontology(id=8, name="Eight")])
        await session.commit()
    response = await client.post(
        "/jobs/novelist/worlds/7/runs",
        json={"unstructured_text": "A note", "language": "it", "instructions": "Brief"},
    )
    assert response.status_code == 202, response.text
    run = response.json()
    assert run["agent_id"] is None
    assert run["ontology_id"] == 7
    assert run["request_payload"]["instructions"] == "Brief"
    assert queue.call_args.kwargs["kwargs"]["author_id"] == "operator"
    upload = await client.post(
        "/jobs/novelist/worlds/8/runs/upload",
        files={"file": ("notes.txt", b"Another note", "text/plain")},
        data={"language": "pt"},
    )
    assert upload.status_code == 202, upload.text
    assert upload.json()["ontology_id"] == 8
    history = await client.get("/jobs/novelist/worlds/7/runs")
    assert [row["id"] for row in history.json()] == [run["id"]]
    assert (
        await client.delete(f"/jobs/novelist/worlds/8/runs/{run['id']}")
    ).status_code == 404
    assert (await client.get(f"/jobs/novelist/runs/{run['id']}")).status_code == 200
    assert (
        await client.delete(f"/jobs/novelist/worlds/7/runs/{run['id']}")
    ).json() == {"deleted": 1}


@pytest.mark.asyncio
async def test_world_workshop_role_world_and_continuity_failures(
    client, session_maker, monkeypatch
):
    configure(monkeypatch, UserRole.PLAYER)
    queue = Mock()
    monkeypatch.setattr(novelist.generate_draft, "apply_async", queue)
    assert (
        await client.post(
            "/jobs/novelist/worlds/7/runs", json={"unstructured_text": "x"}
        )
    ).status_code == 403
    configure(monkeypatch, UserRole.WRITER)
    assert (
        await client.post(
            "/jobs/novelist/worlds/999/runs", json={"unstructured_text": "x"}
        )
    ).status_code == 404
    async with session_maker() as session:
        session.add_all([Ontology(id=7, name="Seven"), Ontology(id=8, name="Eight")])
        session.add(
            NovelistRun(
                id="prior",
                agent_id=None,
                ontology_id=8,
                status=NovelistRunStatus.COMPLETED,
            )
        )
        await session.commit()
    response = await client.post(
        "/jobs/novelist/worlds/7/runs",
        json={"unstructured_text": "x", "previous_novelist_run_id": "prior"},
    )
    assert response.status_code == 400
    configure(monkeypatch, UserRole.PLAYER)
    assert (await client.get("/jobs/novelist/runs/prior")).status_code == 403
    queue.assert_not_called()


@pytest.mark.asyncio
async def test_architect_uses_world_source_and_history_without_agent(
    client, session_maker, monkeypatch
):
    configure(monkeypatch, UserRole.WORLD_BUILDER)
    queue = Mock()
    monkeypatch.setattr(architect.architect_task, "apply_async", queue)
    instances = SimpleNamespace(
        get_instance=AsyncMock(return_value=SimpleNamespace(ontology_id=7))
    )
    app.dependency_overrides[get_ontology_instance_service] = lambda: instances
    async with session_maker() as session:
        session.add_all([Ontology(id=7, name="Seven"), Ontology(id=8, name="Eight")])
        session.add(
            ArchitectAnalysisRun(
                id="other",
                agent_id=None,
                ontology_id=8,
                ontology_instance_id="page-other",
            )
        )
        await session.commit()
    created = await client.post(
        "/jobs/architect/worlds/7/analyze",
        json={"ontology_instance_id": "page-7", "ontology_id": 7},
    )
    assert created.status_code == 202, created.text
    run = created.json()
    assert run["agent_id"] is None and run["ontology_id"] == 7
    assert queue.call_args.kwargs["kwargs"]["agent_id"] is None
    listing = await client.get("/jobs/architect/worlds/7/runs?include=progress")
    assert [row["id"] for row in listing.json()] == [run["id"]]
    assert (
        await client.delete(f"/jobs/architect/worlds/8/runs/{run['id']}")
    ).status_code == 404
    assert (
        await client.post(
            "/jobs/architect/worlds/8/analyze", json={"ontology_instance_id": "page-7"}
        )
    ).status_code == 400
    assert queue.call_count == 1
    assert (await client.delete("/jobs/architect/worlds/7/runs")).json() == {
        "deleted": 1
    }
    assert len((await client.get("/jobs/architect/worlds/8/runs")).json()) == 1


@pytest.mark.asyncio
async def test_continuity_worker_does_not_read_a_different_world():
    repo = SimpleNamespace(
        get_run=AsyncMock(
            return_value=SimpleNamespace(
                status=NovelistRunStatus.COMPLETED,
                ontology_id=8,
                artifacts={"pipeline_version": "v5", "story_plan": {"beats": []}},
            )
        )
    )
    assert await _previous_run_context(repo, "prior", 7) is None


def test_migration_preserves_rows_indexes_and_ambiguous_worlds():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(text("CREATE TABLE agents (id VARCHAR(36) PRIMARY KEY)"))
        conn.execute(
            text(
                "CREATE TABLE agent_ontologies (agent_id VARCHAR(36), ontology_id INTEGER)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE novelist_runs (id VARCHAR(36) PRIMARY KEY, agent_id VARCHAR(36) NOT NULL, ontology_id INTEGER, draft_text TEXT, FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE)"
            )
        )
        conn.execute(text("CREATE INDEX ix_novelist_draft ON novelist_runs(id)"))
        conn.execute(text("INSERT INTO agents VALUES ('single'), ('multi')"))
        conn.execute(
            text(
                "INSERT INTO agent_ontologies VALUES ('single',7), ('multi',7), ('multi',8)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO novelist_runs VALUES ('one','single',NULL,'draft 1'), ('two','multi',NULL,'draft 2')"
            )
        )
        migrate_world_workshop_runs(conn)
        migrate_world_workshop_runs(conn)
        rows = conn.execute(
            text("SELECT id, ontology_id, draft_text FROM novelist_runs ORDER BY id")
        ).all()
        assert rows == [("one", 7, "draft 1"), ("two", None, "draft 2")]
        assert next(
            c
            for c in inspect(conn).get_columns("novelist_runs")
            if c["name"] == "agent_id"
        )["nullable"]
        assert (
            inspect(conn).get_foreign_keys("novelist_runs")[0]["options"]["ondelete"]
            == "SET NULL"
        )
        assert (
            inspect(conn).get_indexes("novelist_runs")[0]["name"] == "ix_novelist_draft"
        )
        conn.execute(
            text("INSERT INTO novelist_runs VALUES ('new',NULL,7,'new draft')")
        )
        conn.execute(text("DELETE FROM agents WHERE id='single'"))
        assert conn.execute(
            text("SELECT agent_id, draft_text FROM novelist_runs WHERE id='one'")
        ).one() == (None, "draft 1")


@pytest.mark.asyncio
async def test_novelist_worker_executes_agentless_run(monkeypatch):
    from contextlib import asynccontextmanager
    from app.tasks import novelist as worker

    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    @asynccontextmanager
    async def sessions():
        yield session

    repo = SimpleNamespace(
        get_run=AsyncMock(
            return_value=SimpleNamespace(agent_id=None, ontology_id=7, settings={})
        ),
        update_status=AsyncMock(),
    )
    llm = SimpleNamespace(aclose=AsyncMock(), get_usage_summary=lambda: {})
    execute = AsyncMock(
        return_value={"artifacts": {}, "final_text_html": "<p>A chapter</p>"}
    )
    monkeypatch.setattr(worker, "AsyncSessionMaker", sessions)
    monkeypatch.setattr(worker, "NovelistRepository", lambda _: repo)
    monkeypatch.setattr(worker, "is_shreckllm_configured", lambda _: True)
    monkeypatch.setattr(worker, "ShreckLLMClient", lambda **_: llm)
    monkeypatch.setattr(
        worker, "NovelistOrchestrator", lambda **_: SimpleNamespace(execute=execute)
    )
    monkeypatch.setattr(worker, "update_job_progress", AsyncMock())
    monkeypatch.setattr(worker, "mark_job_done", AsyncMock())
    result = await worker._execute_run(
        run_id="run", request_payload={"unstructured_text": "A chapter"}, job_id=1
    )
    assert result["final_text_html"] == "<p>A chapter</p>"
    assert "agent" not in execute.call_args.kwargs
    assert any(
        call.kwargs.get("draft_text") == "<p>A chapter</p>"
        for call in repo.update_status.call_args_list
    )


def test_architect_generation_tracks_the_operator_without_an_agent(monkeypatch):
    import asyncio
    from app.tasks import architect_generation as worker
    from app.models.background_job import AuthorType
    from app.services.creative_workshop_service import workshop_content_author_type

    create = AsyncMock(return_value=42)
    execute = AsyncMock(return_value={"run_id": "run"})
    monkeypatch.setattr(worker, "run_async", asyncio.run)
    monkeypatch.setattr(worker, "create_background_job", create)
    monkeypatch.setattr(worker, "_execute_generation", execute)
    monkeypatch.setattr(worker, "_attach_generation_job_to_run", AsyncMock())
    monkeypatch.setattr(worker, "mark_job_running", AsyncMock())
    monkeypatch.setattr(worker, "mark_job_done", AsyncMock())
    worker.generate_entities.run("run", {}, author_type="user", author_id="operator")
    assert create.call_args.kwargs["author_type"] == AuthorType.USER
    assert execute.call_args.kwargs["author_id"] == "operator"
    assert workshop_content_author_type("user") == "human"
    assert workshop_content_author_type("human") == "human"
    assert workshop_content_author_type("agent") == "agent"


@pytest.mark.asyncio
async def test_workshop_write_gate_is_enforced_by_backend(client, monkeypatch):
    from fastapi import HTTPException

    configure(monkeypatch)

    def disabled():
        raise HTTPException(status_code=503, detail="AI agents are disabled")

    monkeypatch.setattr(novelist, "require_ai_agents_enabled", disabled)
    response = await client.post(
        "/jobs/novelist/worlds/7/runs", json={"unstructured_text": "x"}
    )
    assert response.status_code == 503
    configure(monkeypatch, UserRole.WRITER)
    # World Builder remains necessary for Architect; choosing a world does not grant it.
    response = await client.get("/jobs/architect/worlds/7/runs")
    assert response.status_code == 403
