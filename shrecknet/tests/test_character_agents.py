from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text

from app.db.migrations import migrate_remove_legacy_character_agents
from app.graph.neo4j import ensure_character_graph_constraints
from app.schemas.character_agent import (
    CharacterAgentCreate,
    CharacterAgentUpdate,
    CharacterAspectAssignmentCreate,
    CharacterAspectCreate,
    CharacterGoalCreate,
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
