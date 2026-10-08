import json

import pytest

from app.jobs.character_agent.embody_agent import (
    EmbodyAgent,
    EmbodimentGenerationError,
    _SceneEnrichmentsLLMOutput,
    _bind_llm_enrichments,
)
from app.jobs.character_agent.profile import _apply_aspect_ops, _apply_goal_ops
from app.schemas.character_agent import (
    AspectUpdateData,
    EmbodyAgentAnalysis,
    EmbodimentObservationsOutput,
    GoalUpdateData,
    ProfileEventOutput,
)


class _LLM:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _analysis(events):
    return EmbodyAgentAnalysis(
        source_entity_id="source-1",
        source_entity_alias="Source one",
        perspectives=[],
        observations=EmbodimentObservationsOutput(),
        profile_events=events,
        llm_calls=[],
    )


def test_stage_two_contract_has_only_emotions_beliefs_and_profile_events():
    value = _SceneEnrichmentsLLMOutput.model_validate({
        "scene_enrichments": [{
            "emotions": [], "beliefs": [], "profile_events": [],
        }],
    })
    assert value.scene_enrichments[0].profile_events == []
    with pytest.raises(ValueError):
        _SceneEnrichmentsLLMOutput.model_validate({
            "scene_enrichments": [{
                "emotions": [], "beliefs": [], "impacts": [],
                "aspect_signals": [], "goal_signals": [],
            }],
        })


def test_enrichment_scene_and_event_provenance_are_bound_by_backend():
    from app.jobs.character_agent.embody_agent import _SceneEnrichmentLLMOutput, _SceneEnrichmentsLLMOutput

    output = _SceneEnrichmentsLLMOutput.model_validate({
        "scene_enrichments": [{
            "emotions": [], "beliefs": [],
            "profile_events": [{"kind": "aspect", "description": "Reveals artificial origin."}],
        }],
    })
    bound = _bind_llm_enrichments(output, ["scene-9"], {})
    event = bound.scene_enrichments[0].profile_events[0]
    assert event.scene_id == "scene-9"
    assert event.evidence_ids == ["scene:scene-9"]
    assert bound.scene_enrichments[0].scene_id == "scene-9"


@pytest.mark.asyncio
async def test_empty_event_source_skips_consolidation_call_and_keeps_focus():
    llm = _LLM("{}")
    agent = EmbodyAgent(
        llm_client=llm, character_incorporation_model="model",
        scene_interpretation_model="model",
    )
    aspects = [{"id": "a1", "name": "I am a keeper", "status": "active", "in_focus": True}]
    result = await agent._consolidate_profile(
        analysis=_analysis([]), current_aspects=aspects, current_goals=[],
    )
    assert llm.calls == []
    assert result["focused_aspects"] == ["a1"]


@pytest.mark.asyncio
async def test_consolidation_adds_candidate_and_rejects_unknown_focus_ids():
    answer = {"consolidation": {
        "aspect_operations": [{
            "operation": "add", "target_id": None, "candidate_id": "aspect:i-am-a-vessel",
            "name": "I am a vessel", "description": "Artificially created.",
            "category": "identity", "status": "active", "justification": "The revelation changes self-understanding.",
            "event_references": [1],
        }],
        "goal_operations": [],
        "focused_aspects": ["aspect:i-am-a-vessel"], "focused_goals": [],
    }}
    llm = _LLM(json.dumps(answer))
    agent = EmbodyAgent(
        llm_client=llm, character_incorporation_model="model",
        scene_interpretation_model="model",
    )
    result = await agent._consolidate_profile(
        analysis=_analysis([ProfileEventOutput(
            kind="aspect", description="The character learns they were fabricated.",
            scene_id="scene-9", evidence_ids=["scene:scene-9"],
        )]), current_aspects=[], current_goals=[],
    )
    assert len(llm.calls) == 1
    assert result["aspect_updates"][0].candidate_id == "aspect:i-am-a-vessel"
    assert result["aspect_updates"][0].evidence_ids == ["scene:scene-9"]

    answer["consolidation"]["focused_aspects"] = ["aspect:unknown"]
    agent = EmbodyAgent(
        llm_client=_LLM(json.dumps(answer)), character_incorporation_model="model",
        scene_interpretation_model="model",
    )
    with pytest.raises(EmbodimentGenerationError, match="focus references"):
        await agent._consolidate_profile(
            analysis=_analysis([ProfileEventOutput(
                kind="aspect", description="A revelation.", scene_id="scene-9",
                evidence_ids=["scene:scene-9"],
            )]), current_aspects=[], current_goals=[],
        )


@pytest.mark.asyncio
async def test_goal_can_be_introduced_and_completed_in_one_source_bundle():
    answer = {"consolidation": {
        "aspect_operations": [],
        "goal_operations": [
            {
                "operation": "add", "target_id": None, "candidate_id": "goal:find-my-maker",
                "title": "Find my maker", "description": "Discover who created me.",
                "goal_type": "objective", "status": "active",
                "justification": "The character makes a clear commitment.", "event_references": [1],
            },
            {
                "operation": "status", "target_id": "goal:find-my-maker", "candidate_id": None,
                "title": "Find my maker", "description": None, "goal_type": None,
                "status": "completed", "justification": "The character learns the creator's identity.",
                "event_references": [2],
            },
        ],
        "focused_aspects": [], "focused_goals": [],
    }}
    llm = _LLM(json.dumps(answer))
    events = [
        ProfileEventOutput(kind="goal", description="Vows to discover who created him.",
                           scene_id="scene-1", evidence_ids=["scene:scene-1"]),
        ProfileEventOutput(kind="goal", description="Discovers who created him.",
                           scene_id="scene-2", evidence_ids=["scene:scene-2"]),
    ]
    result = await EmbodyAgent(
        llm_client=llm, character_incorporation_model="model", scene_interpretation_model="model",
    )._consolidate_profile(analysis=_analysis(events), current_aspects=[], current_goals=[])
    goals = []
    _apply_goal_ops(goals, result["goal_updates"], focused_ids=result["focused_goals"])
    assert len(goals) == 1
    assert goals[0]["status"] == "completed"
    assert goals[0]["in_focus"] is False
    assert goals[0]["evidence_ids"] == ["scene:scene-1", "scene:scene-2"]


def test_profile_operations_preserve_history_and_separate_focus_from_status():
    aspects = [{"id": "old", "name": "I protected the archive", "status": "active", "in_focus": True}]
    goals = [{"id": "goal-old", "title": "Find my maker", "status": "active", "in_focus": True}]
    _apply_aspect_ops(aspects, [AspectUpdateData(
        operation="status", target_id="old", name="I protected the archive",
        status="inactive", justification="Evidence disproves the lasting role.",
        evidence_ids=["scene:s1"],
    )], focused_ids=[])
    _apply_goal_ops(goals, [], focused_ids=[])
    assert len(aspects) == 1
    assert aspects[0]["status"] == "inactive"
    assert aspects[0]["in_focus"] is False
    assert goals[0]["status"] == "active"
    assert goals[0]["in_focus"] is False


def test_focus_caps_are_enforced_without_capping_historical_records():
    aspects = [{"id": f"a{i}", "name": f"I am aspect {i}", "status": "active", "in_focus": False}
               for i in range(15)]
    _apply_aspect_ops(aspects, [], focused_ids=[f"a{i}" for i in range(10)])
    assert len(aspects) == 15
    assert sum(item["in_focus"] for item in aspects) == 10
    with pytest.raises(ValueError, match="focused aspect limit"):
        _apply_aspect_ops(aspects, [], focused_ids=[f"a{i}" for i in range(11)])
