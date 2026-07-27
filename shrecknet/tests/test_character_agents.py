from __future__ import annotations

from types import SimpleNamespace
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text

from app.db.migrations import migrate_remove_legacy_character_agents
from app.graph.neo4j import ensure_character_graph_constraints
from app.api.routers.character_agents import (
    get_agent,
    list_agent_aspects,
    list_agent_goals,
    list_agents,
    query_character_agent,
    get_character_agent_query_job,
)
from app.schemas.character_agent import (
    CharacterAgentCreate,
    CharacterAgentUpdate,
    CharacterAgentVisibility,
    CharacterAspectAssignmentCreate,
    CharacterAspectAssignmentRead,
    CharacterAspectCreate,
    CharacterAspectRead,
    CharacterGoalCreate,
    CharacterGoalRead,
    CharacterAgentQueryRequest,
)


def test_character_payload_defaults_and_ranges():
    payload = CharacterAgentCreate(
        ontology_id=42, entity_instance_id=" entity-1 ",
        name=" Mara ", background_story=" Story ",
    )
    assert payload.ontology_id == 42
    assert payload.entity_instance_id == "entity-1"
    assert payload.name == "Mara"
    assert payload.calm_aggressive == 50
    assert payload.cooperative_dominating == 50
    assert payload.visibility == CharacterAgentVisibility.PRIVATE
    derived = CharacterAgentCreate(
        ontology_id=42, entity_instance_id="entity-2"
    )
    assert derived.name is None
    assert derived.background_story is None
    assert derived.image_url is None
    with pytest.raises(ValidationError):
        CharacterAgentCreate(
            ontology_id=42, entity_instance_id="e", name="Mara",
            background_story="Story", calm_aggressive=101,
        )
    assert CharacterAgentCreate(
        ontology_id=42, entity_instance_id="public-entity", visibility="public"
    ).visibility == CharacterAgentVisibility.PUBLIC
    with pytest.raises(ValidationError):
        CharacterAgentCreate(
            ontology_id=42, entity_instance_id="e", visibility="shared"
        )


def test_scope_and_embodiment_are_not_patchable():
    with pytest.raises(ValidationError):
        CharacterAgentUpdate.model_validate({"ontology_id": 99})
    with pytest.raises(ValidationError):
        CharacterAgentUpdate.model_validate({"entity_instance_id": "other"})


def test_aspect_goal_and_assignment_enums_and_bounds():
    aspect = CharacterAspectCreate(
        ontology_id=42, name="  Expert   Archer  ", category="capability"
    )
    assert aspect.name == "Expert   Archer"
    goal = CharacterGoalCreate(
        ontology_id=42, title=" Protect their daughter ", goal_type="obligation"
    )
    assert goal.title == "Protect their daughter"
    with pytest.raises(ValidationError):
        CharacterAspectAssignmentCreate(
            character_aspect_id="a", importance=6, intensity=50
        )
    with pytest.raises(ValidationError):
        CharacterGoalCreate(
            ontology_id=42, title="Goal", goal_type="temporary", priority=50
        )


def test_generated_aspect_and_goal_reads_accept_embodiment_provenance():
    timestamps = {
        "created_at": "2026-07-25T10:00:00Z",
        "updated_at": "2026-07-25T10:00:00Z",
    }
    aspect = CharacterAspectRead.model_validate({
        "id": "aspect-1", "ontology_id": 42, "name": "Investigator",
        "normalized_name": "investigator", "category": "role", "status": "active",
        "justification": "Repeatedly investigates dangerous events.",
        "confidence": 0.9, "evidence_ids": '["scene:1"]',
        "generated_by_embodiment_draft_id": "draft-1", **timestamps,
    })
    goal = CharacterGoalRead.model_validate({
        "id": "goal-1", "ontology_id": 42, "title": "Find the truth",
        "goal_type": "objective", "status": "active", "priority": 90,
        "commitment": 85, "justification": "The objective remains unresolved.",
        "confidence": 0.8, "basis": "inferred",
        "evidence_ids": '["scene:2"]',
        "generated_by_embodiment_draft_id": "draft-1", **timestamps,
    })
    assignment = CharacterAspectAssignmentRead.model_validate({
        "aspect": aspect.model_dump(), "importance": 5, "intensity": 80,
        "status": "active", "justification": "Central to the character.",
        "confidence": 0.9, "evidence_ids": '["scene:1"]', **timestamps,
    })

    assert aspect.evidence_ids == ["scene:1"]
    assert goal.evidence_ids == ["scene:2"]
    assert goal.basis == "inferred"
    assert assignment.evidence_ids == ["scene:1"]


def test_legacy_character_table_removal_is_idempotent():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE character_agents (id VARCHAR PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE character_agent_allowed_users (character_agent_id VARCHAR)"))
        migrate_remove_legacy_character_agents(connection)
        migrate_remove_legacy_character_agents(connection)
        result = connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        names = {row[0] for row in result}
    assert "character_agents" not in names
    assert "character_agent_allowed_users" not in names


class _ConstraintSession:
    def __init__(self):
        self.statements = []

    async def run(self, statement):
        self.statements.append(" ".join(statement.split()))


@pytest.mark.asyncio
async def test_character_graph_constraint_setup_is_idempotent():
    session = _ConstraintSession()
    await ensure_character_graph_constraints(session)
    first = list(session.statements)
    await ensure_character_graph_constraints(session)
    assert session.statements == first + first
    assert all("IF NOT EXISTS" in statement for statement in first)
    assert any("embodied_entity_instance_id IS UNIQUE" in statement for statement in first)
    assert {"CharacterAgent", "CharacterAspect", "CharacterGoal"} <= {
        label for statement in first for label in ("CharacterAgent", "CharacterAspect", "CharacterGoal") if label in statement
    }


class _ReadService:
    def __init__(self):
        self.calls = []

    async def list_agents(self, *args, **kwargs):
        self.calls.append(("list", args, kwargs))
        return []

    async def get_agent(self, *args, **kwargs):
        self.calls.append(("get", args, kwargs))
        return {"id": args[0]}

    async def list_agent_aspects(self, *args, **kwargs):
        self.calls.append(("aspects", args, kwargs))
        return []

    async def list_agent_goals(self, *args, **kwargs):
        self.calls.append(("goals", args, kwargs))
        return []

    async def load_query_snapshot(self, *args, **kwargs):
        self.calls.append(("query", args, kwargs))
        return {"character_agent": {}, "aspects": [], "goals": []}

    async def ensure_queryable(self, *args, **kwargs):
        self.calls.append(("queryable", args, kwargs))


@pytest.mark.asyncio
async def test_authenticated_reads_are_public_only_while_admin_reads_are_unrestricted(
    monkeypatch,
):
    import app.api.routers.character_agents as router

    async def create_job(**_kwargs):
        return 41

    monkeypatch.setattr(router, "require_ai_agents_enabled", lambda: None)
    monkeypatch.setattr(router, "is_shreckllm_configured", lambda _settings: True)
    monkeypatch.setattr(router, "create_background_job", create_job)
    monkeypatch.setattr(
        router.run_character_agent_query, "delay", lambda **_kwargs: None
    )
    service = _ReadService()
    user = SimpleNamespace(id=1, role="player")
    admin = SimpleNamespace(id=2, role="admin")

    await list_agents(None, None, None, 0, 50, user, service)
    await get_agent("agent-1", user, service)
    await list_agent_aspects("agent-1", user, service)
    await list_agent_goals("agent-1", user, service)
    await query_character_agent(
        "agent-1", CharacterAgentQueryRequest(query="Reply"), user, service
    )
    await list_agents(None, None, None, 0, 50, admin, service)
    await get_agent("agent-2", admin, service)

    assert service.calls[0][2]["public_only"] is True
    assert service.calls[1][2]["public_only"] is True
    assert service.calls[2][2]["public_only"] is True
    assert service.calls[3][2]["public_only"] is True
    assert service.calls[4][2]["public_only"] is True
    assert service.calls[5][2]["public_only"] is False
    assert service.calls[6][2]["public_only"] is False


@pytest.mark.asyncio
async def test_generic_query_checks_access_without_loading_character_identity(monkeypatch):
    import app.api.routers.character_agents as router

    async def create_job(**_kwargs):
        return 42

    monkeypatch.setattr(router, "require_ai_agents_enabled", lambda: None)
    monkeypatch.setattr(router, "is_shreckllm_configured", lambda _settings: True)
    monkeypatch.setattr(router, "create_background_job", create_job)
    monkeypatch.setattr(
        router.run_character_agent_query, "delay", lambda **_kwargs: None
    )
    service = _ReadService()
    user = SimpleNamespace(id=1, role="player")
    payload = CharacterAgentQueryRequest(query="Reply", use_character_identity=False)

    response = await query_character_agent("agent-1", payload, user, service)

    assert service.calls == [
        ("queryable", ("agent-1",), {"public_only": True})
    ]
    assert response.job_id == 42
    assert response.status == "queued"


@pytest.mark.asyncio
async def test_query_job_polling_enforces_owner_and_returns_terminal_result(monkeypatch):
    import app.api.routers.character_agents as router

    row = SimpleNamespace(
        id=42,
        author_type="user",
        author_id="1",
        job_type="character_agent_query",
        status="done",
        progress=1.0,
        details=(
            '{"character_agent_id":"agent-1","stage":"completed",'
            '"result":{"type":"json","content":{"choice_id":"accept"},'
            '"decision_basis":"The evidence favors acceptance."},"error":null}'
        ),
        started_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )

    class Jobs:
        def __init__(self, _session):
            pass

        async def get_job(self, _job_id):
            return row

    monkeypatch.setattr(router, "BackgroundJobService", Jobs)
    result = await get_character_agent_query_job(
        "agent-1", 42, SimpleNamespace(id=1, role="player"), object()
    )
    assert result.status == "done"
    assert result.result.content == {"choice_id": "accept"}

    with pytest.raises(Exception) as exc_info:
        await get_character_agent_query_job(
            "agent-1", 42, SimpleNamespace(id=2, role="player"), object()
        )
    assert exc_info.value.status_code == 403
