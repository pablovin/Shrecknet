import json

import pytest

from app.core.config_store import LLMModelTarget
from app.jobs.character_agent.decision_making import (
    CharacterAgentDecisionMakingJob,
    CharacterIdentityUnavailableError,
)
from app.jobs.character_agent.memory import select_relevant_memories
from app.schemas.character_agent import CharacterAgentQueryRequest


class FakeLLM:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


TARGET = LLMModelTarget(provider="test", name="model")
RESULT = json.dumps({"content": "Gawaine was brave, but rash.", "decision_basis": "Her remembered experience matters."})
SNAPSHOT = {
    "character_agent": {"name": "Cwenhild", "subtitle": "Commander", "background_story": "A northern commander sworn to guard the crown.", "identity_description": {"identity_summary": "A steadfast commander shaped by siege and duty.", "psychological_summary": "She fears needless loss and values loyalty.", "personality_traits": []}, "trait_profile": {"excluded": "numeric traits"}},
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


@pytest.mark.asyncio
async def test_identity_query_is_one_deliberation_call_with_owner_memory():
    llm = FakeLLM([RESULT])
    job = CharacterAgentDecisionMakingJob(llm_client=llm, deliberation_model=TARGET, repair_model=TARGET)
    result = await job.run(CharacterAgentQueryRequest(query="What do you think of Gawaine?"), SNAPSHOT)

    assert result.content == "Gawaine was brave, but rash."
    assert len(llm.calls) == 1
    assert llm.calls[0]["usage_tag"] == "character_agent.decision_making"
    payload = json.loads(llm.calls[0]["messages"][1]["content"])
    assert payload["identity_description"]["identity_summary"].startswith("A steadfast")
    assert set(payload) == {"identity_description", "memories", "query", "context", "instruction", "response_format"}
    assert "background_story" not in json.dumps(payload)
    assert "trait_profile" not in json.dumps(payload)
    assert "Battlefield Commander" not in json.dumps(payload)
    assert payload["memories"][0]["beliefs"][0]["statement"] == "Gawaine seeks glory."
    assert "memory-1" not in json.dumps(payload)
    assert "qualifying_count" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_invalid_output_gets_one_json_repair_call():
    repaired = json.dumps({"content": "I remember him.", "decision_basis": "A personal memory is relevant."})
    llm = FakeLLM(["not json", repaired])
    job = CharacterAgentDecisionMakingJob(llm_client=llm, deliberation_model=TARGET, repair_model=TARGET)
    result = await job.run(CharacterAgentQueryRequest(query="Gawaine?"), SNAPSHOT)

    assert result.content == "I remember him."
    assert [call["usage_tag"] for call in llm.calls] == ["character_agent.decision_making", "character_agent.repair"]


@pytest.mark.asyncio
async def test_generic_query_is_also_one_call():
    llm = FakeLLM([json.dumps({"content": "Neutral.", "decision_basis": "Only supplied context was used."})])
    job = CharacterAgentDecisionMakingJob(llm_client=llm, deliberation_model=TARGET, repair_model=TARGET)
    await job.run(CharacterAgentQueryRequest(query="Assess", use_character_identity=False))
    assert len(llm.calls) == 1
    assert llm.calls[0]["usage_tag"] == "character_agent.generic_decision_making"


@pytest.mark.asyncio
async def test_memory_retrieval_uses_query_and_context():
    llm = FakeLLM([RESULT])
    job = CharacterAgentDecisionMakingJob(llm_client=llm, deliberation_model=TARGET, repair_model=TARGET)
    request = CharacterAgentQueryRequest(
        query="Choose what to do.",
        context={"threat": "Gawaine is approaching the northern gate"},
    )
    await job.run(request, SNAPSHOT)
    payload = json.loads(llm.calls[0]["messages"][1]["content"])
    assert payload["memories"][0]["perspective"].startswith("I stood beside")


@pytest.mark.asyncio
async def test_retrieval_ignores_identity_only_terms():
    memories = [
        {"id": "identity", "perspective": "The siege shaped my loyalty."},
        {"id": "situation", "perspective": "Gawaine threatened the northern gate."},
    ]
    selected = await select_relevant_memories(
        query="Choose an option.", context={"stakes": "Gawaine at northern gate"},
        memories=memories,
    )
    assert selected == [{"perspective": "Gawaine threatened the northern gate."}]


@pytest.mark.asyncio
async def test_identity_mode_requires_persisted_identity_description():
    llm = FakeLLM([RESULT])
    job = CharacterAgentDecisionMakingJob(llm_client=llm, deliberation_model=TARGET, repair_model=TARGET)
    snapshot = {**SNAPSHOT, "character_agent": {"identity_description": None}}
    with pytest.raises(CharacterIdentityUnavailableError, match="identity_description"):
        await job.run(CharacterAgentQueryRequest(query="Choose."), snapshot)
    assert llm.calls == []
