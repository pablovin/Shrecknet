from __future__ import annotations

from datetime import datetime, timezone

import pytest

from shrecknet_client.models import (
    CharacterImpactCreate,
    CharacterImpactUpdate,
    ScenePerspectiveCreate,
)
from shrecknet_client.resources import CharacterAgentsAPI


NOW = datetime.now(timezone.utc).isoformat()


class Client:
    def __init__(self):
        self.calls = []

    async def raw_request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path.endswith("/impacts"):
            return {
                "id": "impact-1",
                "ontology_id": 12,
                "impact_type": "goal_change",
                "direction": "advanced",
                "magnitude": 80,
                "description": "Strengthened resolve.",
                "target_id": "goal-1",
                "target_type": "goal",
                "caused_by_milestone_id": "milestone-1",
                "created_at": NOW,
                "updated_at": NOW,
            }
        if "/impacts/" in path:
            return {
                "id": "impact-1",
                "ontology_id": 12,
                "impact_type": "goal_change",
                "direction": "advanced",
                "magnitude": 75,
                "description": "Strengthened resolve.",
                "target_id": "goal-1",
                "target_type": "goal",
                "caused_by_milestone_id": None,
                "created_at": NOW,
                "updated_at": NOW,
            }
        return {
            "id": "perspective-1",
            "ontology_id": 12,
            "character_agent_id": "agent-1",
            "scene_id": "scene-1",
            "source_type": "witnessed",
            "awareness_level": 80,
            "confidence": 70,
            "summary": "The guard fell.",
            "interpretation": "The keep is unsafe.",
            "memory_strength": 90,
            "importance": 5,
            "status": "active",
            "created_at": NOW,
            "updated_at": NOW,
            "emotions": [],
            "beliefs": [],
            "impacts": [],
        }


@pytest.mark.asyncio
async def test_scene_perspective_and_impact_sdk_contracts():
    client = Client()
    api = CharacterAgentsAPI(client)
    perspective = await api.create_perspective(
        "agent-1",
        ScenePerspectiveCreate(
            scene_id="scene-1",
            source_type="witnessed",
            awareness_level=80,
            confidence=70,
            summary="The guard fell.",
            interpretation="The keep is unsafe.",
            memory_strength=90,
            importance=5,
        ),
    )
    assert perspective.id == "perspective-1"
    assert client.calls[-1][0:2] == (
        "POST", "/character-agents/agent-1/perspectives"
    )

    impact = await api.create_impact(
        "agent-1",
        "perspective-1",
        CharacterImpactCreate(
            impact_type="goal_change",
            direction="advanced",
            magnitude=80,
            description="Strengthened resolve.",
            target_id="goal-1",
            caused_by_milestone_id="milestone-1",
        ),
    )
    assert impact.target_type == "goal"
    assert client.calls[-1][0:2] == (
        "POST",
        "/character-agents/agent-1/perspectives/perspective-1/impacts",
    )

    await api.update_impact(
        "agent-1",
        "perspective-1",
        "impact-1",
        CharacterImpactUpdate(
            magnitude=75, caused_by_milestone_id=None
        ),
    )
    assert client.calls[-1][2]["json"]["caused_by_milestone_id"] is None
