from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.core.config_store import LLMModelTarget, Settings
from app.jobs.character_agent.query import CharacterAgentQueryJob, CharacterGenerationError
from app.jobs.character_agent.prompts import DELIBERATION_PROMPT, FRAME_PROMPT, VERIFY_PROMPT
from app.jobs.shrecknet.agent import parse_json_deterministically
from app.schemas.character_agent import (
    CharacterAgentCreate,
    CharacterAgentQueryRequest,
    CharacterAspectAssignmentCreate,
    CharacterAspectAssignmentUpdate,
)
from app.services.character_agent_service import CharacterAgentService


SNAPSHOT = {
    "character_agent": {
        "name": "Mara", "background_story": "A guarded ruler.",
        "behavioural_traits": {
            "calm_aggressive": 80, "cautious_reckless": 65,
            "compassionate_ruthless": 30, "trusting_suspicious": 75,
            "honest_deceptive": 45, "patient_impulsive": 70,
            "humble_proud": 60, "cooperative_dominating": 70,
        },
        "trait_adherence": 80,
    },
    "aspects": [{"id": "aspect-1", "name": "Negotiator", "description": "Skilled at negotiation",
                 "category": "capability", "importance": 5, "intensity": 80, "notes": None}],
    "goals": [{"id": "goal-1", "title": "Protect villagers", "description": None,
               "goal_type": "obligation", "status": "active", "priority": 95, "commitment": 90}],
}


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _frame(**changes):
    value = {
        "task_type": "dialogue", "task_summary": "Reply", "mandatory_instructions": [],
        "relevant_trait_axes": [{"trait": "trusting_suspicious", "relevance": 90, "reason": "Threat"}],
        "relevant_aspect_ids": ["aspect-1"], "relevant_goal_ids": ["goal-1"],
        "character_conflicts": [], "unknowns": [], "explicit_options": [],
    }
    value.update(changes)
    return json.dumps(value)


DELIBERATION = json.dumps({
    "interpretation": "A threat", "candidate_responses": [{
        "candidate": "Refuse", "goal_alignment": 90, "aspect_alignment": 70,
        "trait_alignment": 80, "feasibility": 60, "overall_preference": 82,
        "supporting_ids": ["goal-1"],
    }], "preferred_response": "Refuse", "internal_conflict": None,
    "decision_basis": ["Protect villagers"], "confidence": 82,
})


@pytest.mark.asyncio
async def test_query_uses_exactly_three_calls_and_validates_json_contract():
    verified = json.dumps({
        "claim_assessments": [{"claim": "I refuse.", "classification": "creative_expression", "supporting_ids": []}],
        "unsupported_claims_removed": [],
        "rendered_response": {"spoken_response": "I refuse.", "confidence": 82},
    })
    llm = FakeLLM([_frame(), DELIBERATION, verified])
    target = LLMModelTarget(provider="test", name="model")
    job = CharacterAgentQueryJob(llm_client=llm, framing_model=target, deliberation_model=target, verification_model=target)
    request = CharacterAgentQueryRequest.model_validate({
        "query": "Answer the threat", "response_format": {"type": "json", "schema": {
            "type": "object", "required": ["spoken_response", "confidence"],
            "properties": {"spoken_response": {"type": "string"}, "confidence": {"type": "integer"}},
            "additionalProperties": False,
        }},
    })
    result = await job.run(request, SNAPSHOT)
    assert result.content["confidence"] == 82
    assert len(llm.calls) == 3
    assert llm.calls[1]["temperature"] == 0.7
    assert "complete_character_profile" not in llm.calls[1]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_query_rejects_invented_evidence_without_repair_call():
    llm = FakeLLM([_frame(relevant_goal_ids=["invented-goal"])])
    target = LLMModelTarget(provider="test", name="model")
    job = CharacterAgentQueryJob(llm_client=llm, framing_model=target, deliberation_model=target, verification_model=target)
    with pytest.raises(CharacterGenerationError):
        await job.run(CharacterAgentQueryRequest(query="Write a letter"), SNAPSHOT)
    assert len(llm.calls) == 1


def test_deterministic_json_parser_handles_fences_and_prefixes():
    assert parse_json_deterministically("```json\n{\"ok\": true}\n```") == {"ok": True}
    assert parse_json_deterministically("Result: {\"ok\": true} trailing") == {"ok": True}


def test_prompts_embed_complete_stage_contracts():
    assert "Stage 1 of 3" in FRAME_PROMPT
    assert '"complete_character_profile"' in FRAME_PROMPT
    assert '"relevant_goal_ids"' in FRAME_PROMPT
    assert "Stage 2 of 3" in DELIBERATION_PROMPT
    assert '"relevant_character_evidence"' in DELIBERATION_PROMPT
    assert '"overall_preference"' in DELIBERATION_PROMPT
    assert "Stage 3 of 3" in VERIFY_PROMPT
    assert '"supporting_evidence"' in VERIFY_PROMPT
    assert '"rendered_response"' in VERIFY_PROMPT


def test_character_agent_defaults_and_configuration_targets():
    agent = CharacterAgentCreate(ontology_id=1, entity_instance_id="entity")
    assert agent.trait_adherence == 80
    settings = Settings()
    assert settings.model_character_agent_framing == LLMModelTarget(provider="", name="")
    assert settings.model_character_agent_deliberation == LLMModelTarget(provider="", name="")
    assert settings.model_character_agent_verification == LLMModelTarget(provider="", name="")
    assert settings.model_character_agent_embodiment == LLMModelTarget(provider="", name="")


class _Result:
    def __init__(self, row):
        self.row = row

    async def single(self):
        return self.row


class _Graph:
    def __init__(self, row):
        self.row = row
        self.calls = []

    async def run(self, statement, **params):
        self.calls.append((statement, params))
        return _Result(self.row)


class _EmptyResult:
    async def single(self):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _EmptyGraph:
    def __init__(self):
        self.calls = []

    async def run(self, statement, **params):
        self.calls.append((statement, params))
        return _EmptyResult()


@pytest.mark.asyncio
async def test_snapshot_is_one_operation_and_omits_backend_identifiers():
    row = {
        "agent": {"id": "agent-1", "status": "active", "name": "Mara", "background_story": "Story"},
        "entity": {"entity_instance_id": "entity-1", "alias": "Mara"},
        "aspects": SNAPSHOT["aspects"], "goals": SNAPSHOT["goals"],
    }
    graph = _Graph(row)
    snapshot = await CharacterAgentService(object(), graph).load_query_snapshot(
        "agent-1", public_only=True
    )
    assert len(graph.calls) == 1
    assert "coalesce(agent.visibility, 'private') = 'public'" in graph.calls[0][0]
    assert graph.calls[0][1]["public_only"] is True
    assert "ORDER BY assignment.importance DESC" in graph.calls[0][0]
    assert "ORDER BY goal.priority DESC" in graph.calls[0][0]
    assert "id" not in snapshot["character_agent"]
    assert snapshot["character_agent"]["trait_adherence"] == 80


@pytest.mark.asyncio
async def test_public_reads_treat_legacy_agents_as_private():
    graph = _Graph(None)
    service = CharacterAgentService(object(), graph)

    with pytest.raises(HTTPException) as exc_info:
        await service.get_agent("legacy-agent", public_only=True)

    assert exc_info.value.status_code == 404
    statement, params = graph.calls[0]
    assert "coalesce(node.visibility, 'private') = 'public'" in statement
    assert params["public_only"] is True


@pytest.mark.asyncio
async def test_public_list_filters_visibility_in_the_graph_query():
    graph = _EmptyGraph()

    result = await CharacterAgentService(object(), graph).list_agents(
        ontology_id=None,
        agent_status=None,
        entity_id=None,
        skip=0,
        limit=50,
        public_only=True,
    )

    assert result == []
    assert "coalesce(agent.visibility, 'private') = 'public'" in graph.calls[0][0]


@pytest.mark.asyncio
async def test_relationship_writes_separate_set_or_create_from_optional_match():
    timestamp = "2026-07-20T12:00:00+00:00"
    aspect = {
        "id": "aspect-1", "ontology_id": 1, "name": "Negotiator",
        "normalized_name": "negotiator", "category": "capability",
        "description": None, "status": "active", "created_at": timestamp,
        "updated_at": timestamp,
    }
    assignment_row = {
        "aspect": aspect,
        "rel": {"importance": 5, "intensity": 80, "notes": None,
                "status": "active", "created_at": timestamp, "updated_at": timestamp},
        "obtained_from_scene_id": None,
    }
    graph = _Graph(assignment_row)
    service = CharacterAgentService(object(), graph)
    await service.assign_aspect(
        "agent-1",
        CharacterAspectAssignmentCreate(character_aspect_id="aspect-1", importance=5, intensity=80),
    )
    assert "SET rel = $values\n            WITH aspect, rel\n            OPTIONAL MATCH" in graph.calls[0][0]

    graph.calls.clear()
    await service.update_assignment(
        "agent-1", "aspect-1", CharacterAspectAssignmentUpdate(importance=4)
    )
    compact = " ".join(graph.calls[0][0].split())
    assert "SET rel += $changes WITH aspect, rel OPTIONAL MATCH" in compact

    goal = {
        "id": "goal-1", "ontology_id": 1, "title": "Protect villagers",
        "description": None, "goal_type": "obligation", "status": "active",
        "priority": 90, "commitment": 90, "created_at": timestamp, "updated_at": timestamp,
    }
    goal_graph = _Graph({"node": goal, "obtained_from_scene_id": None})
    await CharacterAgentService(object(), goal_graph).pursue_goal("agent-1", "goal-1")
    goal_query = " ".join(goal_graph.calls[0][0].split())
    assert "CREATE (agent)-[:PURSUES {created_at:$now}]->(goal) WITH goal OPTIONAL MATCH" in goal_query
