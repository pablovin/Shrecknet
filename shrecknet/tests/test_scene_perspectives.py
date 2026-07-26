from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.graph.neo4j import ensure_character_graph_constraints
from app.db.migrations import migrate_scene_perspective_audit_types
from app.schemas.character_agent import (
    CharacterImpactCreate,
    CharacterImpactUpdate,
    EmotionalInterpretationCreate,
    ScenePerspectiveCreate,
    ScenePerspectiveUpdate,
)
from app.services.character_agent_service import CharacterAgentService
from app.api.routers.character_agents import get_perspective, list_perspectives
from sqlalchemy import create_engine


def _perspective(**overrides):
    values = {
        "scene_id": "scene-1",
        "source_type": "witnessed",
        "awareness_level": 80,
        "confidence": 70,
        "summary": "The guard fell.",
        "interpretation": "The keep is no longer safe.",
        "memory_strength": 90,
        "importance": 5,
    }
    values.update(overrides)
    return ScenePerspectiveCreate(**values)


def test_scene_perspective_contract_is_strict_and_ownership_is_immutable():
    perspective = _perspective(summary="  The guard fell.  ")
    assert perspective.summary == "The guard fell."
    assert perspective.status.value == "active"

    with pytest.raises(ValidationError):
        _perspective(awareness_level=101)
    with pytest.raises(ValidationError):
        _perspective(importance=0)
    with pytest.raises(ValidationError):
        _perspective(summary=" ")
    with pytest.raises(ValidationError):
        ScenePerspectiveUpdate(scene_id="other")


def test_emotion_and_impact_validation():
    emotion = EmotionalInterpretationCreate(
        arousal=85, valence=2, description="  Angry and frustrated. "
    )
    assert emotion.description == "Angry and frustrated."
    with pytest.raises(ValidationError):
        EmotionalInterpretationCreate(
            arousal=-1, valence=50, description="Invalid"
        )

    goal = CharacterImpactCreate(
        impact_type="goal_change",
        direction="advanced",
        magnitude=80,
        description="The confession strengthens the need for justice.",
        target_id="goal-1",
        caused_by_milestone_id="milestone-1",
    )
    assert goal.direction.value == "advanced"
    with pytest.raises(ValidationError):
        CharacterImpactCreate(
            impact_type="goal_change",
            direction="reinforced",
            magnitude=80,
            description="Wrong target semantics.",
            target_id="goal-1",
        )
    with pytest.raises(ValidationError):
        CharacterImpactUpdate(direction="made_up")


def test_audit_enum_migration_is_safe_on_sqlite():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        migrate_scene_perspective_audit_types(connection)


class _ConstraintSession:
    def __init__(self):
        self.statements: list[str] = []

    async def run(self, statement):
        self.statements.append(" ".join(statement.split()))


@pytest.mark.asyncio
async def test_perspective_constraints_and_indexes_are_idempotent():
    session = _ConstraintSession()
    await ensure_character_graph_constraints(session)
    first = list(session.statements)
    await ensure_character_graph_constraints(session)

    assert session.statements == first + first
    joined = "\n".join(first)
    assert "ScenePerspective) REQUIRE n.id IS UNIQUE" in joined
    assert (
        "REQUIRE (n.character_agent_id, n.scene_id) IS UNIQUE" in joined
    )
    assert "EmotionalInterpretation) REQUIRE n.id IS UNIQUE" in joined
    assert "CharacterBelief) REQUIRE n.id IS UNIQUE" in joined
    assert "CharacterImpact) REQUIRE n.id IS UNIQUE" in joined
    assert "ON (n.character_agent_id)" in joined
    assert "ON (n.scene_id)" in joined


class _Result:
    def __init__(self, row):
        self.row = row

    async def single(self):
        return self.row


class _PerspectiveTx:
    def __init__(self, *, eligible=True, duplicate=False, scene_ontology=12):
        self.eligible = eligible
        self.duplicate = duplicate
        self.scene_ontology = scene_ontology
        self.created_props = None

    async def run(self, query, **params):
        if "RETURN agent, entity, scene" in query:
            return _Result({
                "agent": {"id": "agent-1", "ontology_id": 12},
                "entity": {
                    "entity_instance_id": "entity-1",
                    "ontology_id": 12,
                    "instance_id": "instance-1",
                },
                "scene": {
                    "id": "scene-1",
                    "ontology_id": self.scene_ontology,
                    "instance_id": "instance-1",
                },
                "eligible": self.eligible,
                "duplicate": self.duplicate,
            })
        self.created_props = params["props"]
        return _Result({"node": params["props"]})


class _PerspectiveGraph:
    def __init__(self, tx):
        self.tx = tx

    async def execute_write(self, callback):
        return await callback(self.tx)


@pytest.mark.asyncio
async def test_create_perspective_validates_scope_eligibility_and_duplicates(monkeypatch):
    tx = _PerspectiveTx()
    service = CharacterAgentService(None, _PerspectiveGraph(tx))

    async def read_created(agent_id, perspective_id, public_only=False):
        return {"id": perspective_id, "character_agent_id": agent_id}

    monkeypatch.setattr(service, "get_perspective", read_created)
    result = await service.create_perspective("agent-1", _perspective())
    assert result["character_agent_id"] == "agent-1"
    assert tx.created_props["scene_id"] == "scene-1"
    assert tx.created_props["ontology_id"] == 12

    for tx, expected in (
        (_PerspectiveTx(eligible=False), "not linked"),
        (_PerspectiveTx(duplicate=True), "already has"),
        (_PerspectiveTx(scene_ontology=13), "share ontology"),
    ):
        service = CharacterAgentService(None, _PerspectiveGraph(tx))
        with pytest.raises(HTTPException, match=expected):
            await service.create_perspective("agent-1", _perspective())


class _ReadService:
    def __init__(self):
        self.calls = []

    async def list_perspectives(self, *args, **kwargs):
        self.calls.append(("list", args, kwargs))
        return []

    async def get_perspective(self, *args, **kwargs):
        self.calls.append(("get", args, kwargs))
        return {}


@pytest.mark.asyncio
async def test_perspective_reads_mirror_character_visibility():
    service = _ReadService()
    player = type("User", (), {"role": "player"})()
    admin = type("User", (), {"role": "admin"})()

    await list_perspectives("agent-1", None, 0, 50, player, service)
    await get_perspective("agent-1", "perspective-1", player, service)
    await list_perspectives("agent-1", None, 0, 50, admin, service)

    assert service.calls[0][2]["public_only"] is True
    assert service.calls[1][2]["public_only"] is True
    assert service.calls[2][2]["public_only"] is False
