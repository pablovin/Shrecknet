import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from neo4j.time import DateTime
from sqlalchemy import create_engine, inspect

from app.core.config_store import LLMModelTarget, Settings
from app.jobs.character_agent.embody_agent import EmbodyAgent, EmbodimentGenerationError
from app.jobs.character_agent.embody_agent_prompts import (
    ENRICHMENT_PROMPT,
    OBSERVATIONS_PROMPT,
    PERSPECTIVE_PROMPT,
    PROFILE_UPDATE_PROMPT,
    PROMPT_VERSION,
)
from app.schemas.character_agent import (
    BEHAVIOURAL_AXES,
    CharacterAgentCreateRequest, CharacterAgentRead, CharacterAgentUpdate,
    EmbodimentEvidence, EmbodimentProposal,
    ProfileUpdateOutput,
    SceneInput, ScenePerspectiveOutput,
    CharacterTimelineProjection,
)
from app.services.character_embodiment_service import CharacterEmbodimentService, _json_safe
from app.services.character_agent_service import CharacterAgentService
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


def _agent(llm, *, semantic_correction_attempts=1):
    target = LLMModelTarget()
    return EmbodyAgent(
        llm_client=llm,
        character_incorporation_model=target,
        scene_interpretation_model=target,
        character_update_model=target,
        max_aspects=4,
        max_goals=3,
        semantic_correction_attempts=semantic_correction_attempts,
    )


class FakeLLM:
    def __init__(self, outputs_by_tag):
        self.outputs_by_tag = outputs_by_tag
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.outputs_by_tag[kwargs["usage_tag"]]


class DelayedDynamicLLM:
    def __init__(self, delay: float = 0.03):
        self.delay = delay
        self.calls = []
        self.analysis_running = 0
        self.max_analysis_running = 0
        self.profile_current_values = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        tag = kwargs["usage_tag"]
        payload = json.loads(kwargs["messages"][1]["content"])
        if tag != "character_agent.embodiment.profile_update":
            self.analysis_running += 1
            self.max_analysis_running = max(
                self.max_analysis_running, self.analysis_running
            )
            scene_id = (
                payload.get("scenes", [{}])[0].get("scene_id")
                or payload.get("scene_bundles", [{}])[0].get("scene", {}).get("scene_id")
            )
            extra = self.delay if scene_id == "s1" else 0
            await asyncio.sleep(self.delay + extra)
            self.analysis_running -= 1
        if tag == "character_agent.embodiment.character_incorporation":
            scene_id = payload["scenes"][0]["scene_id"]
            return json.dumps({"perspectives": [{
                "scene_id": scene_id, "source_type": "participated",
                "awareness_level": 50, "confidence": 50,
                "summary": "Scene.", "interpretation": "Grounded.",
                "character_reflection": "I remember.",
                "memory_strength": 50, "importance": 3,
            }]})
        if tag == "character_agent.embodiment.scene_interpretation":
            scene_id = payload["scenes"][0]["scene_id"]
            return json.dumps({"scene_enrichments": [{
                "scene_id": scene_id, "emotions": [], "beliefs": [], "impacts": [],
            }]})
        if tag == "character_agent.embodiment.observations":
            scene_id = payload["scene_bundles"][0]["scene"]["scene_id"]
            return json.dumps({
                "recurring_behaviours": [{
                    "text": f"Observed {scene_id}.",
                    "evidence_ids": [f"scene:{scene_id}"],
                }],
                "motivations": [], "values": [], "fears": [], "conflicts": [],
                "relationships": [], "contradictions": [], "evidence_gaps": [],
            })
        current = payload["current_profile"]["behavioural_axes"]
        self.profile_current_values.append(current["cautious_reckless"])
        evidence_id = payload["observations"]["recurring_behaviours"][0]["evidence_ids"][0]
        return json.dumps({
            "behavioural_axes": [{
                "axis": axis,
                "new_value": (
                    current[axis] + 1 if axis == "cautious_reckless" else current[axis]
                ),
                "justification": "Ordered cumulative update.",
                "confidence": 0.7,
                "evidence_ids": [evidence_id],
            } for axis in BEHAVIOURAL_AXES],
            "aspect_updates": [],
            "goal_updates": [],
        })


def _profile_update_output(*, changed_axis: str | None = None) -> str:
    return json.dumps({
        "behavioural_axes": [
            {
                "axis": axis,
                "new_value": 30 if axis == changed_axis else 50,
                "justification": (
                    "Grounded change." if axis == changed_axis else "No change warranted."
                ),
                "confidence": 0.6,
                "evidence_ids": ["scene:s1"],
            }
            for axis in BEHAVIOURAL_AXES
        ],
        "aspect_updates": [],
        "goal_updates": [],
    })


# ── Prompt contract tests ─────────────────────────────────────────────

def test_prompts_document_complete_contracts():
    assert "scene_id" in PERSPECTIVE_PROMPT
    assert "source_type" in PERSPECTIVE_PROMPT
    assert "awareness_level" in PERSPECTIVE_PROMPT
    assert "character_reflection" in PERSPECTIVE_PROMPT
    assert "emotions" in ENRICHMENT_PROMPT
    assert "beliefs" in ENRICHMENT_PROMPT
    assert "impacts" in ENRICHMENT_PROMPT

    assert "recurring_behaviours" in OBSERVATIONS_PROMPT
    assert "motivations" in OBSERVATIONS_PROMPT
    assert "relationships" in OBSERVATIONS_PROMPT
    assert "contradictions" in OBSERVATIONS_PROMPT
    assert "evidence_gaps" in OBSERVATIONS_PROMPT

    assert "calm_aggressive" in PROFILE_UPDATE_PROMPT
    assert "cooperative_dominating" in PROFILE_UPDATE_PROMPT
    assert "add | update | remove" in PROFILE_UPDATE_PROMPT
    assert "add | update | remove | complete" in PROFILE_UPDATE_PROMPT
    assert "current_profile" in PROFILE_UPDATE_PROMPT

    assert PROMPT_VERSION


# ── Pipeline tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_embodiment_pipeline_is_four_calls_with_atomic_profile_update():
    llm = FakeLLM({
        "character_agent.embodiment.character_incorporation": json.dumps({
            "perspectives": [{
                "scene_id": "s1", "source_type": "participated",
                "awareness_level": 80, "confidence": 75,
                "summary": "Mara explored the ruins.",
                "interpretation": "She felt curiosity.",
                "character_reflection": "I need to know what lies below.",
                "memory_strength": 60, "importance": 3, "status": "active",
            }],
        }),
        "character_agent.embodiment.scene_interpretation": json.dumps({
            "scene_enrichments": [{
                "scene_id": "s1",
                "emotions": [{"arousal": 50, "valence": 10, "description": "Uneasy curiosity."}],
                "beliefs": [{"statement": "The ruins matter.", "confidence": 60, "status": "suspected"}],
                "impacts": [{
                    "impact_type": "goal_change", "target_id": "goal:investigate",
                    "direction": "advanced", "magnitude": 40,
                    "description": "The investigation advanced.",
                }],
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
        "character_agent.embodiment.profile_update": _profile_update_output(
            changed_axis="cautious_reckless"
        ),
    })
    agent = _agent(llm)
    scenes = [
        SceneInput(scene_id="s1", name="Ruins", description="Mara explores ancient ruins.",
                   created_at="2026-01-01T00:00:00Z"),
    ]
    result = await agent.run(
        source_entity_id="source-a",
        source_entity_alias="Source A",
        canonical_identity=_canonical(),
        current_behavioural_axes={axis: 50 for axis in BEHAVIOURAL_AXES},
        current_aspects=[{
            "id": "aspect:curious", "name": "Curious", "category": "personality",
            "description": "Seeks answers",
        }],
        current_goals=[{
            "id": "goal:investigate", "title": "Investigate",
            "description": "Find answers", "goal_type": "desire",
        }],
        scenes=scenes,
    )
    assert len(llm.calls) == 4
    assert result.total_llm_calls == 4
    assert result.total_tokens_est > 0
    assert len(result.llm_calls) == 4
    assert result.source_entity_id == "source-a"
    assert result.source_entity_alias == "Source A"
    assert len(result.perspectives) == 1
    assert result.perspectives[0].scene_id == "s1"
    assert result.perspectives[0].character_reflection.startswith("I need")
    assert len(result.perspectives[0].emotions) == 1
    assert result.perspectives[0].impacts[0].target_id == "goal:investigate"
    assert len(result.observations.recurring_behaviours) == 1
    assert len(result.axis_updates) == 1
    assert result.axis_updates[0].axis == "cautious_reckless"
    assert result.axis_updates[0].new_value == 30
    tags = [call["usage_tag"] for call in llm.calls]
    assert "character_agent.embodiment.character_incorporation" in tags
    assert "character_agent.embodiment.scene_interpretation" in tags
    assert "character_agent.embodiment.observations" in tags
    assert tags.index("character_agent.embodiment.character_incorporation") < tags.index(
        "character_agent.embodiment.observations"
    )
    assert "character_agent.embodiment.profile_update" in tags
    incorporation_payload = json.loads(llm.calls[0]["messages"][1]["content"])
    assert "authored_text" not in json.dumps(incorporation_payload)
    enrichment_call = next(
        call for call in llm.calls
        if call["usage_tag"] == "character_agent.embodiment.scene_interpretation"
    )
    observations_call = next(
        call for call in llm.calls
        if call["usage_tag"] == "character_agent.embodiment.observations"
    )
    assert "character_reflection" not in enrichment_call["messages"][1]["content"]
    assert "character_reflection" not in observations_call["messages"][1]["content"]


@pytest.mark.asyncio
async def test_snapshot_analysis_is_bounded_and_updates_remain_ordered():
    from app.tasks.character_embodiment import _apply_axis_updates

    llm = DelayedDynamicLLM()
    agents = [_agent(llm) for _ in range(4)]
    semaphore = asyncio.Semaphore(2)
    completion_order = []
    initial_axes = {axis: 50 for axis in BEHAVIOURAL_AXES}

    async def analyze(index):
        async with semaphore:
            result = await agents[index].analyze(
                source_entity_id=f"source-{index}",
                source_entity_alias=f"Source {index}",
                canonical_identity=_canonical(),
                current_behavioural_axes=initial_axes,
                current_aspects=[],
                current_goals=[],
                scenes=[SceneInput(
                    scene_id=f"s{index + 1}",
                    name=f"Scene {index + 1}",
                    description="Evidence.",
                )],
            )
            completion_order.append(index)
            return result

    started = asyncio.get_running_loop().time()
    analyses = await asyncio.gather(*(analyze(index) for index in range(4)))
    analysis_elapsed = asyncio.get_running_loop().time() - started

    current_axes = dict(initial_axes)
    for index, analysis in enumerate(analyses):
        result = await agents[index].apply_profile_update(
            analysis=analysis,
            current_behavioural_axes=current_axes,
            current_aspects=[],
            current_goals=[],
        )
        _apply_axis_updates(current_axes, result.axis_updates)

    assert llm.max_analysis_running == 2
    assert analysis_elapsed < 0.30  # Sequential baseline is at least 0.36s.
    assert completion_order != [0, 1, 2, 3]
    assert llm.profile_current_values == [50, 51, 52, 53]
    assert current_axes["cautious_reckless"] == 54


@pytest.mark.asyncio
async def test_concurrent_progress_writes_are_serialized(monkeypatch):
    from app.tasks import character_embodiment as task_module

    writes_running = 0
    max_writes_running = 0
    payloads = []

    async def fake_update_job_progress(job_id, progress, details):
        nonlocal writes_running, max_writes_running
        writes_running += 1
        max_writes_running = max(max_writes_running, writes_running)
        await asyncio.sleep(0.01)
        payloads.append((job_id, progress, details))
        writes_running -= 1

    monkeypatch.setattr(
        task_module, "update_job_progress", fake_update_job_progress
    )
    groups = [
        {"source_alias": "Source 1"},
        {"source_alias": "Source 2"},
    ]
    bundles = [
        {
            "index": index + 1, "source_name": group["source_alias"],
            "status": "pending", "active_steps": [], "done_steps": [],
            "elapsed_seconds": None,
        }
        for index, group in enumerate(groups)
    ]
    progress = task_module._EmbodimentProgress(
        job_id=9, draft_id="draft-1", bundles=bundles, source_groups=groups
    )

    await asyncio.gather(progress.stage(0, [1]), progress.stage(1, [1]))
    await asyncio.gather(progress.analysis_ready(0), progress.failed(1))
    await progress.stage(0, [4])
    await progress.complete(0)

    assert max_writes_running == 1
    assert bundles[0]["status"] == "done"
    assert bundles[0]["done_steps"] == [1, 2, 3, 4]
    assert bundles[1]["status"] == "failed"
    assert [item[1] for item in payloads] == sorted(item[1] for item in payloads)


@pytest.mark.asyncio
async def test_embodiment_rejects_empty_scenes():
    agent = _agent(FakeLLM({}))
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
        "character_agent.embodiment.character_incorporation": json.dumps({
            "perspectives": [{
                "scene_id": "s1", "source_type": "participated",
                "awareness_level": 50, "confidence": 50,
                "summary": "Scene.", "interpretation": "Ok.",
                "character_reflection": "I remember.",
                "memory_strength": 50, "importance": 3, "status": "active",
            }],
        }),
        "character_agent.embodiment.scene_interpretation": json.dumps({
            "scene_enrichments": [{
                "scene_id": "s1", "emotions": [], "beliefs": [], "impacts": [],
            }],
        }),
        "character_agent.embodiment.observations": json.dumps({
            "recurring_behaviours": [{
                "text": "Mara digs.", "evidence_ids": ["scene:missing"],
            }],
            "motivations": [], "values": [], "fears": [], "conflicts": [],
            "relationships": [], "contradictions": [], "evidence_gaps": [],
        }),
        "character_agent.embodiment.observations.semantic_correction": json.dumps({
            "recurring_behaviours": [{
                "text": "Mara digs.", "evidence_ids": ["scene:still-missing"],
            }],
            "motivations": [], "values": [], "fears": [], "conflicts": [],
            "relationships": [], "contradictions": [], "evidence_gaps": [],
        }),
    })
    agent = _agent(llm)
    with pytest.raises(EmbodimentGenerationError, match="unknown evidence"):
        await agent.run(
            source_entity_id="s", source_entity_alias="S",
            canonical_identity=_canonical(),
            current_behavioural_axes={axis: 50 for axis in BEHAVIOURAL_AXES},
            current_aspects=[], current_goals=[],
            scenes=[SceneInput(scene_id="s1", name="Scene", description="", created_at=None)],
        )


@pytest.mark.asyncio
async def test_embodiment_corrects_semantic_evidence_and_canonicalizes_bare_id():
    valid_perspective = json.dumps({"perspectives": [{
        "scene_id": "s1", "source_type": "participated",
        "awareness_level": 50, "confidence": 50,
        "summary": "Scene.", "interpretation": "Ok.",
        "character_reflection": "I remember.",
        "memory_strength": 50, "importance": 3,
    }]})
    llm = FakeLLM({
        "character_agent.embodiment.character_incorporation": valid_perspective,
        "character_agent.embodiment.scene_interpretation": json.dumps({
            "scene_enrichments": [{
                "scene_id": "s1", "emotions": [], "beliefs": [], "impacts": [],
            }],
        }),
        "character_agent.embodiment.observations": json.dumps({
            "recurring_behaviours": [{
                "text": "Unsupported.", "evidence_ids": ["invented"],
            }],
        }),
        "character_agent.embodiment.observations.semantic_correction": json.dumps({
            "recurring_behaviours": [{
                "text": "Grounded.", "evidence_ids": ["s1"],
            }],
        }),
        "character_agent.embodiment.profile_update": _profile_update_output(),
    })

    result = await _agent(llm).run(
        source_entity_id="source-1", source_entity_alias="Source",
        canonical_identity=_canonical(),
        current_behavioural_axes={axis: 50 for axis in BEHAVIOURAL_AXES},
        current_aspects=[], current_goals=[],
        scenes=[SceneInput(scene_id="s1", name="Scene", description="Evidence.")],
    )

    assert result.observations.recurring_behaviours[0].evidence_ids == ["scene:s1"]
    correction_call = next(
        call for call in llm.calls
        if call["usage_tag"].endswith(".semantic_correction")
    )
    correction_payload = json.loads(correction_call["messages"][1]["content"])
    assert correction_payload["validation_error"]["offending_ids"] == [
        "scene:invented"
    ]
    assert correction_payload["validation_error"]["allowed_ids"] == ["scene:s1"]


@pytest.mark.asyncio
async def test_embodiment_rejects_mismatched_scene_ids():
    llm = FakeLLM({
        "character_agent.embodiment.character_incorporation": json.dumps({
            "perspectives": [{
                "scene_id": "wrong_id", "source_type": "participated",
                "awareness_level": 50, "confidence": 50,
                "summary": "Scene.", "interpretation": "Ok.",
                "character_reflection": "I remember.",
                "memory_strength": 50, "importance": 3, "status": "active",
            }],
        }),
    })
    agent = _agent(llm)
    with pytest.raises(EmbodimentGenerationError, match="scene_ids must match"):
        await agent.run(
            source_entity_id="s", source_entity_alias="S",
            canonical_identity=_canonical(),
            current_behavioural_axes={axis: 50 for axis in BEHAVIOURAL_AXES},
            current_aspects=[], current_goals=[],
            scenes=[SceneInput(scene_id="s1", name="Scene", description="", created_at=None)],
        )


@pytest.mark.asyncio
async def test_enrichment_rejects_unknown_impact_target():
    llm = FakeLLM({
        "character_agent.embodiment.character_incorporation": json.dumps({
            "perspectives": [{
                "scene_id": "s1", "source_type": "participated",
                "awareness_level": 50, "confidence": 50,
                "summary": "Scene.", "interpretation": "Grounded.",
                "character_reflection": "I remember.",
                "memory_strength": 50, "importance": 3,
            }],
        }),
        "character_agent.embodiment.scene_interpretation": json.dumps({
            "scene_enrichments": [{
                "scene_id": "s1", "emotions": [], "beliefs": [],
                "impacts": [{
                    "impact_type": "goal_change", "target_id": "goal:unknown",
                    "direction": "advanced", "magnitude": 50,
                    "description": "Advances an unknown goal.",
                }],
            }],
        }),
    })
    with pytest.raises(EmbodimentGenerationError, match="unknown profile target"):
        await _agent(llm).run(
            source_entity_id="s", source_entity_alias="S",
            canonical_identity=_canonical(),
            current_behavioural_axes={axis: 50 for axis in BEHAVIOURAL_AXES},
            current_aspects=[], current_goals=[],
            scenes=[SceneInput(scene_id="s1", name="Scene", description="Objective")],
        )


@pytest.mark.asyncio
async def test_embodiment_pipeline_records_llm_stats():
    llm = FakeLLM({
        "character_agent.embodiment.character_incorporation": json.dumps({
            "perspectives": [{
                "scene_id": "s1", "source_type": "participated",
                "awareness_level": 50, "confidence": 50,
                "summary": "Test.", "interpretation": "Test.",
                "character_reflection": "I remember this.",
                "memory_strength": 50, "importance": 3, "status": "active",
            }],
        }),
        "character_agent.embodiment.scene_interpretation": json.dumps({
            "scene_enrichments": [{
                "scene_id": "s1", "emotions": [], "beliefs": [], "impacts": [],
            }],
        }),
        "character_agent.embodiment.observations": json.dumps({
            "recurring_behaviours": [{
                "text": "Test behaviour.", "evidence_ids": ["scene:s1"],
            }],
            "motivations": [], "values": [], "fears": [], "conflicts": [],
            "relationships": [], "contradictions": [], "evidence_gaps": [],
        }),
        "character_agent.embodiment.profile_update": _profile_update_output(),
    })
    agent = _agent(llm)
    result = await agent.run(
        source_entity_id="s", source_entity_alias="S",
        canonical_identity=_canonical(),
        current_behavioural_axes={axis: 50 for axis in BEHAVIOURAL_AXES},
        current_aspects=[], current_goals=[],
        scenes=[SceneInput(scene_id="s1", name="Scene", description="", created_at=None)],
    )
    assert result.total_llm_calls == 4
    assert result.total_tokens_est >= 5
    for record in result.llm_calls:
        assert record.stage in (
            "character incorporation", "scene psychological enrichment",
            "cross-scene observations", "profile updates",
        )
        assert record.usage_tag.startswith("character_agent.embodiment.")
        assert record.input_chars > 0
        assert record.output_chars > 0
    assert set(agent.stage_elapsed_seconds) == {
        "character incorporation", "scene psychological enrichment",
        "cross-scene observations", "profile updates",
    }


def test_profile_update_requires_all_eight_unique_axes():
    valid = json.loads(_profile_update_output())
    assert len(ProfileUpdateOutput.model_validate(valid).behavioural_axes) == 8

    missing = {**valid, "behavioural_axes": valid["behavioural_axes"][:-1]}
    with pytest.raises(ValueError):
        ProfileUpdateOutput.model_validate(missing)

    duplicate = {
        **valid,
        "behavioural_axes": [
            *valid["behavioural_axes"][:-1],
            valid["behavioural_axes"][0],
        ],
    }
    with pytest.raises(ValueError, match="exactly once"):
        ProfileUpdateOutput.model_validate(duplicate)


def test_profile_update_enforces_configured_aspect_and_goal_limits():
    agent = _agent(FakeLLM({}))
    update = json.loads(_profile_update_output())
    update["aspect_updates"] = [{
        "operation": "add", "name": "Fifth aspect", "category": "identity",
        "justification": "Grounded.", "confidence": 0.8,
        "evidence_ids": ["scene:s1"],
    }]
    profile_result = ProfileUpdateOutput.model_validate(update)
    with pytest.raises(EmbodimentGenerationError, match="aspect limit"):
        agent._validate_profile_limits(
            current_aspects=[
                {"name": f"Aspect {index}"} for index in range(agent.max_aspects)
            ],
            current_goals=[],
            profile_result=profile_result,
        )

    update["aspect_updates"] = []
    update["goal_updates"] = [{
        "operation": "add", "title": "Fourth goal", "goal_type": "desire",
        "justification": "Grounded.", "confidence": 0.8,
        "evidence_ids": ["scene:s1"],
    }]
    profile_result = ProfileUpdateOutput.model_validate(update)
    with pytest.raises(EmbodimentGenerationError, match="goal limit"):
        agent._validate_profile_limits(
            current_aspects=[],
            current_goals=[
                {"title": f"Goal {index}"} for index in range(agent.max_goals)
            ],
            profile_result=profile_result,
        )


def test_embodiment_source_concurrency_defaults_and_bounds():
    assert Settings().character_agent_embodiment_source_concurrency == 4
    assert Settings(
        character_agent_embodiment_source_concurrency=1
    ).character_agent_embodiment_source_concurrency == 1
    with pytest.raises(ValueError):
        Settings(character_agent_embodiment_source_concurrency=0)
    with pytest.raises(ValueError):
        Settings(character_agent_embodiment_source_concurrency=17)
    assert Settings().character_agent_embodiment_semantic_correction_attempts == 1
    assert Settings(
        character_agent_embodiment_semantic_correction_attempts=0
    ).character_agent_embodiment_semantic_correction_attempts == 0
    with pytest.raises(ValueError):
        Settings(character_agent_embodiment_semantic_correction_attempts=4)


def test_embodiment_checkpoint_key_invalidates_changed_evidence_or_model():
    from app.tasks.character_embodiment import _checkpoint_cache_key

    common = {
        "revision": 2,
        "canonical_identity": _canonical(),
        "axes": {axis: 50 for axis in BEHAVIOURAL_AXES},
        "aspects": [],
        "goals": [],
        "model_targets": {"observations": "openrouter:model-a"},
    }
    first = _checkpoint_cache_key(
        **common,
        source_group={"source_id": "src", "scenes": [{"scene_id": "s1"}]},
    )
    changed_evidence = _checkpoint_cache_key(
        **common,
        source_group={"source_id": "src", "scenes": [{"scene_id": "s2"}]},
    )
    changed_model = _checkpoint_cache_key(
        **{**common, "model_targets": {"observations": "openrouter:model-b"}},
        source_group={"source_id": "src", "scenes": [{"scene_id": "s1"}]},
    )

    assert first != changed_evidence
    assert first != changed_model


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
    assert "character_embodiment_checkpoints" in inspect(engine).get_table_names()
    checkpoint_columns = {
        column["name"]
        for column in inspect(engine).get_columns(
            "character_embodiment_checkpoints"
        )
    }
    assert {
        "draft_id", "generation_revision", "source_index", "stage",
        "cache_key", "payload", "prompt_version", "model_target",
    } <= checkpoint_columns


def test_read_embodiment_draft_ignores_legacy_subtitle_change_in_observations():
    now = datetime.now(timezone.utc)
    draft = CharacterEmbodimentDraft(
        id=str(uuid4()), ontology_id=1, source_entity_id="entity-mara",
        created_by_user_id=1, status="ready", generation_revision=1,
        observations=json.dumps({
            "identity_description": {
                "text": "Mara", "evidence_ids": ["scene:one"],
            },
            "subtitle_change": None,
        }),
        created_at=now, updated_at=now,
    )

    result = CharacterEmbodimentService.read(draft)

    assert result.observations is not None
    assert result.observations.identity_description.text == "Mara"


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


class _TimelineResult:
    def __init__(self, row=None):
        self.row = row

    async def single(self):
        return self.row


class _TimelineTx:
    def __init__(self):
        self.calls = []

    async def run(self, query, **params):
        self.calls.append((query, params))
        if "RETURN aspect.id AS id" in query:
            return _TimelineResult({"id": "persisted-aspect"})
        if "RETURN target.id AS target_id" in query:
            return _TimelineResult({"target_id": params["target_id"]})
        return _TimelineResult()


@pytest.mark.asyncio
async def test_timeline_persists_removed_impact_target_as_inactive_assignment():
    aspect = {
        "suggestion_id": "aspect:trusting",
        "name": "Trusting",
        "category": "identity",
        "description": "Tends to trust others.",
        "importance": 3,
        "intensity": 60,
        "justification": "Shown in the scene.",
        "confidence": 0.8,
        "evidence_ids": ["scene:1"],
    }
    revision_0 = {
        "revision_number": 0,
        "name": "Mara",
        "trait_adherence": 80,
        "behavioural_axes": {axis: 50 for axis in BEHAVIOURAL_AXES},
        "active_aspects": [aspect],
        "active_goals": [],
    }
    revision_1 = {
        **revision_0,
        "revision_number": 1,
        "source_group_id": "source-1",
        "active_aspects": [],
    }
    timeline = CharacterTimelineProjection.model_validate({
        "revisions": [revision_0, revision_1],
        "source_projections": [{
            "source_group_id": "source-1",
            "starting_revision_number": 0,
            "perspectives": [{
                "scene_id": "scene-1",
                "source_type": "participated",
                "awareness_level": 100,
                "confidence": 100,
                "summary": "Mara trusted someone.",
                "interpretation": "The trust was misplaced.",
                "memory_strength": 80,
                "importance": 4,
                "impacts": [{
                    "impact_type": "aspect_change",
                    "target_id": "aspect:trusting",
                    "direction": "invalidated",
                    "magnitude": 90,
                    "description": "Her trusting nature was undermined.",
                }],
            }],
            "resulting_revision": revision_1,
        }],
    })
    tx = _TimelineTx()

    await CharacterAgentService(None, None)._persist_timeline_tx(
        tx,
        {"id": "agent-1", "ontology_id": 1, "name": "Mara"},
        timeline,
        "2026-07-29T00:00:00+00:00",
        provider=None,
        model=None,
        prompt_version=None,
        profile_target_ids={},
    )

    inactive_assignment = next(
        params for query, params in tx.calls
        if "rel.status='inactive'" in query and "HAS_ASPECT" in query
    )
    assert inactive_assignment["agent_id"] == "agent-1"
    impact = next(
        params for query, params in tx.calls if "RETURN target.id AS target_id" in query
    )
    assert impact["target_id"] == "persisted-aspect"


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
        ScenePerspectiveBundleOutput, EmbodimentObservationsOutput,
    )
    result = EmbodyAgentResult(
        source_entity_id="s1",
        source_entity_alias="Source 1",
        perspectives=[
            ScenePerspectiveBundleOutput(
                scene_id="sc1", source_type="participated",
                awareness_level=50, confidence=50,
                summary="Test.", interpretation="Test.",
                character_reflection="I remember this.",
                memory_strength=50, importance=3, status="active",
            ),
        ],
        observations=EmbodimentObservationsOutput(),
        axis_updates=[],
        aspect_updates=[],
        goal_updates=[],
        llm_calls=[
            LLMCallRecord(stage="s1", usage_tag="t1", provider="openai", model="m1",
                          input_chars=100, output_chars=50,
                          input_tokens_est=25, output_tokens_est=13, total_tokens_est=38),
            LLMCallRecord(stage="s2", usage_tag="t2", provider="openai", model="m2",
                          input_chars=200, output_chars=100,
                          input_tokens_est=50, output_tokens_est=25, total_tokens_est=75),
        ],
    )
    assert result.total_llm_calls == 2
    assert result.total_tokens_est == 113
