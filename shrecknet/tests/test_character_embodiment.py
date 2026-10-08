import asyncio
import json
from datetime import datetime, timezone
from inspect import getsource
from uuid import uuid4

import pytest
from neo4j.time import DateTime
from sqlalchemy import create_engine, inspect

from app.core.config_store import LLMModelTarget, Settings
from app.jobs.character_agent.embody_agent import (
    EMBODIMENT_LLM_MAX_TOKENS,
    EmbodyAgent,
    EmbodimentGenerationError,
    ScenePerspectiveLLMOutput,
    _AspectSignalLLMOutput,
    _BaselineTraitObservationsLLMOutput,
    _SceneEnrichmentsLLMOutput,
    _SceneTraitInterpretationsLLMOutput,
)
from app.jobs.character_agent.embody_agent_prompts import (
    PSYCHOLOGICAL_ANALYSIS_PROMPT,
    PERSPECTIVE_PROMPT,
    PROMPT_VERSION,
    TRAIT_INTERPRETATION_PROMPT,
)
from app.schemas.character_agent import (
    CharacterAgentCreateRequest, CharacterAgentRead, CharacterAgentUpdate,
    EmbodimentEvidence, EmbodimentProposal,
    ProfileUpdateOutput,
    EmotionalInterpretationOutput, SceneEnrichmentsOutput, SceneInput, ScenePerspectiveOutput,
    CharacterTimelineProjection,
)
from app.services.character_embodiment_service import CharacterEmbodimentService, _json_safe
from app.services.character_agent_service import CharacterAgentService
from app.jobs.character_agent.profile import _apply_aspect_ops, _apply_goal_ops
from app.tasks.character_embodiment import _public_error_message
from app.db.base import Base
from app.models.character_embodiment import CharacterEmbodimentDraft  # noqa: F401


from app.schemas.character_traits import TraitProfile


def _profile_update_output():
    return json.dumps({"aspect_updates": [], "goal_updates": []})


def test_provider_unavailable_error_is_actionable_for_embodiment_draft_reads():
    error = EmbodimentGenerationError(
        "authored baseline provider is unavailable",
        category="provider_unavailable",
        stage="authored baseline",
        provider_id="openrouter",
        model_name="qwen/qwen3.7-flash",
        provider_reason="model_unavailable",
    )

    assert _public_error_message(error) == (
        "Character embodiment cannot start because provider 'openrouter' "
        "model 'qwen/qwen3.7-flash' is unavailable (model_unavailable). "
        "Configure an available model and retry."
    )
    assert error.details()["failure_category"] == "provider_unavailable"
    assert error.details()["provider_reason"] == "model_unavailable"


@pytest.mark.asyncio
async def test_provider_watchdog_failure_preserves_the_provider_reason():
    class WatchdogLLM:
        async def chat(self, **_kwargs):
            raise RuntimeError("chat job failed error=attempt watchdog exceeded 45.0s")

    with pytest.raises(EmbodimentGenerationError) as raised:
        await _agent(WatchdogLLM()).generate_perspectives(
            source_entity_id="source", source_entity_alias="Source",
            canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
            current_aspects=[], current_goals=[], scenes=scenes(1),
        )

    assert raised.value.category == "provider_timeout"
    assert raised.value.details()["provider_reason"] == (
        "chat job failed error=attempt watchdog exceeded 45.0s"
    )


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
        max_aspects=4,
        max_goals=3,
        semantic_correction_attempts=semantic_correction_attempts,
    )


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
    await progress.stage(0, [3])
    await progress.complete(0)

    assert max_writes_running == 1
    assert bundles[0]["status"] == "done"
    assert bundles[0]["done_steps"] == [1, 2, 3]
    assert bundles[1]["status"] == "failed"
    assert [item[1] for item in payloads] == sorted(item[1] for item in payloads)


@pytest.mark.asyncio
async def test_progress_exposes_each_concurrent_scene_chunk(monkeypatch):
    from app.tasks import character_embodiment as task_module

    async def fake_update_job_progress(*_args):
        return None

    monkeypatch.setattr(task_module, "update_job_progress", fake_update_job_progress)
    bundles = [{
        "index": 1, "source_name": "Source", "status": "pending",
        "active_steps": [], "done_steps": [], "elapsed_seconds": None,
    }]
    progress = task_module._EmbodimentProgress(
        job_id=9, draft_id="draft-1", bundles=bundles,
        source_groups=[{"source_alias": "Source"}],
    )
    progress.configure_chunks(0, [[{"scene_id": "s1"}], [{"scene_id": "s2"}]])

    await asyncio.gather(
        progress.stage(0, [1], chunk_index=0),
        progress.stage(0, [1], chunk_index=1),
    )
    await asyncio.gather(
        progress.stage(0, [2], chunk_index=0),
        progress.stage(0, [2], chunk_index=1),
    )

    assert bundles[0]["active_steps"] == [2]
    assert bundles[0]["parallel"]["active"] is True
    assert bundles[0]["parallel"]["active_chunk_count"] == 2
    assert [chunk["status"] for chunk in bundles[0]["chunks"]] == [
        "processing", "processing",
    ]

    await asyncio.gather(progress.chunk_complete(0, 0), progress.chunk_complete(0, 1))
    assert bundles[0]["done_steps"] == [1, 2]
    assert [chunk["status"] for chunk in bundles[0]["chunks"]] == ["done", "done"]


def test_source_scene_analysis_is_partitioned_into_five_scene_chunks():
    from app.tasks.character_embodiment import _scene_analysis_chunks

    chunks = _scene_analysis_chunks({
        "scenes": [
            {"scene_id": f"s{index}", "name": "Scene", "description": "", "created_at": str(index)}
            for index in range(12)
        ],
    })

    assert [len(chunk) for chunk in chunks] == [5, 5, 2]
    assert [scene["scene_id"] for chunk in chunks for scene in chunk] == [
        f"s{index}" for index in range(12)
    ]


def test_profile_update_enforces_per_bundle_operation_caps():
    update = json.loads(_profile_update_output())
    aspect = {
        "operation": "add", "name": "Aspect", "category": "identity",
        "justification": "Grounded.", "confidence": 0.8,
        "evidence_ids": ["scene:s1"],
    }
    goal = {
        "operation": "add", "title": "Goal", "goal_type": "desire",
        "justification": "Grounded.", "confidence": 0.8,
        "evidence_ids": ["scene:s1"],
    }
    update["aspect_updates"] = [
        {**aspect, "name": f"Aspect {index}"} for index in range(3)
    ]
    with pytest.raises(ValueError, match="at most 2"):
        ProfileUpdateOutput.model_validate(update)

    update["aspect_updates"] = []
    update["goal_updates"] = [
        {**goal, "title": f"Goal {index}"} for index in range(2)
    ]
    with pytest.raises(ValueError, match="at most 1"):
        ProfileUpdateOutput.model_validate(update)


def test_profile_capacity_retains_importance_then_recency():
    update = json.loads(_profile_update_output())
    update["aspect_updates"] = [{
        "operation": "add", "name": "New modest aspect", "category": "identity",
        "importance": 3, "justification": "Grounded.", "confidence": 0.8,
        "evidence_ids": ["scene:s1"],
    }]
    profile = ProfileUpdateOutput.model_validate(update)
    aspects = [
        {"name": "Old vital aspect", "importance": 5, "created_at": "2020-01-01"},
        {"name": "Old weak aspect", "importance": 2, "created_at": "2020-01-01"},
    ]
    _apply_aspect_ops(aspects, profile.aspect_updates, max_active=2)
    assert [item["name"] for item in aspects] == [
        "Old vital aspect", "New modest aspect",
    ]

    update["aspect_updates"] = []
    update["goal_updates"] = [{
        "operation": "add", "title": "New equal goal", "goal_type": "desire",
        "priority": 40, "justification": "Grounded.", "confidence": 0.8,
        "evidence_ids": ["scene:s1"],
    }]
    profile = ProfileUpdateOutput.model_validate(update)
    goals = [
        {"title": "Old important goal", "priority": 90, "created_at": "2020-01-01"},
        {"title": "Old equal goal", "priority": 40, "created_at": "2020-01-01"},
    ]
    _apply_goal_ops(goals, profile.goal_updates, max_active=2)
    assert [item["title"] for item in goals] == [
        "Old important goal", "New equal goal",
    ]


def test_complete_goal_and_remove_aspect_make_them_inactive_in_projection():
    update = json.loads(_profile_update_output())
    update["aspect_updates"] = [{
        "operation": "remove", "name": "Former role",
        "justification": "No longer applies.", "confidence": 0.8,
        "evidence_ids": ["scene:s1"],
    }]
    update["goal_updates"] = [{
        "operation": "complete", "title": "Find the key",
        "justification": "The key was found.", "confidence": 0.9,
        "evidence_ids": ["scene:s1"],
    }]
    profile = ProfileUpdateOutput.model_validate(update)
    aspects = [{"name": "former  ROLE", "importance": 3}]
    goals = [{"title": "find THE key", "priority": 80}]
    _apply_aspect_ops(aspects, profile.aspect_updates, max_active=8)
    _apply_goal_ops(goals, profile.goal_updates, max_active=8)
    assert aspects == []
    assert goals == []


def test_proposal_requires_unique_suggestion_ids():
    raw = {
        "name": "Mara", "background_story": "Grounded.", "image_url": None,
        "trait_profile": TraitProfile().model_dump(mode="json"),
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
            "trait_profile": TraitProfile().model_dump(mode="json"),
            "active_aspects": [], "active_goals": [],
        }],
        "source_projections": [],
    })
    assert timeline.revisions[0].subtitle == "The Doll"


def test_embodiment_scene_query_does_not_reject_stale_instance_metadata():
    source = getsource(CharacterEmbodimentService.load_embodiment_input)

    assert "coalesce(scene.instance_id" not in source
    assert "coalesce(scene.ontology_id, $ontology_id) = $ontology_id" in source


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
        if "AS scoped_scene_ids" in query:
            return _TimelineResult({"scoped_scene_ids": params["scene_ids"]})
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
        "trait_profile": TraitProfile().model_dump(mode="json"),
        "active_aspects": [aspect],
        "active_goals": [],
    }
    revision_1 = {
        **revision_0,
        "revision_number": 1,
        "source_group_id": "source-1",
        "scene_ids": ["scene-1"],
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
                "behavioral_evidence": [{
                    "action": "Bonded with two other individuals in the café",
                    "context": "Amid threats and whispers, forming an alliance with unlikely allies",
                    "source_quote": "three unlikely allies bond amid threats and whispers",
                }],
                "impacts": [{
                    "impact_type": "aspect_change",
                    "target_id": "aspect:trusting",
                    "target": {
                        "id": "aspect:trusting",
                        "type": "aspect",
                        "name": "Trusting",
                        "description": "Tends to trust others.",
                        "instance_name": "Mara",
                    },
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
    final_aspect_status = next(
        params for query, params in tx.calls
        if "WHEN aspect.id IN $active_ids" in query
    )
    final_goal_status = next(
        params for query, params in tx.calls
        if "WHEN goal.id IN $active_ids" in query
    )
    assert final_aspect_status["active_ids"] == []
    assert final_goal_status["active_ids"] == []
    assert final_goal_status["completed_titles"] == []
    impact = next(
        params for query, params in tx.calls if "RETURN target.id AS target_id" in query
    )
    assert impact["target_id"] == "persisted-aspect"
    assert impact["props"] == {
        "id": impact["props"]["id"],
        "ontology_id": 1,
        "impact_type": "aspect_change",
        "direction": "invalidated",
        "magnitude": 90,
        "description": "Her trusting nature was undermined.",
        "created_at": "2026-07-29T00:00:00+00:00",
        "updated_at": "2026-07-29T00:00:00+00:00",
    }

    perspective = next(
        params for query, params in tx.calls
        if "CREATE (perspective:ScenePerspective)" in query
    )
    assert "behavioral_evidence" not in perspective["props"]

    # Exercise every graph ``props`` payload emitted by a fully hydrated
    # timeline. Neo4j permits scalar values and lists of scalars, never maps
    # or nested lists. Display references are API/draft payload only.
    for _, params in tx.calls:
        if "props" not in params:
            continue
        for value in params["props"].values():
            assert not isinstance(value, dict)
            if isinstance(value, list):
                assert all(not isinstance(item, (dict, list)) for item in value)


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
                scene_id="sc1", source_type="participated", evidence_ids=["scene:sc1"],
                awareness_level=50, confidence=50,
                summary="Test.", interpretation="Test.",
                character_reflection="I remember this.",
                memory_strength=50, importance=3, status="active",
            ),
        ],
        observations=EmbodimentObservationsOutput(),
        trait_profile=TraitProfile(),
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


class BatchLLM:
    """Deterministic provider fixture; semantic quality is evaluated separately."""
    def __init__(self, corruption=None):
        self.calls=[]
        self.corruption=corruption

    async def chat(self, **kwargs):
        from test_character_traits import observation
        self.calls.append(kwargs)
        payload=json.loads(kwargs['messages'][1]['content'])
        stage=kwargs['usage_tag'].rsplit('.',1)[-1]
        if stage == 'structured_fallback':
            stage = kwargs['usage_tag'].rsplit('.', 2)[-2]
        if stage.isdigit() and '.truncation_recovery.' in kwargs['usage_tag']:
            stage = 'scene_interpretation'
        if stage=='baseline':
            return json.dumps({'trait_evidence':[]})
        if stage=='character_incorporation':
            result={'perspectives':[dict(
                source_type='participated',awareness_level=90,confidence=90,
                summary='Returned an untraceable overpayment.',interpretation='Keeping it would exploit another.',
                character_reflection='REFLECTION MUST NOT BECOME EVIDENCE',memory_strength=80,importance=3,
                behavioral_evidence=[{'action':'Returned an untraceable overpayment.',
                    'context':'The character could keep it.',
                    'source_quote':'returned an untraceable overpayment'}])
                for s in payload['scenes']]}
            return json.dumps(result)
        if stage=='scene_interpretation':
            return json.dumps({'scene_enrichments':[dict(
                emotions=[],beliefs=[],impacts=[],
                aspect_signals=[],goal_signals=[]) for p in payload['perspectives']]})
        if stage=='trait_interpretation':
            return json.dumps({'scene_trait_interpretations':[
                {'trait_candidates': [{'trait':'integrity','polarity':'high',
                    'situation_type':'exploitation:other:ordinary',
                    'justification':'Returned an untraceable overpayment despite a choice to keep it.'}]
                 if item['behavioral_evidence'] else []} for item in payload['scenes']
            ]})
        if stage=='identity_signals':
            return json.dumps({'scene_identity_signals':[
                {'aspect_signals': [], 'goal_signals': []} for _ in payload['perspectives']
            ]})
        if stage=='observations':
            items=[observation(scene=b['scene']['scene_id']).model_dump() for b in payload['scene_bundles']]
            if self.corruption=='unknown': items[0]['evidence_ids']=['scene:foreign']
            return json.dumps({'trait_evidence':items})
        if stage=='profile_update':
            refs=[e['position'] for e in payload['trait_evidence'] if e['eligible']]
            return json.dumps({'trait_proposals':[dict(trait='integrity', observation_indexes=refs,
                justification='Repeated voluntary choices.',addresses_contradictions='No opposing behavior.')],
                'aspect_updates':[],'goal_updates':[]})
        raise AssertionError(stage)


@pytest.mark.asyncio
async def test_single_scene_unwrapped_perspective_is_normalized_without_retry():
    class UnwrappedPerspectiveLLM(BatchLLM):
        async def chat(self, **kwargs):
            raw = await super().chat(**kwargs)
            if kwargs["usage_tag"].endswith(".character_incorporation"):
                return json.dumps(json.loads(raw)["perspectives"][0])
            return raw

    llm = UnwrappedPerspectiveLLM()
    analysis = await _agent(llm).analyze(
        source_entity_id="source", source_entity_alias="Source",
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(1),
    )

    assert analysis.perspectives[0].scene_id == "s0"
    assert not any("schema_correction" in call["usage_tag"] for call in llm.calls)


@pytest.mark.asyncio
async def test_every_embodiment_generation_call_requests_strict_json_schema():
    llm = BatchLLM()
    agent = _agent(llm)
    await agent.initialize(canonical_identity=_canonical(), entity_id="e1")
    await agent.run(
        source_entity_id="source", source_entity_alias="Source",
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(1), batch_id="source",
    )

    assert {call["usage_tag"].rsplit(".", 1)[-1] for call in llm.calls} == {
        "baseline", "character_incorporation", "scene_interpretation", "trait_interpretation",
    }
    for call in llm.calls:
        assert call["max_tokens"] == EMBODIMENT_LLM_MAX_TOKENS == 10_000
        response_format = call["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["strict"] is True
        assert response_format["json_schema"]["schema"]["type"] == "object"
    trait_format = next(
        call["response_format"] for call in llm.calls
        if call["usage_tag"].endswith(".scene_interpretation")
    )
    definitions = trait_format["json_schema"]["schema"]["$defs"]
    goal_impact = definitions["_GoalImpactLLMOutput"]
    assert "target_index" in goal_impact["properties"]
    assert "target_id" not in goal_impact["properties"]
    assert definitions["_SceneEnrichmentLLMOutput"]["properties"]["impacts"]["maxItems"] == 0


def test_psychological_llm_contract_excludes_backend_references_and_bounds_output():
    schema = _SceneEnrichmentsLLMOutput.model_json_schema()
    fields = schema["$defs"]["_SceneEnrichmentLLMOutput"]["properties"]
    assert fields["emotions"]["maxItems"] == 2
    assert fields["beliefs"]["maxItems"] == 2
    assert fields["impacts"]["maxItems"] == 2
    assert "trait_candidates" not in fields


def test_trait_llm_schemas_have_only_four_semantic_fields():
    expected = {"trait", "polarity", "situation_type", "justification"}
    for output_type in (_BaselineTraitObservationsLLMOutput, _SceneTraitInterpretationsLLMOutput):
        schema = output_type.model_json_schema()
        assert set(schema["$defs"]["_TraitCandidateLLMOutput"]["properties"]) == expected


def test_generated_emotion_uses_public_valence_scale_and_normalizes_legacy_values():
    assert EmotionalInterpretationOutput(
        arousal=80, valence=25, description="Uneasy.",
    ).valence == 25
    assert EmotionalInterpretationOutput(
        arousal=80, valence=-50, description="Legacy unease.",
    ).valence == 25


@pytest.mark.asyncio
async def test_incorporation_schema_has_exact_count_and_only_llm_owned_fields():
    llm = BatchLLM()
    await _agent(llm).generate_perspectives(
        source_entity_id="source", source_entity_alias="Source",
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(3),
    )

    call = next(call for call in llm.calls if call["usage_tag"].endswith(".character_incorporation"))
    schema = call["response_format"]["json_schema"]["schema"]
    perspectives = schema["properties"]["perspectives"]
    assert perspectives["minItems"] == perspectives["maxItems"] == 3
    fields = schema["$defs"]["ScenePerspectiveLLMOutput"]["properties"]
    assert {"scene_id", "evidence_ids", "status"}.isdisjoint(fields)
    assert fields["summary"]["maxLength"] == 300
    assert fields["interpretation"]["maxLength"] == 700
    assert fields["character_reflection"]["maxLength"] == 500


def test_incorporation_llm_text_fields_are_bounded():
    valid = {
        "source_type": "participated", "awareness_level": 50, "confidence": 50,
        "summary": "Summary.", "interpretation": "Interpretation.",
        "character_reflection": "I react.", "memory_strength": 50, "importance": 3,
    }
    assert ScenePerspectiveLLMOutput.model_validate(valid)
    with pytest.raises(ValueError, match="String should have at most 300 characters"):
        ScenePerspectiveLLMOutput.model_validate(valid | {"summary": "x" * 301})


@pytest.mark.asyncio
async def test_truncated_incorporation_retries_once_with_compact_contract():
    class RecoveringLLM(BatchLLM):
        def __init__(self):
            super().__init__()
            self.call_tags = []

        async def chat(self, **kwargs):
            self.call_tags.append(kwargs["usage_tag"])
            request = dict(kwargs)
            recovering = request["usage_tag"].endswith(".truncation_recovery")
            if recovering:
                request["usage_tag"] = request["usage_tag"].removesuffix(".truncation_recovery")
            raw = await super().chat(**request)
            self.last_response_metadata = {"finish_reason": "stop" if recovering else "length"}
            return raw

    llm = RecoveringLLM()
    result = await _agent(llm).generate_perspectives(
        source_entity_id="source", source_entity_alias="Source",
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(2),
    )

    assert len(result.perspectives) == 2
    assert llm.call_tags == [
        "character_agent.embodiment.character_incorporation",
        "character_agent.embodiment.character_incorporation.truncation_recovery",
    ]


@pytest.mark.asyncio
async def test_truncated_psychological_analysis_retries_smaller_batches_without_reincorporation():
    class RecoveringLLM(BatchLLM):
        async def chat(self, **kwargs):
            raw = await super().chat(**kwargs)
            tag = kwargs["usage_tag"]
            if tag.endswith(".scene_interpretation"):
                self.last_response_metadata = {"finish_reason": "length"}
            else:
                self.last_response_metadata = {"finish_reason": "stop"}
            return raw

    llm = RecoveringLLM()
    analysis = await _agent(llm).analyze(
        source_entity_id="source", source_entity_alias="Source",
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(3),
    )

    assert len(analysis.perspectives) == 3
    tags = [call["usage_tag"] for call in llm.calls]
    assert tags.count("character_agent.embodiment.character_incorporation") == 1
    assert tags.count("character_agent.embodiment.scene_interpretation") == 1
    assert sorted(tag for tag in tags if ".truncation_recovery." in tag) == [
        "character_agent.embodiment.scene_interpretation.truncation_recovery.1",
        "character_agent.embodiment.scene_interpretation.truncation_recovery.1",
    ]


@pytest.mark.asyncio
async def test_truncated_embodiment_response_is_rejected_before_schema_validation():
    class TruncatedLLM(BatchLLM):
        async def chat(self, **kwargs):
            request = dict(kwargs)
            if request['usage_tag'].endswith('.truncation_recovery'):
                request['usage_tag'] = request['usage_tag'].removesuffix('.truncation_recovery')
            raw = await super().chat(**request)
            self.last_response_metadata = {"finish_reason": "length"}
            return raw

    with pytest.raises(EmbodimentGenerationError) as raised:
        await _agent(TruncatedLLM()).generate_perspectives(
            source_entity_id="source", source_entity_alias="Source",
            canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
            current_aspects=[], current_goals=[], scenes=scenes(1),
        )

    assert raised.value.category == "truncated"
    assert raised.value.retryable is True
    assert "10000-token output limit" in str(raised.value)


@pytest.mark.asyncio
async def test_unresolvable_optional_impacts_are_dropped_without_retry():
    class UnresolvableImpactLLM(BatchLLM):
        async def chat(self, **kwargs):
            raw = await super().chat(**kwargs)
            if kwargs["usage_tag"].endswith(".scene_interpretation"):
                payload = json.loads(raw)
                payload["scene_enrichments"][0]["impacts"] = [{
                    "impact_type": "goal_change", "target_index": 1,
                    "direction": "advanced", "magnitude": 50,
                    "description": "Claims progress toward an absent goal.",
                }]
                return json.dumps(payload)
            return raw

    llm = UnresolvableImpactLLM()
    analysis = await _agent(llm).analyze(
        source_entity_id="source", source_entity_alias="Source",
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(1),
    )

    assert analysis.perspectives[0].impacts == []
    assert not any("schema_correction" in call["usage_tag"] for call in llm.calls)


@pytest.mark.asyncio
async def test_embodiment_falls_back_only_when_native_schema_is_unsupported():
    class UnsupportedStructuredOutputLLM(BatchLLM):
        async def chat(self, **kwargs):
            if kwargs.get("response_format") is not None:
                raise RuntimeError("response_format json_schema is unsupported")
            return await super().chat(**kwargs)

    llm = UnsupportedStructuredOutputLLM()
    analysis = await _agent(llm).analyze(
        source_entity_id="source", source_entity_alias="Source",
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(1),
    )

    assert analysis.perspectives
    assert all(call["usage_tag"].endswith(".structured_fallback") for call in llm.calls)


class AspectDirectionMismatchCorrectionLLM(BatchLLM):
    """Reproduces the aspect-direction mismatch returned by DeepSeek."""

    async def chat(self, **kwargs):
        usage_tag = kwargs['usage_tag']
        if usage_tag.endswith('.scene_interpretation.schema_correction'):
            self.calls.append(kwargs)
            rejected = json.loads(json.loads(kwargs['messages'][1]['content'])['rejected_output'])
            rejected['scene_enrichments'][0]['impacts'][0]['direction'] = 'invalidated'
            return json.dumps(rejected)

        raw = await super().chat(**kwargs)
        if usage_tag.endswith('.scene_interpretation'):
            payload = json.loads(raw)
            payload['scene_enrichments'][0]['impacts'] = [{
                'impact_type': 'aspect_change', 'target_index': 1,
                'direction': 'threatened', 'magnitude': 65,
                'description': 'The alliance is strained.',
            }]
            return json.dumps(payload)
        return raw


class MalformedObservationsLLM(BatchLLM):
    def __init__(self, *, correction_is_valid: bool):
        super().__init__()
        self.correction_is_valid = correction_is_valid

    async def chat(self, **kwargs):
        stage = kwargs['usage_tag'].rsplit('.', 1)[-1]
        if stage == 'observations':
            return json.dumps({'trait_evidence': [{
                'trait': 'curiosity', 'evidence_kind': 'behavior',
                'situation_type': 'exploration', 'direction': 'high',
                'expression_z': 1.2, 'confidence': .9, 'diagnosticity': .9,
                'behavior': 'Investigated the unknown.',
                'justification': 'The character chose exploration.',
            }]})
        if stage == 'schema_correction':
            self.calls.append(kwargs)
            return json.dumps({'trait_evidence': []} if self.correction_is_valid else {
                'trait_evidence': [{'trait': 'curiosity'}],
            })
        return await super().chat(**kwargs)


def scenes(count, offset=0):
    return [SceneInput(scene_id=f's{i}',name='Choice',description='Free, known and safe choice. Mara returned an untraceable overpayment when she could keep it.',created_at=f'{i:03}')
            for i in range(offset,offset+count)]


@pytest.mark.asyncio
async def test_trait_interpretation_rejects_duplicate_trait_for_one_perspective():
    class DuplicateTraitLLM:
        async def chat(self, **kwargs):
            candidate = {"trait": "curiosity", "polarity": "high",
                "situation_type": "exploration:other:ordinary",
                "justification": "A voluntary investigation reveals curiosity."}
            return json.dumps({"scene_trait_interpretations": [
                {"trait_candidates": [candidate, candidate]}]})

    perspective = ScenePerspectiveOutput(
        scene_id="scene-1", evidence_ids=["scene:scene-1"], source_type="participated",
        awareness_level=90, confidence=90, summary="A device appeared.",
        interpretation="It might reveal something new.", character_reflection="I want to inspect it.",
        memory_strength=80, importance=3,
        behavioral_evidence=[{"action": "Inspected the unfamiliar device.", "context": None,
                              "source_quote": "inspected the unfamiliar device"}],
    )
    with pytest.raises(EmbodimentGenerationError, match="duplicate trait candidate"):
        await _agent(DuplicateTraitLLM())._interpret_traits_batch(
            source_entity_id="source", source_entity_alias="Source", identity=_canonical(),
            perspectives=[perspective],
        )


@pytest.mark.asyncio
async def test_trait_interpretation_corrects_invalid_context():
    class ContextLLM:
        def __init__(self): self.calls = []
        async def chat(self, **kwargs):
            self.calls.append(kwargs)
            situation = ("exploration:other:ordinary" if kwargs["usage_tag"].endswith(".schema_correction")
                         else "exploration:unknown:ordinary")
            return json.dumps({"scene_trait_interpretations": [{"trait_candidates": [{
                "trait": "curiosity", "polarity": "high", "situation_type": situation,
                "justification": "The voluntary investigation reveals curiosity."}]}]})

    perspective = ScenePerspectiveOutput(
        scene_id="scene-1", evidence_ids=["scene:scene-1"], source_type="participated",
        awareness_level=90, confidence=90, summary="A device appeared.",
        interpretation="It might reveal something new.", character_reflection="I want to inspect it.",
        memory_strength=80, importance=3,
        behavioral_evidence=[{"action": "Inspected the unfamiliar device.", "context": None,
                              "source_quote": "inspected the unfamiliar device"}],
    )
    llm = ContextLLM()
    candidates = await _agent(llm)._interpret_traits_batch(
        source_entity_id="source", source_entity_alias="Source", identity=_canonical(),
        perspectives=[perspective],
    )
    assert candidates[0][0]["perspective_id"] == perspective.id
    assert candidates[0][0]["polarity"] == "high"
    assert any(call["usage_tag"].endswith(".schema_correction") for call in llm.calls)


def test_trait_interpretation_prompt_requires_one_candidate_per_trait_per_perspective():
    assert "at most one observation per trait" in TRAIT_INTERPRETATION_PROMPT
    assert "polarity" in TRAIT_INTERPRETATION_PROMPT
    assert "ST EADINESS".replace(" ", "") in TRAIT_INTERPRETATION_PROMPT


@pytest.mark.asyncio
@pytest.mark.skip(reason="cross-scene LLM correction was removed in the scene-centric pipeline")
async def test_observation_schema_correction_receives_context_and_can_return_no_evidence(monkeypatch):
    async def unrepaired(**_kwargs):
        return '{"trait_evidence": [{"trait": "curiosity"}]}'
    monkeypatch.setattr('app.jobs.character_agent.embody_agent.repair_json_text', unrepaired)
    llm = MalformedObservationsLLM(correction_is_valid=True)

    analysis = await _agent(llm).analyze(
        source_entity_id='source', source_entity_alias='Source',
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(1),
    )

    correction = next(call for call in llm.calls if call['usage_tag'].endswith('.schema_correction'))
    payload = json.loads(correction['messages'][1]['content'])
    assert payload['original_input']['allowed_evidence_ids'] == ['scene:s0']
    assert payload['validation_errors']
    assert analysis.observations.trait_evidence == []
    assert not analysis.observations_unavailable
    assert len(llm.calls) == 3


@pytest.mark.asyncio
@pytest.mark.skip(reason="cross-scene LLM correction was removed in the scene-centric pipeline")
async def test_unrecoverable_observations_create_no_evidence_or_profile_update(monkeypatch):
    async def unrepaired(**_kwargs):
        return '{"trait_evidence": [{"trait": "curiosity"}]}'
    monkeypatch.setattr('app.jobs.character_agent.embody_agent.repair_json_text', unrepaired)
    llm = MalformedObservationsLLM(correction_is_valid=False)

    result = await _agent(llm).run(
        source_entity_id='source', source_entity_alias='Source',
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(1), batch_id='bundle',
    )

    assert result.trait_evidence == [] and result.trait_changes == []
    assert result.aspect_updates == [] and result.goal_updates == []
    assert not any(call['usage_tag'].endswith('.profile_update') for call in llm.calls)


@pytest.mark.asyncio
@pytest.mark.skip(reason="embodiment now uses three LLM calls per source bundle")
async def test_source_batch_is_four_calls_with_grounded_cumulative_update():
    llm=BatchLLM()
    result=await _agent(llm).run(source_entity_id='source',source_entity_alias='Source',canonical_identity=_canonical(),
        current_trait_profile=TraitProfile(),current_aspects=[],current_goals=[],scenes=scenes(10),batch_id='batch1')
    assert len(llm.calls)==4 and result.total_llm_calls==4
    assert len(result.perspectives)==10 and len(result.trait_evidence)==10
    assert result.trait_profile.dispositional_traits['integrity'].z==.1
    assert result.trait_profile.steadiness.z is None
    for call in llm.calls[1:]:
        assert 'REFLECTION MUST NOT BECOME EVIDENCE' not in call['messages'][1]['content']
    final_input=json.loads(llm.calls[-1]['messages'][1]['content'])
    assert 'scenes' not in final_input
    assert len(final_input['trait_evidence'])==10
    assert result.trait_changes[0].evidence_ids


@pytest.mark.asyncio
async def test_sequential_chunks_use_previous_profile_and_preserve_actual_revision_links():
    from app.jobs.character_agent.profile import _build_timeline
    from app.services.character_trait_service import merge_evidence
    profile=TraitProfile();ledger=[];results=[]
    for offset in (0,3):
        llm=BatchLLM()
        result=await _agent(llm).run(source_entity_id='same-source',source_entity_alias='Source',canonical_identity=_canonical(),
            current_trait_profile=profile,current_trait_evidence=ledger,current_aspects=[],current_goals=[],
            scenes=scenes(3,offset),batch_id=f'b{offset}')
        first=json.loads(llm.calls[0]['messages'][1]['content'])
        assert 'current_profile' not in first
        profile=result.trait_profile;ledger=merge_evidence(ledger,result.trait_evidence);results.append(result)
    timeline=CharacterTimelineProjection.model_validate_json(_build_timeline('e','Mara',_canonical(),TraitProfile(),[],[],None,results))
    assert [r.revision_number for r in timeline.revisions]==[0,1,2]
    assert [p.starting_revision_number for p in timeline.source_projections]==[0,1]
    assert timeline.revisions[0].trait_profile.dispositional_traits['integrity'].point is None
    assert [len(r.trait_evidence) for r in timeline.revisions]==[0,3,3]
    assert len(ledger)==6


@pytest.mark.asyncio
async def test_backend_binds_availability_cutoff_before_grounding():
    result = await _agent(BatchLLM()).run(
        source_entity_id='source', source_entity_alias='Source',
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(3), batch_id='bundle',
    )
    assert [item.perspective_id for item in result.trait_evidence] == [p.id for p in result.perspectives]


@pytest.mark.asyncio
async def test_perspective_references_are_bound_to_input_positions():
    analysis = await _agent(BatchLLM(), semantic_correction_attempts=0).analyze(
        source_entity_id="source", source_entity_alias="Source",
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(3),
    )
    assert [item.scene_id for item in analysis.perspectives] == ["s0", "s1", "s2"]
    assert [item.evidence_ids for item in analysis.perspectives] == [
        ["scene:s0"], ["scene:s1"], ["scene:s2"],
    ]


@pytest.mark.asyncio
async def test_nested_scene_evidence_is_bound_to_parent_position():
    analysis = await _agent(BatchLLM(), semantic_correction_attempts=0).analyze(
        source_entity_id="source", source_entity_alias="Source",
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(3),
    )
    assert [item.trait_candidates[0].evidence_ids for item in analysis.perspectives] == [
        ["scene:s0"], ["scene:s1"], ["scene:s2"],
    ]


@pytest.mark.asyncio
async def test_scene_analysis_corrects_an_aspect_direction_mismatch():
    llm = AspectDirectionMismatchCorrectionLLM()

    analysis = await _agent(llm).analyze(
        source_entity_id='source', source_entity_alias='Source',
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[{'id': 'aspect-1', 'name': 'Alliance'}],
        current_goals=[], scenes=scenes(2),
    )

    correction = next(call for call in llm.calls if call['usage_tag'].endswith('.schema_correction'))
    payload = json.loads(correction['messages'][1]['content'])
    assert correction['response_format']['type'] == 'json_schema'
    assert correction['response_format']['json_schema']['strict'] is True
    assert any(error['loc'][-1] == 'direction' for error in payload['validation_errors'])
    assert analysis.perspectives[0].impacts[0].direction.value == 'invalidated'
    assert analysis.perspectives[0].impacts[0].target_id == 'aspect-1'


@pytest.mark.asyncio
async def test_scene_analysis_schema_separates_goal_and_aspect_impact_directions():
    llm = BatchLLM()
    await _agent(llm).analyze(
        source_entity_id='source', source_entity_alias='Source',
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[{'id': 'aspect-1', 'name': 'Alliance'}],
        current_goals=[{'id': 'goal-1', 'title': 'Protect the alliance'}],
        scenes=scenes(1),
    )

    call = next(call for call in llm.calls if call['usage_tag'].endswith('.scene_interpretation'))
    definitions = call['response_format']['json_schema']['schema']['$defs']
    goal = definitions['_GoalImpactLLMOutput']['properties']
    aspect = definitions['_AspectImpactLLMOutput']['properties']
    assert goal['direction']['enum'] == ['advanced', 'threatened']
    assert goal['target_index']['maximum'] == 1
    assert aspect['direction']['enum'] == ['created', 'reinforced', 'invalidated']
    assert aspect['target_index']['maximum'] == 1


@pytest.mark.asyncio
async def test_embodiment_uses_bounded_completion_limits():
    llm = BatchLLM()

    await _agent(llm).run(
        source_entity_id='source', source_entity_alias='Source',
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(3), batch_id='bundle',
    )

    assert llm.calls
    assert all(call['max_tokens'] == EMBODIMENT_LLM_MAX_TOKENS for call in llm.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption',['future','unknown'])
@pytest.mark.skip(reason="trait candidates are now emitted by enrichment, not cross-scene observations")
async def test_batch_rejects_future_grounding_and_unknown_trait_evidence(corruption):
    agent = _agent(BatchLLM(corruption), semantic_correction_attempts=0)
    if corruption == 'future':
        with pytest.raises(EmbodimentGenerationError):
            await agent.run(source_entity_id='s',source_entity_alias='S',canonical_identity=_canonical(),
                current_trait_profile=TraitProfile(),current_aspects=[],current_goals=[],scenes=scenes(3))
    else:
        result = await agent.run(source_entity_id='s',source_entity_alias='S',canonical_identity=_canonical(),
            current_trait_profile=TraitProfile(),current_aspects=[],current_goals=[],scenes=scenes(3))
        assert result.trait_evidence == [] and result.trait_changes == []


@pytest.mark.asyncio
async def test_authored_only_initialization_is_one_call_and_unknown_is_preserved():
    llm=BatchLLM()
    profile,evidence,observations=await _agent(llm).initialize(canonical_identity=_canonical(),entity_id='e')
    assert len(llm.calls)==1 and evidence==[]
    assert all(value.point is None for value in profile.dispositional_traits.values())
    payload=json.loads(llm.calls[0]['messages'][1]['content'])
    assert payload['identity']['authored_text']=='Canonical authored biography.'
    assert 'generated_text' not in payload['identity']


@pytest.mark.asyncio
async def test_authored_baseline_binds_provenance_after_four_field_llm_output():
    class BaselineLLM:
        async def chat(self, **_kwargs):
            return json.dumps({'trait_evidence': [{
                'trait': 'forbearance', 'polarity': 'high',
                'situation_type': 'unspecified',
                'justification': 'The authored identity explicitly says she forgives those who wrong her.',
            }]})

    profile, evidence, _ = await _agent(BaselineLLM()).initialize(
        canonical_identity=_canonical({'authored_text': 'She forgives those who wrong her.'}),
        entity_id='e1',
    )
    assert profile.estimate('forbearance').point == 6
    assert profile.estimate('forbearance').observation_count == 0
    assert evidence[0].evidence_ids == ['identity:e1']
    assert evidence[0].perspective_id is None


def test_complete_prompt_contracts():
    from app.jobs.character_agent.embody_agent_prompts import BASELINE_PROMPT
    assert 'at most 40 words' in PERSPECTIVE_PROMPT
    assert 'behavioral_evidence' in PERSPECTIVE_PROMPT
    for field in ('emotions', 'beliefs', 'impacts', 'aspect_signals', 'goal_signals'):
        assert field in PSYCHOLOGICAL_ANALYSIS_PROMPT
    for field in ('trait_candidates', 'polarity', 'situation_type', 'justification'):
        assert field in TRAIT_INTERPRETATION_PROMPT
    assert 'authored_disposition' in BASELINE_PROMPT
    assert 'expression_z' not in TRAIT_INTERPRETATION_PROMPT


def test_enrichment_requires_explicit_noop_arrays():
    raw = {
        'scene_enrichments': [{
            'scene_id': 's1', 'evidence_ids': ['scene:s1'],
            'emotions': [], 'beliefs': [], 'impacts': [],
        }],
    }
    with pytest.raises(ValueError, match='trait_candidates'):
        SceneEnrichmentsOutput.model_validate(raw)

    complete = raw['scene_enrichments'][0] | {
        'trait_candidates': [], 'aspect_signals': [], 'goal_signals': [],
    }
    validated = SceneEnrichmentsOutput.model_validate({'scene_enrichments': [complete]})
    assert validated.scene_enrichments[0].trait_candidates == []


def test_enrichment_candidates_reject_old_numeric_contract():
    candidate = {'trait': 'curiosity', 'polarity': 'high',
        'situation_type': 'exploration:other:ordinary',
        'justification': 'A voluntary investigation reveals curiosity.',
        'perspective_id': 'perspective-1', 'evidence_ids': ['scene:s1']}
    payload = {'scene_enrichments': [{
        'scene_id': 's1', 'evidence_ids': ['scene:s1'], 'emotions': [],
        'beliefs': [], 'impacts': [], 'trait_candidates': [candidate],
        'aspect_signals': [], 'goal_signals': [],
    }]}
    assert SceneEnrichmentsOutput.model_validate(payload).scene_enrichments
    candidate['expression_z'] = .7
    with pytest.raises(ValueError, match='expression_z'):
        SceneEnrichmentsOutput.model_validate(payload)


@pytest.mark.asyncio
async def test_enrichment_omitted_arrays_use_one_schema_correction(monkeypatch):
    repair_called = False

    async def unexpected_repair(**_kwargs):
        nonlocal repair_called
        repair_called = True
        return '{}'

    class OmittedArraysLLM(BatchLLM):
        async def chat(self, **kwargs):
            tag = kwargs['usage_tag']
            if tag.endswith('.scene_interpretation'):
                self.calls.append(kwargs)
                payload = json.loads(kwargs['messages'][1]['content'])
                return json.dumps({'scene_enrichments': [{
                    'emotions': [], 'beliefs': [], 'impacts': [],
                } for perspective in payload['perspectives']]})
            return await super().chat(**kwargs)

    monkeypatch.setattr('app.jobs.character_agent.embody_agent.repair_json_text', unexpected_repair)
    llm = OmittedArraysLLM()
    with pytest.raises(EmbodimentGenerationError):
        await _agent(llm).analyze(
            source_entity_id='source', source_entity_alias='Source',
            canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
            current_aspects=[], current_goals=[], scenes=scenes(1),
        )
    assert repair_called is False
    assert sum(
        call['usage_tag'].endswith('.schema_correction') for call in llm.calls
    ) == 1


@pytest.mark.asyncio
async def test_psychological_analysis_receives_only_bounded_interpretations():
    llm = BatchLLM()
    result = await _agent(llm).run(
        source_entity_id='source', source_entity_alias='Source',
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(1), batch_id='source',
    )
    trait_payload = json.loads(next(
        call['messages'][1]['content'] for call in llm.calls
        if call['usage_tag'].endswith('.scene_interpretation')
    ))
    assert len(llm.calls) == 3
    assert not any(call['usage_tag'].endswith('.profile_update') for call in llm.calls)
    assert 'scenes' not in trait_payload
    assert trait_payload['perspectives'][0]['position'] == 1
    assert 'scene_id' not in trait_payload['perspectives'][0]
    assert trait_payload['perspectives'][0]['interpretation']
    assert 'character_reflection' not in trait_payload['perspectives'][0]
    assert 'current_profile' in trait_payload
    assert result.trait_evidence


@pytest.mark.asyncio
async def test_perspective_generation_receives_no_inferred_profile():
    llm = BatchLLM()
    profile = TraitProfile()
    profile.dispositional_traits['integrity'].point = 7
    profile.dispositional_traits['integrity'].status = 'provisional'
    await _agent(llm).run(
        source_entity_id='source', source_entity_alias='Source',
        canonical_identity=_canonical(), current_trait_profile=profile,
        current_aspects=[], current_goals=[], scenes=scenes(1), batch_id='source',
    )
    perspective_payload = json.loads(next(
        call['messages'][1]['content'] for call in llm.calls
        if call['usage_tag'].endswith('.character_incorporation')
    ))
    assert 'current_profile' not in perspective_payload


@pytest.mark.asyncio
async def test_psychological_analysis_durable_signals_become_identity_additions():
    class DurableSignalsLLM(BatchLLM):
        async def chat(self, **kwargs):
            raw = await super().chat(**kwargs)
            if kwargs['usage_tag'].endswith('.scene_interpretation'):
                payload = json.loads(raw)
                enrichment = payload['scene_enrichments'][0]
                enrichment['aspect_signals'] = [{
                    'name': 'I am the village warden', 'category': 'role',
                    'description': 'Entrusted with the village watch.', 'importance': 4,
                    'justification': 'The perspective establishes an enduring office.',
                    'confidence': .9,
                }]
                enrichment['goal_signals'] = [{
                    'title': 'Protect the village', 'description': 'Maintain the village watch.',
                    'goal_type': 'obligation', 'priority': 90, 'commitment': 90,
                    'basis': 'explicit',
                    'justification': 'The character renews an ongoing commitment.',
                    'confidence': .9,
                }]
                return json.dumps(payload)
            return raw

    result = await _agent(DurableSignalsLLM()).run(
        source_entity_id='source', source_entity_alias='Source',
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(1), batch_id='source',
    )
    assert [item.name for item in result.aspect_updates] == ['I am the village warden']
    assert [item.title for item in result.goal_updates] == ['Protect the village']


@pytest.mark.asyncio
async def test_identity_revelation_adds_a_distinct_aspect_when_existing_role_is_reinforced():
    class DollRevelationLLM(BatchLLM):
        async def chat(self, **kwargs):
            raw = await super().chat(**kwargs)
            if kwargs['usage_tag'].endswith('.scene_interpretation'):
                payload = json.loads(raw)
                payload['scene_enrichments'][0]['aspect_signals'] = [{
                    'name': 'I am in a secret pact', 'category': 'role',
                    'description': 'The existing pact remains relevant.', 'importance': 4,
                    'justification': 'The scene reinforces the existing role.', 'confidence': .9,
                }]
                payload['scene_enrichments'][1]['aspect_signals'] = [{
                    'name': 'I am a Belshazar-crafted vessel', 'category': 'identity',
                    'description': 'Ernst is a constructed vessel whose memories were threaded at creation.',
                    'importance': 5,
                    'justification': 'A confirmed revelation establishes Ernst\'s constructed origin.',
                    'confidence': .95,
                }]
                return json.dumps(payload)
            return raw

    result = await _agent(DollRevelationLLM()).run(
        source_entity_id='source', source_entity_alias='Source',
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[{'id': 'aspect-pact', 'name': 'I am in a secret pact'}],
        current_goals=[], scenes=scenes(2), batch_id='source',
    )

    assert [item.name for item in result.aspect_updates] == ['I am a Belshazar-crafted vessel']
    assert result.aspect_updates[0].importance == 5


def test_generated_aspect_name_must_be_a_first_person_statement():
    common = {
        'category': 'identity', 'description': 'A durable fact.', 'importance': 5,
        'justification': 'The scene confirms it.', 'confidence': .95,
    }
    assert _AspectSignalLLMOutput(name='I am a vessel', **common).name == 'I am a vessel'
    with pytest.raises(ValueError, match="first-person"):
        _AspectSignalLLMOutput(name='Hoffman-crafted vessel', **common)


@pytest.mark.asyncio
async def test_conclusive_goal_signal_completes_the_matching_active_goal():
    class GoalCompletionLLM(BatchLLM):
        async def chat(self, **kwargs):
            raw = await super().chat(**kwargs)
            if kwargs['usage_tag'].endswith('.scene_interpretation'):
                payload = json.loads(raw)
                payload['scene_enrichments'][0]['goal_signals'] = [{
                    'operation': 'complete', 'title': "Assess Anna Tchaikovsky's condition",
                    'description': 'Anna has been assessed and the condition resolved.',
                    'goal_type': 'obligation', 'priority': 80, 'commitment': 70,
                    'basis': 'explicit',
                    'justification': 'The scene explicitly establishes that the assessment is complete.',
                    'confidence': .95,
                }]
                return json.dumps(payload)
            return raw

    result = await _agent(GoalCompletionLLM()).run(
        source_entity_id='source', source_entity_alias='Source',
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[],
        current_goals=[{'id': 'goal-anna', 'title': "Assess Anna Tchaikovsky's condition"}],
        scenes=scenes(1), batch_id='source',
    )

    assert len(result.goal_updates) == 1
    assert result.goal_updates[0].operation == 'complete'
    assert result.goal_updates[0].title == "Assess Anna Tchaikovsky's condition"
    assert result.goal_updates[0].description is None


def test_second_wave_prompts_are_compact_and_have_separate_contracts():
    assert len(PSYCHOLOGICAL_ANALYSIS_PROMPT) < 7_500
    assert 'trait_candidates' not in PSYCHOLOGICAL_ANALYSIS_PROMPT
    assert 'trait_candidates' in TRAIT_INTERPRETATION_PROMPT
    assert 'aspect_signals' in PSYCHOLOGICAL_ANALYSIS_PROMPT
    assert 'Current-profile aspects are impact targets only' in PSYCHOLOGICAL_ANALYSIS_PROMPT
    assert 'operation `complete`' in PSYCHOLOGICAL_ANALYSIS_PROMPT
    assert 'first-person self-statement' in PSYCHOLOGICAL_ANALYSIS_PROMPT
    assert 'mandatory distinct `identity` aspect signal' in PSYCHOLOGICAL_ANALYSIS_PROMPT
    assert 'Authoritative trait definitions and scale' not in PERSPECTIVE_PROMPT


@pytest.mark.asyncio
@pytest.mark.skip(reason="empty cross-scene responses no longer exist")
async def test_empty_observation_response_skips_json_repair_and_becomes_no_change(monkeypatch):
    repair_called = False

    async def repair(**_kwargs):
        nonlocal repair_called
        repair_called = True
        return "{}"

    class EmptyObservationsLLM(BatchLLM):
        async def chat(self, **kwargs):
            if kwargs["usage_tag"].endswith(".observations"):
                self.calls.append(kwargs)
                return ""
            return await super().chat(**kwargs)

    monkeypatch.setattr("app.jobs.character_agent.embody_agent.repair_json_text", repair)
    analysis = await _agent(EmptyObservationsLLM()).analyze(
        source_entity_id="source", source_entity_alias="Source",
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(1),
    )
    assert analysis.observations_unavailable is True
    assert analysis.observations.trait_evidence == []
    assert repair_called is False


@pytest.mark.asyncio
async def test_timeline_persists_evidence_with_single_eligible_numeric_change():
    from app.jobs.character_agent.profile import _build_timeline
    result=await _agent(BatchLLM()).run(source_entity_id='source',source_entity_alias='Source',canonical_identity=_canonical(),
        current_trait_profile=TraitProfile(),current_aspects=[],current_goals=[],scenes=scenes(1),batch_id='b1')
    timeline=CharacterTimelineProjection.model_validate_json(_build_timeline(
        'e', 'Mara', _canonical(), TraitProfile(), [], [], None, [result],
        source_groups=[{'source_id': 'source', 'source_alias': 'Case file', 'scenes': [{
            'scene_id': 's0', 'name': 'The first choice',
            'description': 'A voluntary and informed choice.',
        }]}],
    ))
    projection = timeline.source_projections[0]
    assert projection.source_group.name == 'Case file'
    assert projection.perspectives[0].scene.name == 'The first choice'
    assert projection.perspectives[0].evidence[0].description == 'A voluntary and informed choice.'
    assert projection.trait_changes[0].current.point == 6
    tx=_TimelineTx()
    await CharacterAgentService(None,None)._persist_timeline_tx(tx,{'id':'a','ontology_id':1,'name':'Mara'},timeline,
        '2026-09-21',provider='test',model='test',prompt_version=PROMPT_VERSION)
    revisions=[params['props'] for q,params in tx.calls if 'CREATE (revision:CharacterIdentityRevision)' in q]
    assert len(revisions)==2 and len(json.loads(revisions[1]['trait_evidence']))==1
    assert json.loads(revisions[1]['trait_profile'])['dispositional_traits']['integrity']['point'] == 6
    perspective=next(params for q,params in tx.calls if 'CREATE (perspective:ScenePerspective)' in q)
    assert perspective['props']['id'] == projection.perspectives[0].id
    assert json.loads(revisions[1]['trait_evidence'])[0]['perspective_id'] == perspective['props']['id']
    assert json.loads(revisions[1]['trait_evidence'])[0]['revision_id'] == revisions[1]['id']
    assert perspective['revision_id']==revisions[0]['id']
    assert 'scene' not in perspective['props']
    assert 'evidence' not in perspective['props']
    assert any('SET agent.trait_profile=' in q for q,_ in tx.calls)
