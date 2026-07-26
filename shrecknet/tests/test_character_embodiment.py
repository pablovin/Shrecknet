import asyncio
import json
from datetime import timezone

import pytest
from neo4j.time import DateTime
from sqlalchemy import create_engine, inspect

from app.core.config_store import LLMModelTarget
from app.jobs.character_agent.embody_agent import EmbodyAgent, EmbodimentGenerationError
from app.jobs.character_agent.embody_agent_prompts import (
    ASPECTS_UPDATE_PROMPT,
    AXES_UPDATE_PROMPT,
    GOALS_UPDATE_PROMPT,
    OBSERVATIONS_PROMPT,
    PERSPECTIVE_PROMPT,
    PROMPT_VERSION,
)
from app.schemas.character_agent import (
    BEHAVIOURAL_AXES,
    CharacterAgentCreateRequest, CharacterAgentRead, CharacterAgentUpdate,
    EmbodimentEvidence, EmbodimentProposal,
    SceneInput, ScenePerspectiveOutput,
    CharacterTimelineProjection,
)
from app.services.character_embodiment_service import CharacterEmbodimentService, _json_safe
from app.db.base import Base
from app.models.character_embodiment import CharacterEmbodimentDraft  # noqa: F401


def _canonical(overrides=None):
    data = {
        "entity_instance_id": "e1",
        "alias": "Mara",
        "avatar_url": "mara.png",
        "authored_text": "Canonical authored biography.",
        "generated_text": "Generated fallback biography.",
        "entity_type": "Character",
        "entity_type_description": "A person in the story.",
        "properties": {"origin": "Shrecknet"},
    }
    if overrides:
        data.update(overrides)
    return data


class FakeLLM:
    def __init__(self, outputs_by_tag):
        self.outputs_by_tag = outputs_by_tag
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.outputs_by_tag[kwargs["usage_tag"]]


class ParallelStepLLM(FakeLLM):
    def __init__(self, outputs_by_tag):
        super().__init__(outputs_by_tag)
        self.step3_started = asyncio.Event()
        self.steps_345_running: set[str] = set()

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        tag = kwargs["usage_tag"]
        if tag in (
            "character_agent.embodiment.axes",
            "character_agent.embodiment.aspects",
            "character_agent.embodiment.goals",
        ):
            self.steps_345_running.add(tag)
            if len(self.steps_345_running) == 3:
                self.step3_started.set()
            await asyncio.wait_for(self.step3_started.wait(), timeout=1)
        return self.outputs_by_tag[tag]


# ── Prompt contract tests ─────────────────────────────────────────────

def test_prompts_document_complete_contracts():
    assert "scene_id" in PERSPECTIVE_PROMPT
    assert "source_type" in PERSPECTIVE_PROMPT
    assert "awareness_level" in PERSPECTIVE_PROMPT
    assert "emotional_interpretation" in PERSPECTIVE_PROMPT
    assert "belief" in PERSPECTIVE_PROMPT
    assert "impact" in PERSPECTIVE_PROMPT

    assert "recurring_behaviours" in OBSERVATIONS_PROMPT
    assert "motivations" in OBSERVATIONS_PROMPT
    assert "relationships" in OBSERVATIONS_PROMPT
    assert "contradictions" in OBSERVATIONS_PROMPT
    assert "evidence_gaps" in OBSERVATIONS_PROMPT

    assert "calm_aggressive" in AXES_UPDATE_PROMPT
    assert "cooperative_dominating" in AXES_UPDATE_PROMPT
    assert "confidence" in AXES_UPDATE_PROMPT

    assert "add | update | remove" in ASPECTS_UPDATE_PROMPT
    assert "category" in ASPECTS_UPDATE_PROMPT

    assert "add | update | remove | complete" in GOALS_UPDATE_PROMPT
    assert "goal_type" in GOALS_UPDATE_PROMPT

    assert PROMPT_VERSION


# ── Pipeline tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_embodiment_pipeline_is_five_calls_with_parallel_steps_3_4_5():
    llm = ParallelStepLLM({
        "character_agent.embodiment.perspectives": json.dumps({
            "perspectives": [{
                "scene_id": "s1", "source_type": "participated",
                "awareness_level": 80, "confidence": 75,
                "summary": "Mara explored the ruins.",
                "interpretation": "She felt curiosity.",
                "memory_strength": 60, "importance": 3, "status": "active",
            }],
        }),
        "character_agent.embodiment.observations": json.dumps({
            "recurring_behaviours": [{
                "text": "Mara explores cautiously.",
                "evidence_ids": ["scene:s1"],
            }],
            "motivations": [], "values": [], "fears": [], "conflicts": [],
            "relationships": [], "contradictions": [], "evidence_gaps": [],
        }),
        "character_agent.embodiment.axes": json.dumps({
            "behavioural_axes": [{
                "axis": "cautious_reckless", "new_value": 30,
                "justification": "Exploration was cautious.",
                "confidence": 0.6, "evidence_ids": ["scene:s1"],
            }],
        }),
        "character_agent.embodiment.aspects": json.dumps({
            "aspect_updates": [],
        }),
        "character_agent.embodiment.goals": json.dumps({
            "goal_updates": [],
        }),
    })
    agent = EmbodyAgent(llm_client=llm, model=LLMModelTarget(), max_aspects=4, max_goals=3)
    scenes = [
        SceneInput(scene_id="s1", name="Ruins", description="Mara explores ancient ruins.",
                   created_at="2026-01-01T00:00:00Z"),
    ]
    result = await agent.run(
        source_entity_id="source-a",
        source_entity_alias="Source A",
        canonical_identity=_canonical(),
        current_behavioural_axes={axis: 50 for axis in BEHAVIOURAL_AXES},
        current_aspects=[],
        current_goals=[],
        scenes=scenes,
    )
    assert len(llm.calls) == 5
    assert result.total_llm_calls == 5
    assert result.total_tokens_est > 0
    assert len(result.llm_calls) == 5
    assert result.source_entity_id == "source-a"
    assert result.source_entity_alias == "Source A"
    assert len(result.perspectives) == 1
    assert result.perspectives[0].scene_id == "s1"
    assert len(result.observations.recurring_behaviours) == 1
    assert len(result.axis_updates) == 1
    assert result.axis_updates[0].axis == "cautious_reckless"
    tags = [call["usage_tag"] for call in llm.calls]
    assert "character_agent.embodiment.perspectives" in tags
    assert "character_agent.embodiment.observations" in tags
    assert tags.index("character_agent.embodiment.perspectives") < tags.index(
        "character_agent.embodiment.observations"
    )
    for t in ("character_agent.embodiment.axes", "character_agent.embodiment.aspects",
              "character_agent.embodiment.goals"):
        assert t in tags


@pytest.mark.asyncio
async def test_embodiment_rejects_empty_scenes():
    agent = EmbodyAgent(
        llm_client=FakeLLM({}), model=LLMModelTarget(), max_aspects=4, max_goals=3,
    )
    with pytest.raises(EmbodimentGenerationError, match="no scenes"):
        await agent.run(
            source_entity_id="s", source_entity_alias="S",
            canonical_identity=_canonical(),
            current_behavioural_axes={axis: 50 for axis in BEHAVIOURAL_AXES},
            current_aspects=[], current_goals=[], scenes=[],
        )


@pytest.mark.asyncio
async def test_embodiment_rejects_unknown_evidence_in_observations():
    llm = FakeLLM({
        "character_agent.embodiment.perspectives": json.dumps({
            "perspectives": [{
                "scene_id": "s1", "source_type": "participated",
                "awareness_level": 50, "confidence": 50,
                "summary": "Scene.", "interpretation": "Ok.",
                "memory_strength": 50, "importance": 3, "status": "active",
            }],
        }),
        "character_agent.embodiment.observations": json.dumps({
            "recurring_behaviours": [{
                "text": "Mara digs.", "evidence_ids": ["scene:missing"],
            }],
            "motivations": [], "values": [], "fears": [], "conflicts": [],
            "relationships": [], "contradictions": [], "evidence_gaps": [],
        }),
    })
    agent = EmbodyAgent(llm_client=llm, model=LLMModelTarget(), max_aspects=4, max_goals=3)
    with pytest.raises(EmbodimentGenerationError, match="unknown evidence"):
        await agent.run(
            source_entity_id="s", source_entity_alias="S",
            canonical_identity=_canonical(),
            current_behavioural_axes={axis: 50 for axis in BEHAVIOURAL_AXES},
            current_aspects=[], current_goals=[],
            scenes=[SceneInput(scene_id="s1", name="Scene", description="", created_at=None)],
        )


@pytest.mark.asyncio
async def test_embodiment_rejects_mismatched_scene_ids():
    llm = FakeLLM({
        "character_agent.embodiment.perspectives": json.dumps({
            "perspectives": [{
                "scene_id": "wrong_id", "source_type": "participated",
                "awareness_level": 50, "confidence": 50,
                "summary": "Scene.", "interpretation": "Ok.",
                "memory_strength": 50, "importance": 3, "status": "active",
            }],
        }),
    })
    agent = EmbodyAgent(llm_client=llm, model=LLMModelTarget(), max_aspects=4, max_goals=3)
    with pytest.raises(EmbodimentGenerationError, match="scene_ids must match"):
        await agent.run(
            source_entity_id="s", source_entity_alias="S",
            canonical_identity=_canonical(),
            current_behavioural_axes={axis: 50 for axis in BEHAVIOURAL_AXES},
            current_aspects=[], current_goals=[],
            scenes=[SceneInput(scene_id="s1", name="Scene", description="", created_at=None)],
        )


@pytest.mark.asyncio
async def test_embodiment_pipeline_records_llm_stats():
    llm = FakeLLM({
        "character_agent.embodiment.perspectives": json.dumps({
            "perspectives": [{
                "scene_id": "s1", "source_type": "participated",
                "awareness_level": 50, "confidence": 50,
                "summary": "Test.", "interpretation": "Test.",
                "memory_strength": 50, "importance": 3, "status": "active",
            }],
        }),
        "character_agent.embodiment.observations": json.dumps({
            "recurring_behaviours": [{
                "text": "Test behaviour.", "evidence_ids": ["scene:s1"],
            }],
            "motivations": [], "values": [], "fears": [], "conflicts": [],
            "relationships": [], "contradictions": [], "evidence_gaps": [],
        }),
        "character_agent.embodiment.axes": json.dumps({"behavioural_axes": []}),
        "character_agent.embodiment.aspects": json.dumps({"aspect_updates": []}),
        "character_agent.embodiment.goals": json.dumps({"goal_updates": []}),
    })
    agent = EmbodyAgent(llm_client=llm, model=LLMModelTarget(), max_aspects=4, max_goals=3)
    result = await agent.run(
        source_entity_id="s", source_entity_alias="S",
        canonical_identity=_canonical(),
        current_behavioural_axes={axis: 50 for axis in BEHAVIOURAL_AXES},
        current_aspects=[], current_goals=[],
        scenes=[SceneInput(scene_id="s1", name="Scene", description="", created_at=None)],
    )
    assert result.total_llm_calls == 5
    assert result.total_tokens_est >= 5
    for record in result.llm_calls:
        assert record.stage in (
            "scene perspectives", "embodiment observations",
            "axis updates", "aspect updates", "goal updates",
        )
        assert record.usage_tag.startswith("character_agent.embodiment.")
        assert record.input_chars > 0
        assert record.output_chars > 0


# ── Schema tests ───────────────────────────────────────────────────────

def test_proposal_requires_unique_suggestion_ids():
    raw = {
        "name": "Mara", "background_story": "Grounded.", "image_url": None,
        "behavioural_axes": [
            {"axis": axis, "value": 50, "confidence": 0.5,
             "justification": "Limited evidence.", "evidence_ids": ["scene:s1"]}
            for axis in BEHAVIOURAL_AXES
        ],
        "aspects": [
            {"suggestion_id": "a1", "name": "Brave", "category": "identity",
             "importance": 3, "justification": "Seen.", "confidence": 0.5,
             "evidence_ids": ["scene:s1"]},
            {"suggestion_id": "a1", "name": "Brave", "category": "identity",
             "importance": 3, "justification": "Seen.", "confidence": 0.5,
             "evidence_ids": ["scene:s1"]},
        ],
        "goals": [],
    }
    with pytest.raises(ValueError, match="unique"):
        EmbodimentProposal.model_validate(raw)


def test_evidence_packing_uses_ten_characters_per_configured_token():
    evidence = [
        EmbodimentEvidence(
            evidence_id="identity:e1", kind="identity", source_id="e1",
            text="a" * 9_000,
        ),
        EmbodimentEvidence(
            evidence_id="scene:1", kind="scene", source_id="1",
            text="b" * 1_000,
        ),
        EmbodimentEvidence(
            evidence_id="scene:2", kind="scene", source_id="2",
            text="c",
        ),
    ]
    packed = CharacterEmbodimentService.pack_evidence(evidence, token_budget=1_000)
    assert [item.evidence_id for item in packed] == ["identity:e1", "scene:1"]


def test_embodiment_draft_table_contains_review_and_provenance_state():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("character_embodiment_drafts")
    }
    assert {
        "source_entity_id", "evidence_snapshot", "observations",
        "generated_proposal", "timeline_projection",
        "generation_revision", "background_job_id", "active_entity_key",
    } <= columns


def test_normal_create_contract_accepts_optional_draft_aspects_and_goals():
    payload = CharacterAgentCreateRequest.model_validate({
        "ontology_id": 12,
        "entity_instance_id": "entity-mara",
        "embodiment_draft_id": "draft-1",
        "name": "Mara",
        "background_story": "Edited story",
        "aspects": [{
            "name": "Frontier leader", "category": "role", "importance": 5,
            "justification": "Mara repeatedly organized the settlement.",
            "evidence_ids": ["milestone:m1"], "confidence": .9,
        }],
        "goals": [{
            "title": "Protect the village", "goal_type": "obligation",
            "justification": "Mara explicitly accepted responsibility for its safety.",
            "priority": 90, "evidence_ids": ["milestone:m1"],
        }],
    })
    assert payload.embodiment_draft_id == "draft-1"
    assert payload.aspects[0].importance == 5
    assert payload.aspects[0].justification
    assert payload.goals[0].goal_type.value == "obligation"
    assert payload.goals[0].justification


def test_character_agent_read_accepts_embodiment_draft_provenance():
    payload = CharacterAgentRead.model_validate({
        "id": "agent-1",
        "ontology_id": 1,
        "entity_instance_id": "entity-1",
        "embodied_entity_instance_id": "entity-1",
        "embodiment_draft_id": "draft-1",
        "name": "Ernst",
        "subtitle": "The Doll",
        "background_story": "A restrained investigator.",
        "created_by_user_id": 1,
        "created_at": "2026-07-25T10:00:00Z",
        "updated_at": "2026-07-25T10:00:00Z",
    })
    assert payload.embodiment_draft_id == "draft-1"
    assert payload.subtitle == "The Doll"


def test_character_agent_subtitle_is_optional_editable_and_clearable():
    created = CharacterAgentCreateRequest.model_validate({
        "ontology_id": 1, "entity_instance_id": "entity-1",
        "name": "Morgana", "subtitle": "The Archivist of Arkham",
    })
    assert created.subtitle == "The Archivist of Arkham"
    update = CharacterAgentUpdate.model_validate({"subtitle": None})
    assert "subtitle" in update.model_fields_set
    assert update.subtitle is None


def test_timeline_contract_preserves_revision_subtitles():
    timeline = CharacterTimelineProjection.model_validate({
        "revisions": [{
            "revision_number": 0, "name": "Ernst", "subtitle": "The Doll",
            "trait_adherence": 80,
            "behavioural_axes": {axis: 50 for axis in BEHAVIOURAL_AXES},
            "active_aspects": [], "active_goals": [],
        }],
        "source_projections": [],
    })
    assert timeline.revisions[0].subtitle == "The Doll"


def test_graph_temporal_values_are_json_safe_in_nested_evidence():
    created_at = DateTime(2026, 7, 25, 14, 26, 27, 60_000_000, tzinfo=timezone.utc)
    normalized = _json_safe({
        "created_at": created_at,
        "history": [{"observed_at": created_at}],
    })
    assert normalized == {
        "created_at": "2026-07-25T14:26:27.060000000+00:00",
        "history": [{"observed_at": "2026-07-25T14:26:27.060000000+00:00"}],
    }
    evidence = EmbodimentEvidence(
        evidence_id="scene:1", kind="scene", source_id="1", text="Scene",
        provenance=normalized,
    )
    json.dumps(evidence.model_dump(mode="json"))


def test_embody_agent_result_aggregates_stats():
    from app.schemas.character_agent import (
        EmbodyAgentResult, LLMCallRecord,
        ScenePerspectiveOutput, EmbodimentObservationsOutput,
    )
    result = EmbodyAgentResult(
        source_entity_id="s1",
        source_entity_alias="Source 1",
        perspectives=[
            ScenePerspectiveOutput(
                scene_id="sc1", source_type="participated",
                awareness_level=50, confidence=50,
                summary="Test.", interpretation="Test.",
                memory_strength=50, importance=3, status="active",
            ),
        ],
        observations=EmbodimentObservationsOutput(),
        axis_updates=[],
        aspect_updates=[],
        goal_updates=[],
        llm_calls=[
            LLMCallRecord(stage="s1", usage_tag="t1", input_chars=100, output_chars=50,
                          input_tokens_est=25, output_tokens_est=13, total_tokens_est=38),
            LLMCallRecord(stage="s2", usage_tag="t2", input_chars=200, output_chars=100,
                          input_tokens_est=50, output_tokens_est=25, total_tokens_est=75),
        ],
    )
    assert result.total_llm_calls == 2
    assert result.total_tokens_est == 113
