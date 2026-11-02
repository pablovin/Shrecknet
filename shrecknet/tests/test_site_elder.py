"""Site-wide Elder identity, caller-owned world scope, and preserved legacy history."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, inspect, text, select, func
from fastapi import HTTPException
from app.api.deps import get_current_user
from app.api.routers import elder, elder_chats
from app.db.elder_chat_migration import migrate_site_elder_chats
from app.jobs.elder.context import ElderExecutionContext
from app.jobs.elder.schemas import ElderQueryResponse
from app.main import app
from app.models.agent import Agent
from app.models.elder_chat import ElderChat
from app.models.ontology import Ontology
from app.models.user import UserRole
from app.utils import chat_store


@pytest.fixture
def site(monkeypatch, tmp_path):
    actor = SimpleNamespace(id=1, role=UserRole.ADMIN)
    app.dependency_overrides[get_current_user] = lambda: actor
    for module in (elder, elder_chats):
        monkeypatch.setattr(module, "require_ai_agents_enabled", lambda: None)
    monkeypatch.setattr(
        chat_store, "get_settings", lambda: SimpleNamespace(media_root=tmp_path)
    )
    pipeline = SimpleNamespace(
        execute=AsyncMock(
            return_value=ElderQueryResponse(
                agent_id="elder",
                query="Why?",
                answer="Scoped answer",
                trace_id="test",
                retrieval_plan={
                    "answer_goal": "Explain",
                    "response_scope": "standard",
                    "steps": [],
                },
                sources=[],
            )
        )
    )
    app.dependency_overrides[elder.get_elder_orchestrator] = lambda: pipeline
    return actor, pipeline


async def worlds(session_maker):
    async with session_maker() as session:
        session.add_all([Ontology(id=7, name="Seven"), Ontology(id=8, name="Eight")])
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope, ids",
    [
        ({"mode": "selected", "ontology_ids": [7]}, (7,)),
        ({"mode": "selected", "ontology_ids": [8, 7, 8]}, (7, 8)),
        ({"mode": "all"}, (7, 8)),
    ],
)
async def test_scoped_chat_uses_builtin_identity_and_persisted_scope(
    client, session_maker, site, scope, ids
):
    actor, pipeline = site
    await worlds(session_maker)
    created = await client.post(
        "/jobs/elder/chats/", json={"name": "My chat", "world_scope": scope}
    )
    assert created.status_code == 201, created.text
    chat = created.json()
    assert chat["agent_id"] is None and chat["ontology_ids"] == list(ids)
    assert chat["read_only"] is False
    response = await client.post(
        "/jobs/elder/query", json={"chat_id": chat["id"], "query": "Why?"}
    )
    assert response.status_code == 200, response.text
    context, request, history = pipeline.execute.call_args.args
    assert isinstance(context, ElderExecutionContext)
    assert context.id == "elder" and context.ontology_ids == ids
    assert history == []
    saved = (
        await client.get(f"/jobs/elder/chats/{chat['id']}?include_history=true")
    ).json()
    assert [message["content"] for message in saved["history"]] == [
        "Why?",
        "Scoped answer",
    ]
    async with session_maker() as session:
        assert await session.scalar(select(func.count()).select_from(Agent)) == 0
    actor.id = 2
    assert (
        await client.get(f"/jobs/elder/chats/{chat['id']}?include_history=true")
    ).status_code == 404
    assert (
        await client.post(
            "/jobs/elder/query", json={"chat_id": chat["id"], "query": "Why?"}
        )
    ).status_code == 404
    assert (await client.delete(f"/jobs/elder/chats/{chat['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_scope_must_be_explicit_and_cannot_reuse_broader_history(
    client, session_maker, site
):
    await worlds(session_maker)
    pipeline = site[1]
    assert (
        await client.post("/jobs/elder/query", json={"query": "Why?"})
    ).status_code == 422
    assert (
        await client.post(
            "/jobs/elder/query",
            json={
                "query": "Why?",
                "world_scope": {"mode": "selected", "ontology_ids": []},
            },
        )
    ).status_code == 422
    assert (
        await client.post(
            "/jobs/elder/query",
            json={
                "query": "Why?",
                "world_scope": {"mode": "selected", "ontology_ids": [999]},
            },
        )
    ).status_code == 404
    chat = (
        await client.post(
            "/jobs/elder/chats/", json={"name": "Both", "world_scope": {"mode": "all"}}
        )
    ).json()
    narrowed = await client.post(
        "/jobs/elder/query",
        json={
            "query": "Why?",
            "chat_id": chat["id"],
            "world_scope": {"mode": "selected", "ontology_ids": [7]},
        },
    )
    assert narrowed.status_code == 409
    pipeline.execute.assert_not_awaited()
    # A new world is available to new all-world calls, never silently added to old history.
    async with session_maker() as session:
        session.add(Ontology(id=9, name="Nine"))
        await session.commit()
    assert (
        await client.post(
            "/jobs/elder/query", json={"query": "Why?", "chat_id": chat["id"]}
        )
    ).status_code == 200
    assert pipeline.execute.call_args.args[0].ontology_ids == (7, 8)
    assert (
        await client.post(
            "/jobs/elder/query", json={"query": "Why?", "world_scope": {"mode": "all"}}
        )
    ).status_code == 200
    assert pipeline.execute.call_args.args[0].ontology_ids == (7, 8, 9)


@pytest.mark.asyncio
async def test_removed_world_and_disabled_ai_do_not_execute(
    client, session_maker, site, monkeypatch
):
    await worlds(session_maker)
    chat = (
        await client.post(
            "/jobs/elder/chats/",
            json={
                "name": "One",
                "world_scope": {"mode": "selected", "ontology_ids": [7]},
            },
        )
    ).json()
    async with session_maker() as session:
        world = await session.get(Ontology, 7)
        await session.delete(world)
        await session.commit()
    assert (
        await client.get(f"/jobs/elder/chats/{chat['id']}?include_history=true")
    ).status_code == 404
    assert (
        await client.post(
            "/jobs/elder/query", json={"query": "Why?", "chat_id": chat["id"]}
        )
    ).status_code == 404

    def disabled():
        raise HTTPException(status_code=503, detail="Disabled")

    monkeypatch.setattr(elder, "require_ai_agents_enabled", disabled)
    assert (
        await client.post(
            "/jobs/elder/query", json={"query": "Why?", "world_scope": {"mode": "all"}}
        )
    ).status_code == 503
    site[1].execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_transcripts_are_retained_read_only_and_outside_singleton_quota(
    client, session_maker, site
):
    await worlds(session_maker)
    async with session_maker() as session:
        agent = Agent(id="old-elder", name="Old Elder", job="elder", active=True)
        session.add(agent)
        session.add(
            ElderChat(
                id="old-chat",
                user_id=1,
                agent_id="old-elder",
                legacy_storage_agent_id="old-elder",
                name="Old conversation",
            )
        )
        await session.commit()
    chat_store.append_message(
        1, "old-elder", "old-chat", "assistant", "Historical answer"
    )
    saved = (await client.get("/jobs/elder/chats/old-chat?include_history=true")).json()
    assert saved["read_only"] and saved["history"][0]["content"] == "Historical answer"
    assert (
        await client.post(
            "/jobs/elder/query", json={"query": "Why?", "chat_id": "old-chat"}
        )
    ).status_code == 409
    for number in range(10):
        created = await client.post(
            "/jobs/elder/chats/",
            json={
                "name": str(number),
                "world_scope": {"mode": "selected", "ontology_ids": [7]},
            },
        )
        assert created.status_code == 201
    assert (
        await client.post(
            "/jobs/elder/chats/",
            json={"name": "Eleven", "world_scope": {"mode": "all"}},
        )
    ).status_code == 400
    assert len((await client.get("/jobs/elder/chats/")).json()["chats"]) == 11


def test_migration_preserves_child_history_indexes_and_legacy_storage(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.execute(text("CREATE TABLE agents (id VARCHAR(36) PRIMARY KEY)"))
        conn.execute(
            text(
                "CREATE TABLE elder_chats (id VARCHAR(36) PRIMARY KEY, agent_id VARCHAR(36) NOT NULL, name TEXT, FOREIGN KEY(agent_id) REFERENCES agents(id) ON DELETE CASCADE)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE elder_chat_history (id INTEGER PRIMARY KEY, chat_id VARCHAR(36), content TEXT, FOREIGN KEY(chat_id) REFERENCES elder_chats(id) ON DELETE CASCADE)"
            )
        )
        conn.execute(text("CREATE INDEX ix_elder_name ON elder_chats(name)"))
        conn.execute(text("INSERT INTO agents VALUES ('old')"))
        conn.execute(text("INSERT INTO elder_chats VALUES ('chat','old','History')"))
        conn.execute(
            text("INSERT INTO elder_chat_history VALUES (1,'chat','Preserve me')")
        )
    migrate_site_elder_chats(engine)
    migrate_site_elder_chats(engine)
    with engine.begin() as conn:
        assert (
            conn.execute(text("SELECT content FROM elder_chat_history")).scalar_one()
            == "Preserve me"
        )
        assert (
            conn.execute(
                text("SELECT legacy_storage_agent_id FROM elder_chats")
            ).scalar_one()
            == "old"
        )
        assert inspect(conn).get_indexes("elder_chats")[0]["name"] == "ix_elder_name"
        conn.execute(text("DELETE FROM agents WHERE id='old'"))
        assert (
            conn.execute(text("SELECT agent_id FROM elder_chats")).scalar_one() is None
        )
        assert (
            conn.execute(text("SELECT COUNT(*) FROM elder_chat_history")).scalar_one()
            == 1
        )
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []


@pytest.mark.asyncio
async def test_sql_only_legacy_history_remains_readable(client, session_maker, site):
    from app.models.elder_chat import ElderChatHistory

    async with session_maker() as session:
        session.add(Agent(id="old-sql", name="Old SQL Elder", job="elder", active=True))
        session.add(
            ElderChat(id="sql-chat", user_id=1, agent_id="old-sql", name="SQL history")
        )
        session.add(
            ElderChatHistory(
                chat_id="sql-chat", role="assistant", content="Stored in SQL"
            )
        )
        await session.commit()
    saved = await client.get("/jobs/elder/chats/sql-chat?include_history=true")
    assert saved.status_code == 200, saved.text
    assert saved.json()["history"][0]["content"] == "Stored in SQL"


@pytest.mark.asyncio
async def test_orchestrator_allocates_builtin_elder_without_creating_agents(
    session_maker,
):
    from app.services.herald_orchestrator_service import HeraldOrchestratorService

    await worlds(session_maker)
    async with session_maker() as session:
        allocation = await HeraldOrchestratorService(session).allocate_tools(7)
        assert len(allocation.elder) == 1
        assert allocation.elder[0].id == "elder"
        assert allocation.elder[0].ontology_ids == [7]
        assert await session.scalar(select(func.count()).select_from(Agent)) == 0


@pytest.mark.asyncio
async def test_world_embedding_record_removal_is_admin_only_and_world_scoped(
    client, session_maker, site
):
    from app.models.background_job import AuthorType, BackgroundJob, JobType

    async with session_maker() as session:
        session.add_all(
            [
                BackgroundJob(
                    id=1,
                    author_type=AuthorType.USER,
                    author_id="1",
                    ontology_id=7,
                    job_type=JobType.NEO4J_EMBEDDING,
                ),
                BackgroundJob(
                    id=2,
                    author_type=AuthorType.USER,
                    author_id="1",
                    ontology_id=8,
                    job_type=JobType.NEO4J_EMBEDDING,
                ),
                BackgroundJob(
                    id=3,
                    author_type=AuthorType.USER,
                    author_id="1",
                    ontology_id=7,
                    job_type=JobType.NOVELIST_DRAFT,
                ),
            ]
        )
        await session.commit()
    actor = site[0]
    actor.role = UserRole.PLAYER
    assert (await client.delete("/jobs/worlds/7/embedding-jobs")).status_code == 403
    actor.role = UserRole.ADMIN
    assert (await client.delete("/jobs/worlds/7/embedding-jobs/2")).status_code == 404
    response = await client.delete("/jobs/worlds/7/embedding-jobs")
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] == 1
    async with session_maker() as session:
        assert set((await session.scalars(select(BackgroundJob.id))).all()) == {2, 3}
