"""Regression tests for the scene perspective and psychological embodiment pipeline."""

import asyncio
import json

import pytest

from app.jobs.character_agent.embody_agent import (
    EmbodyAgent,
    EmbodimentGenerationError,
    _SceneEnrichmentsLLMOutput,
    _bind_llm_enrichments,
)
from app.jobs.character_agent.profile import _apply_aspect_ops, _apply_goal_ops
from app.jobs.character_agent.embody_agent_prompts import (
    PSYCHOLOGICAL_ANALYSIS_PROMPT,
    PSYCHOLOGICAL_CONSOLIDATION_PROMPT,
    PERSPECTIVE_PROMPT,
    PROMPT_VERSION,
    TRAIT_INTERPRETATION_PROMPT,
)
from app.schemas.character_agent import (
    AspectUpdateData,
    EmbodyAgentAnalysis,
    EmbodimentObservationsOutput,
    GoalUpdateData,
    ProfileEventOutput,
    ScenePerspectiveOutput,
    SceneInput,
)
from app.schemas.character_traits import TraitProfile
from app.tasks.character_embodiment import _public_error_message


class FakeLLM:
    def __init__(self, response="{}"):
        self.response = response
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _analysis(events):
    return EmbodyAgentAnalysis(
        source_entity_id="source", source_entity_alias="Source", perspectives=[],
        observations=EmbodimentObservationsOutput(), profile_events=events, llm_calls=[],
    )


def _agent(llm):
    return EmbodyAgent(
        llm_client=llm, character_incorporation_model="model",
        scene_interpretation_model="model",
    )


def _canonical(overrides=None):
    value = {
        "entity_instance_id": "e1", "alias": "Mara", "entity_type": "Character",
        "entity_type_description": "A person in the story.", "properties": {},
        "authored_text": "Mara is a person.", "generated_text": "",
    }
    value.update(overrides or {})
    return value


def scenes(count, offset=0):
    return [SceneInput(
        scene_id=f"s{i}", name="Choice",
        description="Mara faces a meaningful choice.", created_at=f"{i:03}",
    ) for i in range(offset, offset + count)]


class BatchLLM:
    """Small deterministic provider fixture used by adjacent persistence tests."""

    def __init__(self, corruption=None):
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        payload = json.loads(kwargs["messages"][1]["content"])
        tag = kwargs["usage_tag"]
        if tag.endswith("character_incorporation"):
            return json.dumps({"perspectives": [{
                "source_type": "participated", "perspective": "I made a difficult choice.",
            } for _ in payload["scenes"]]})
        if tag.endswith("scene_interpretation"):
            return json.dumps({"scene_enrichments": [{
                "emotions": [], "beliefs": [], "profile_events": [],
            } for _ in payload["scenes"]]})
        if tag.endswith("trait_interpretation"):
            return json.dumps({"scene_trait_interpretations": [
                {"trait_candidates": [{
                    "trait": "integrity", "polarity": "high",
                    "situation_type": "exploitation:other:ordinary",
                    "justification": "The perspective describes a voluntary choice shaped by fairness.",
                }]} for _ in payload["scenes"]
            ]})
        raise AssertionError(f"Unexpected LLM call: {tag}")


def test_provider_unavailable_error_is_actionable():
    error = EmbodimentGenerationError(
        "provider unavailable", category="provider_unavailable", provider_id="openrouter",
        model_name="model", provider_reason="model_unavailable",
    )
    message = _public_error_message(error)
    assert "provider 'openrouter'" in message
    assert "Configure an available model and retry." in message


def test_stage_two_accepts_only_emotions_beliefs_and_profile_events():
    parsed = _SceneEnrichmentsLLMOutput.model_validate({"scene_enrichments": [
        {"emotions": [], "beliefs": [], "profile_events": []},
    ]})
    assert parsed.scene_enrichments[0].profile_events == []
    with pytest.raises(ValueError):
        _SceneEnrichmentsLLMOutput.model_validate({"scene_enrichments": [
            {"emotions": [], "beliefs": [], "impacts": [], "aspect_signals": [], "goal_signals": []},
        ]})


def test_backend_binds_profile_event_to_its_input_scene():
    parsed = _SceneEnrichmentsLLMOutput.model_validate({"scene_enrichments": [{
        "emotions": [], "beliefs": [], "profile_events": [
            {"kind": "aspect", "description": "Learns he is an artificial vessel."},
        ],
    }]})
    bound = _bind_llm_enrichments(parsed, ["scene-1"], {})
    event = bound.scene_enrichments[0].profile_events[0]
    assert event.scene_id == "scene-1"
    assert event.evidence_ids == ["scene:scene-1"]


@pytest.mark.asyncio
async def test_empty_profile_events_skip_consolidation():
    llm = FakeLLM()
    result = await _agent(llm)._consolidate_profile(
        analysis=_analysis([]), current_aspects=[], current_goals=[],
    )
    assert llm.calls == []
    assert result["aspect_updates"] == []
    assert result["goal_updates"] == []


@pytest.mark.asyncio
async def test_consolidation_adds_revelation_with_source_provenance():
    response = {"consolidation": {
        "aspect_operations": [{
            "operation": "add", "target_id": None,
            "candidate_id": "aspect:i-am-an-artificial-vessel",
            "name": "I am an artificial vessel", "description": "Created by another person.",
            "category": "identity", "status": "active", "justification": "A defining revelation.",
            "event_references": [1],
        }],
        "goal_operations": [],
        "focused_aspects": ["aspect:i-am-an-artificial-vessel"], "focused_goals": [],
    }}
    llm = FakeLLM(json.dumps(response))
    event = ProfileEventOutput(
        kind="aspect", description="Discovers he is artificial.", scene_id="s1", evidence_ids=["scene:s1"],
    )
    result = await _agent(llm)._consolidate_profile(
        analysis=_analysis([event]), current_aspects=[], current_goals=[],
    )
    op = result["aspect_updates"][0]
    assert op.candidate_id == "aspect:i-am-an-artificial-vessel"
    assert op.evidence_ids == ["scene:s1"]
    assert len(llm.calls) == 1


def test_focus_rotation_keeps_old_history_without_status_mutation():
    aspects = [{"id": f"a{i}", "name": f"I am {i}", "status": "active", "in_focus": False}
               for i in range(600)]
    _apply_aspect_ops(aspects, [], focused_ids=[f"a{i}" for i in range(10)])
    assert len(aspects) == 600
    assert sum(item["in_focus"] for item in aspects) == 10
    assert all(item["status"] == "active" for item in aspects)
    with pytest.raises(ValueError, match="focused aspect limit"):
        _apply_aspect_ops(aspects, [], focused_ids=[f"a{i}" for i in range(11)])


def test_leaving_focus_does_not_resolve_active_goal():
    goals = [{"id": "g1", "title": "Find my maker", "status": "active", "in_focus": True}]
    _apply_goal_ops(goals, [], focused_ids=[])
    assert goals == [{"id": "g1", "title": "Find my maker", "status": "active", "in_focus": False}]


def test_status_transition_preserves_aspect_record():
    aspects = [{"id": "a1", "name": "I guard the archive", "status": "active", "in_focus": True}]
    _apply_aspect_ops(aspects, [AspectUpdateData(
        operation="status", target_id="a1", name="I guard the archive", status="inactive",
        justification="The role has ended.", evidence_ids=["scene:s2"],
    )], focused_ids=[])
    assert len(aspects) == 1
    assert aspects[0]["status"] == "inactive"
    assert aspects[0]["in_focus"] is False


def test_prompt_contracts_describe_the_new_execution_order():
    assert "Stage 2" in PSYCHOLOGICAL_ANALYSIS_PROMPT
    assert "profile_events" in PSYCHOLOGICAL_ANALYSIS_PROMPT
    assert "Stage 4" in PSYCHOLOGICAL_CONSOLIDATION_PROMPT
    assert "aspect_signals" not in PSYCHOLOGICAL_ANALYSIS_PROMPT
    assert "goal_signals" not in PSYCHOLOGICAL_ANALYSIS_PROMPT
    assert "impacts" not in PSYCHOLOGICAL_ANALYSIS_PROMPT
    assert "perspective" in PERSPECTIVE_PROMPT
    assert "trait" in TRAIT_INTERPRETATION_PROMPT.lower()
    assert "clearly demonstrated dispositional responses" in TRAIT_INTERPRETATION_PROMPT
    assert '"personality_traits"' not in TRAIT_INTERPRETATION_PROMPT
    assert "Established personality dimensions and meanings" in TRAIT_INTERPRETATION_PROMPT
    assert "generated perspectives" in TRAIT_INTERPRETATION_PROMPT
    assert PROMPT_VERSION


@pytest.mark.asyncio
async def test_trait_interpretation_payload_uses_scenes_without_personality_or_perspective():
    llm = FakeLLM(json.dumps({"scene_trait_interpretations": [{"trait_candidates": []}]}))
    agent = _agent(llm)
    await agent._interpret_traits_batch(
        source_entity_id="source", source_entity_alias="Source",
        identity={"alias": "Mara", "identity_description": {"personality_traits": [
            {"trait": "integrity", "description": "biased old wording"}]}},
        perspectives=[ScenePerspectiveOutput(
            scene_id="scene-1", evidence_ids=["scene:scene-1"], source_type="participated",
            perspective="Mara refuses to exploit an unobserved opportunity.",
        )],
        scene_contexts=[{"scene": {"name": "Choice", "description": "Mara chooses."}, "position": 1}],
    )
    payload = json.loads(llm.calls[0]["messages"][1]["content"])
    assert payload == {
        "target": {"alias": "Mara"},
        "scenes": [{"scene": {"name": "Choice", "description": "Mara chooses."}, "position": 1}],
    }
