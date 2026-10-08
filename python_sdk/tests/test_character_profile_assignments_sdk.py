from datetime import datetime, timezone

import pytest

from shrecknet_client.models import (
    CharacterAspectAssignmentCreate,
    CharacterAspectAssignmentUpdate,
    CharacterGoalAssignmentCreate,
    CharacterGoalAssignmentUpdate,
)
from shrecknet_client.resources import CharacterAgentsAPI


NOW = datetime.now(timezone.utc).isoformat()


class Client:
    def __init__(self):
        self.calls = []

    async def raw_request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if "/aspects" in path:
            return {
                "aspect": {
                    "id": "aspect-1", "ontology_id": 2, "name": "I lead the town",
                    "normalized_name": "i lead the town", "category": "role",
                    "created_at": NOW, "updated_at": NOW,
                },
                "status": "active", "in_focus": True, "evidence_ids": [],
                "created_at": NOW, "updated_at": NOW,
            }
        return {
            "goal": {
                "id": "goal-1", "ontology_id": 2, "title": "Protect the town",
                "goal_type": "obligation", "created_at": NOW, "updated_at": NOW,
            },
            "status": "active", "in_focus": False, "evidence_ids": [],
            "created_at": NOW, "updated_at": NOW,
        }


@pytest.mark.asyncio
async def test_sdk_uses_relationship_owned_focus_and_lifecycle_contract():
    client = Client()
    api = CharacterAgentsAPI(client)
    aspect = await api.assign_aspect("agent-1", CharacterAspectAssignmentCreate(
        character_aspect_id="aspect-1", status="active", in_focus=True,
    ))
    assert aspect.in_focus is True
    await api.update_aspect_assignment("agent-1", "aspect-1", CharacterAspectAssignmentUpdate(
        in_focus=False,
    ))
    goal = await api.pursue_goal("agent-1", CharacterGoalAssignmentCreate(character_goal_id="goal-1"))
    assert goal.status == "active"
    await api.update_goal_assignment("agent-1", "goal-1", CharacterGoalAssignmentUpdate(
        status="completed", in_focus=False,
    ))
    assert [call[:2] for call in client.calls] == [
        ("POST", "/character-agents/agent-1/aspects"),
        ("PATCH", "/character-agents/agent-1/aspects/aspect-1"),
        ("POST", "/character-agents/agent-1/goals"),
        ("PATCH", "/character-agents/agent-1/goals/goal-1"),
    ]
