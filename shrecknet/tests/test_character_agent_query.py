from __future__ import annotations

import json

import pytest
from fastapi import HTTPException
from pydantic import ValidationError as PydanticValidationError

from app.core.config_store import LLMModelTarget, Settings
from app.jobs.character_agent.query import CharacterAgentQueryJob, CharacterGenerationError
from app.jobs.character_agent.prompts import (
    DELIBERATION_PROMPT,
    FRAME_PROMPT,
    GENERIC_FRAME_PROMPT,
    GENERIC_QUERY_PROMPT,
)
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
        "context_summary": "A threat must be answered.",
        "relevant_trait_axes": ["trusting_suspicious"],
        "relevant_aspect_ids": ["aspect-1"], "relevant_goal_ids": ["goal-1"],
        "conflicts": [], "unknowns": [],
    }
    value.update(changes)
    return json.dumps(value)


DELIBERATION = json.dumps({
    "content": {"spoken_response": "I refuse.", "confidence": 82},
    "decision_basis": "Protecting the villagers is the priority.",
})
TEXT_DELIBERATION = json.dumps({
    "content": "I refuse.",
    "decision_basis": "Protecting the villagers is the priority.",
})


@pytest.mark.asyncio
async def test_query_uses_exactly_two_calls_and_passes_lean_stage_two_payload():
    llm = FakeLLM([_frame(), DELIBERATION])
    stages = []

    async def report(stage, progress):
        stages.append((stage, progress))

    target = LLMModelTarget(provider="test", name="model")
    job = CharacterAgentQueryJob(
        llm_client=llm, framing_model=target, deliberation_model=target,
        repair_model=target,
        report_stage=report,
    )
    request = CharacterAgentQueryRequest.model_validate({
        "query": "Answer the threat", "response_format": {"type": "json", "schema": {
            "type": "object", "required": ["spoken_response", "confidence"],
            "properties": {"spoken_response": {"type": "string"}, "confidence": {"type": "integer"}},
            "additionalProperties": False,
        }},
    })
    result = await job.run(request, SNAPSHOT)
    assert result.content["confidence"] == 82
    assert result.decision_basis == "Protecting the villagers is the priority."
    assert len(llm.calls) == 2
    assert [stage for stage, _ in stages] == [
        "framing", "deliberating", "validating"
    ]
    assert llm.calls[1]["temperature"] == 0.7
    assert all("max_tokens" not in call for call in llm.calls)
    framing = json.loads(llm.calls[0]["messages"][1]["content"])
    assert set(framing) == {"query", "context", "agent_profile"}
    assert framing["agent_profile"]["active_aspects"] == [
        {"id": "aspect-1", "name": "Negotiator"}
    ]
    deliberation = json.loads(llm.calls[1]["messages"][1]["content"])
    assert set(deliberation) == {
        "query", "context_summary", "system_instruction", "relevant_trait_axes",
        "relevant_aspect_names", "relevant_goal_names", "conflicts", "unknowns",
        "response_format",
    }
    encoded = json.dumps(deliberation)
    for excluded in (
        "background_story", "trait_adherence", "aspect-1", "goal-1",
        "Skilled at negotiation", "Protect villagers\":",
    ):
        assert excluded not in encoded


@pytest.mark.parametrize("removed_field", ["mode", "max_tokens"])
def test_query_rejects_removed_generation_fields(removed_field):
    with pytest.raises(PydanticValidationError):
        CharacterAgentQueryRequest.model_validate({
            "query": "Reply briefly",
            "generation": {removed_field: "simulation" if removed_field == "mode" else 32},
        })


@pytest.mark.asyncio
async def test_query_without_character_identity_uses_neutral_framing_then_deliberation():
    llm = FakeLLM([_frame(
        relevant_trait_axes=[],
        relevant_aspect_ids=[],
        relevant_goal_ids=[],
    ), json.dumps({
        "content": "A neutral response.",
        "decision_basis": "The supplied facts support a neutral assessment.",
    })])
    target = LLMModelTarget(provider="test", name="model")
    job = CharacterAgentQueryJob(
        llm_client=llm,
        framing_model=target,
        deliberation_model=target,
        repair_model=target,
    )
    request = CharacterAgentQueryRequest(
        query="Assess the treaty",
        use_character_identity=False,
        context={"supply_days": 14},
        system_instruction="Be concise.",
    )

    result = await job.run(request)

    assert result.model_dump() == {
        "type": "text",
        "content": "A neutral response.",
        "decision_basis": "The supplied facts support a neutral assessment.",
    }
    assert len(llm.calls) == 2
    assert [call["model"] for call in llm.calls] == [target, target]
    assert [call["usage_tag"] for call in llm.calls] == [
        "character_agent.generic_frame",
        "character_agent.generic_deliberate",
    ]
    assert all("max_tokens" not in call for call in llm.calls)
    framing = json.loads(llm.calls[0]["messages"][1]["content"])
    assert framing == {
        "query": "Assess the treaty",
        "context": {"supply_days": 14},
    }
    deliberation = json.loads(llm.calls[1]["messages"][1]["content"])
    assert set(deliberation) == {
        "query", "context_summary", "system_instruction", "conflicts",
        "unknowns", "response_format",
    }
    assert deliberation["context_summary"] == "A threat must be answered."
    encoded_calls = json.dumps([call["messages"] for call in llm.calls])
    assert "agent_profile" not in encoded_calls
    assert "background_story" not in encoded_calls
    assert "behavioural_traits" not in encoded_calls


@pytest.mark.asyncio
async def test_query_without_character_identity_validates_json_contract():
    llm = FakeLLM([_frame(
        relevant_trait_axes=[],
        relevant_aspect_ids=[],
        relevant_goal_ids=[],
    ), json.dumps({
        "content": {"choice": "accept"},
        "decision_basis": "Accept is best supported.",
    })])
    target = LLMModelTarget(provider="test", name="model")
    job = CharacterAgentQueryJob(
        llm_client=llm,
        framing_model=target,
        deliberation_model=target,
        repair_model=target,
    )
    request = CharacterAgentQueryRequest.model_validate({
        "query": "Choose",
        "use_character_identity": False,
        "response_format": {
            "type": "json",
            "schema": {
                "type": "object",
                "required": ["choice"],
                "properties": {"choice": {"enum": ["accept", "reject"]}},
                "additionalProperties": False,
            },
        },
    })

    result = await job.run(request)

    assert result.content == {"choice": "accept"}
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_query_without_character_identity_rejects_identity_selectors():
    llm = FakeLLM([_frame(relevant_goal_ids=["goal-1"])])
    target = LLMModelTarget(provider="test", name="model")
    job = CharacterAgentQueryJob(
        llm_client=llm,
        framing_model=target,
        deliberation_model=target,
        repair_model=target,
    )

    with pytest.raises(
        CharacterGenerationError, match="generic task framing returned identity selectors"
    ):
        await job.run(CharacterAgentQueryRequest(
            query="Assess the treaty", use_character_identity=False
        ))
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_query_discards_unknown_identity_selectors_and_continues():
    llm = FakeLLM([
        _frame(
            relevant_aspect_ids=["invented-aspect"],
            relevant_goal_ids=["invented-goal"],
        ),
        TEXT_DELIBERATION,
    ])
    target = LLMModelTarget(provider="test", name="model")
    job = CharacterAgentQueryJob(
        llm_client=llm, framing_model=target, deliberation_model=target,
        repair_model=target,
    )
    result = await job.run(CharacterAgentQueryRequest(query="Write a letter"), SNAPSHOT)

    assert result.content == "I refuse."
    deliberation = json.loads(llm.calls[1]["messages"][1]["content"])
    assert deliberation["relevant_aspect_names"] == []
    assert deliberation["relevant_goal_names"] == []
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_query_resolves_unique_exact_identity_names_to_active_ids():
    llm = FakeLLM([
        _frame(
            relevant_aspect_ids=["  NEGOTIATOR "],
            relevant_goal_ids=["protect   VILLAGERS"],
        ),
        TEXT_DELIBERATION,
    ])
    target = LLMModelTarget(provider="test", name="model")
    job = CharacterAgentQueryJob(
        llm_client=llm, framing_model=target, deliberation_model=target,
        repair_model=target,
    )

    await job.run(CharacterAgentQueryRequest(query="Write a letter"), SNAPSHOT)

    deliberation = json.loads(llm.calls[1]["messages"][1]["content"])
    assert deliberation["relevant_aspect_names"] == ["Negotiator"]
    assert deliberation["relevant_goal_names"] == ["Protect villagers"]


def test_query_does_not_infer_ambiguous_goal_name():
    snapshot = {
        **SNAPSHOT,
        "goals": [
            {**SNAPSHOT["goals"][0], "id": "goal-1"},
            {**SNAPSHOT["goals"][0], "id": "goal-2"},
        ],
    }
    frame = CharacterAgentQueryJob._parse_frame(
        _frame(relevant_goal_ids=["Protect villagers"])
    )

    CharacterAgentQueryJob._validate_selectors(frame, snapshot)

    assert frame.relevant_goal_ids == []


@pytest.mark.asyncio
async def test_query_uses_global_repair_target_once_and_validates_repair():
    repaired = json.dumps({
        "content": {"choice": "accept"},
        "decision_basis": "Accept is best supported.",
    })
    llm = FakeLLM([_frame(), "not json", repaired])
    normal_target = LLMModelTarget(provider="test", name="normal")
    repair_target = LLMModelTarget(provider="test", name="global-repair")
    job = CharacterAgentQueryJob(
        llm_client=llm, framing_model=normal_target,
        deliberation_model=normal_target, repair_model=repair_target,
    )
    request = CharacterAgentQueryRequest.model_validate({
        "query": "Choose",
        "response_format": {"type": "json", "schema": {
            "type": "object", "required": ["choice"],
            "properties": {"choice": {"enum": ["accept", "reject"]}},
        }},
    })
    result = await job.run(request, SNAPSHOT)
    assert result.content == {"choice": "accept"}
    assert [call["usage_tag"] for call in llm.calls] == [
        "character_agent.frame",
        "character_agent.deliberate",
        "character_agent.repair",
    ]
    assert llm.calls[2]["model"] == repair_target


@pytest.mark.asyncio
async def test_query_caps_rationale_at_2000_without_repair_or_failure():
    long_rationale = "r" * 2_500
    llm = FakeLLM([
        _frame(),
        json.dumps({
            "content": {"choice": "investigate", "rationale": long_rationale},
            "decision_basis": "The supplied evidence supports investigation.",
        }),
    ])
    target = LLMModelTarget(provider="test", name="model")
    job = CharacterAgentQueryJob(
        llm_client=llm,
        framing_model=target,
        deliberation_model=target,
        repair_model=target,
    )
    request = CharacterAgentQueryRequest.model_validate({
        "query": "Choose",
        "response_format": {"type": "json", "schema": {
            "type": "object",
            "required": ["choice", "rationale"],
            "properties": {
                "choice": {"type": "string"},
                "rationale": {"type": "string", "maxLength": 500},
            },
            "additionalProperties": False,
        }},
    })

    result = await job.run(request, SNAPSHOT)

    assert result.content["rationale"] == "r" * 2_000
    assert len(llm.calls) == 2
    deliberation = json.loads(llm.calls[1]["messages"][1]["content"])
    assert (
        deliberation["response_format"]["schema"]["properties"]["rationale"][
            "maxLength"
        ]
        == 2_000
    )


def test_rationale_cap_applies_to_nested_objects_and_preserves_short_values():
    content = {
        "rationale": "short",
        "decisions": [{"rationale": "x" * 2_001}],
    }

    normalized = CharacterAgentQueryJob._cap_rationale(content)

    assert normalized["rationale"] == "short"
    assert normalized["decisions"][0]["rationale"] == "x" * 2_000
    assert content["decisions"][0]["rationale"] == "x" * 2_001


@pytest.mark.asyncio
async def test_query_repair_hint_describes_the_top_level_envelope():
    repaired = json.dumps({
        "content": {"choice": "accept"},
        "decision_basis": "Accept is best supported.",
    })
    llm = FakeLLM([_frame(), "not json", repaired])
    target = LLMModelTarget(provider="test", name="model")
    job = CharacterAgentQueryJob(
        llm_client=llm,
        framing_model=target,
        deliberation_model=target,
        repair_model=target,
    )
    request = CharacterAgentQueryRequest.model_validate({
        "query": "Choose",
        "response_format": {"type": "json", "schema": {
            "type": "object",
            "required": ["choice"],
            "properties": {"choice": {"enum": ["accept", "reject"]}},
            "additionalProperties": False,
        }},
    })

    await job.run(request, SNAPSHOT)

    repair_prompt = llm.calls[2]["messages"][0]["content"]
    schema_hint = repair_prompt.split(
        "Expected schema hint:\n", 1
    )[1].split("\nMalformed JSON:", 1)[0]
    schema = json.loads(schema_hint)
    assert set(schema["properties"]) == {"content", "decision_basis"}
    assert schema["properties"]["content"] == request.response_format.schema_
    assert "envelope" not in schema


def test_deterministic_json_parser_handles_fences_and_prefixes():
    assert parse_json_deterministically("```json\n{\"ok\": true}\n```") == {"ok": True}
    assert parse_json_deterministically("Result: {\"ok\": true} trailing") == {"ok": True}


def test_prompts_embed_complete_stage_contracts():
    assert "general-purpose backend response generator" in GENERIC_QUERY_PROMPT
    assert "neutral context summarization" in GENERIC_FRAME_PROMPT
    assert '"relevant_goal_ids": []' in GENERIC_FRAME_PROMPT
    assert "Stage 1 of 2" in FRAME_PROMPT
    assert '"agent_profile"' in FRAME_PROMPT
    assert '"relevant_goal_ids"' in FRAME_PROMPT
    assert "Stage 2 of 2" in DELIBERATION_PROMPT
    assert '"context_summary"' in DELIBERATION_PROMPT
    assert '"decision_basis"' in DELIBERATION_PROMPT


def test_character_agent_defaults_and_configuration_targets():
    agent = CharacterAgentCreate(ontology_id=1, entity_instance_id="entity")
    assert agent.trait_adherence == 80
    assert CharacterAgentQueryRequest(query="Reply").use_character_identity is True
    settings = Settings()
    assert settings.model_character_agent_framing == LLMModelTarget(provider="", name="")
    assert settings.model_character_agent_deliberation == LLMModelTarget(provider="", name="")
    assert not hasattr(settings, "model_character_agent_verification")
    assert settings.model_character_agent_character_incorporation == LLMModelTarget(provider="", name="")
    assert settings.model_character_agent_scene_interpretation == LLMModelTarget(provider="", name="")
    assert settings.model_character_agent_update == LLMModelTarget(provider="", name="")


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
async def test_generic_access_check_loads_no_character_identity():
    graph = _Graph({"status": "active"})

    await CharacterAgentService(object(), graph).ensure_queryable(
        "agent-1", public_only=True
    )

    assert len(graph.calls) == 1
    statement, params = graph.calls[0]
    assert "RETURN agent.status AS status" in statement
    assert "background_story" not in statement
    assert "HAS_ASPECT" not in statement
    assert "PURSUES" not in statement
    assert params == {"node_id": "agent-1", "public_only": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "expected_status"),
    [(None, 404), ({"status": "inactive"}, 409)],
)
async def test_generic_access_check_preserves_query_errors(row, expected_status):
    with pytest.raises(HTTPException) as exc_info:
        await CharacterAgentService(object(), _Graph(row)).ensure_queryable(
            "agent-1", public_only=False
        )

    assert exc_info.value.status_code == expected_status


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
