import json

import pytest

from app.core.config_store import LLMModelTarget
from app.jobs.character_agent.query import CharacterAgentQueryJob
from app.schemas.character_agent import CharacterAgentQueryRequest
from app.schemas.character_traits import TraitProfile


class FakeLLM:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


TARGET = LLMModelTarget(provider="test", name="model")
RESULT = json.dumps({"content": "Gawaine was brave, but rash.", "decision_basis": "Her remembered experience matters."})
SNAPSHOT = {
    "character_agent": {"name": "Cwenhild", "subtitle": "Commander", "trait_profile": TraitProfile().model_dump(mode="json")},
    "aspects": [{"id": "aspect-1", "name": "Battlefield Commander", "category": "role", "importance": 5, "description": "Commands soldiers."}],
    "goals": [{"id": "goal-1", "title": "Protect Arthur's reign", "priority": 98, "commitment": 95, "description": "Protect the crown."}],
    "memories": [{
        "id": "memory-1", "summary": "Gawaine fought beside Cwenhild at Bedegraine.",
        "interpretation": "His courage became recklessness near glory.",
        "source_type": "participated", "perspective": "I stood beside them and felt the threat growing.",
        "emotions": [{"description": "Pride mixed with worry."}],
        "beliefs": [{"statement": "Gawaine seeks glory.", "status": "believed", "confidence": 70}],
        "impacts": [{"impact_type": "goal_change", "direction": "advanced", "description": "Protect the line.", "target_name": "Protect Arthur's reign"}],
    }],
}


def test_query_trait_summary_distinguishes_unknown_from_mixed_point_five():
    profile = TraitProfile()
    profile.dispositional_traits['curiosity'].point = 5
    profile.dispositional_traits['curiosity'].status = 'supported'
    profile.dispositional_traits['curiosity'].observation_ids = ['perspective-1', 'perspective-2']
    profile.dispositional_traits['curiosity'].observation_count = 2
    snapshot = {**SNAPSHOT, 'character_agent': {
        **SNAPSHOT['character_agent'], 'trait_profile': profile.model_dump(mode='json')}}
    compact = CharacterAgentQueryJob._compact_character(snapshot)
    assert 'Mixed or context-dependent' in compact['traits']['curiosity']['summary']
    assert compact['traits']['integrity']['point'] is None
    assert 'No grounded disposition' in compact['traits']['integrity']['summary']


@pytest.mark.asyncio
async def test_identity_query_is_one_deliberation_call_with_owner_memory():
    llm = FakeLLM([RESULT])
    job = CharacterAgentQueryJob(llm_client=llm, deliberation_model=TARGET, repair_model=TARGET)
    result = await job.run(CharacterAgentQueryRequest(query="What do you think of Gawaine?"), SNAPSHOT)

    assert result.content == "Gawaine was brave, but rash."
    assert len(llm.calls) == 1
    assert llm.calls[0]["usage_tag"] == "character_agent.query"
    payload = json.loads(llm.calls[0]["messages"][1]["content"])
    assert payload["character"]["goals"][0]["title"] == "Protect Arthur's reign"
    assert payload["memories"][0]["beliefs"][0]["statement"] == "Gawaine seeks glory."
    assert "memory-1" not in json.dumps(payload)
    assert "qualifying_count" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_invalid_output_gets_one_json_repair_call():
    repaired = json.dumps({"content": "I remember him.", "decision_basis": "A personal memory is relevant."})
    llm = FakeLLM(["not json", repaired])
    job = CharacterAgentQueryJob(llm_client=llm, deliberation_model=TARGET, repair_model=TARGET)
    result = await job.run(CharacterAgentQueryRequest(query="Gawaine?"), SNAPSHOT)

    assert result.content == "I remember him."
    assert [call["usage_tag"] for call in llm.calls] == ["character_agent.query", "character_agent.repair"]


@pytest.mark.asyncio
async def test_generic_query_is_also_one_call():
    llm = FakeLLM([json.dumps({"content": "Neutral.", "decision_basis": "Only supplied context was used."})])
    job = CharacterAgentQueryJob(llm_client=llm, deliberation_model=TARGET, repair_model=TARGET)
    await job.run(CharacterAgentQueryRequest(query="Assess", use_character_identity=False))
    assert len(llm.calls) == 1
    assert llm.calls[0]["usage_tag"] == "character_agent.generic_query"
