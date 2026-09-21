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
    CharacterAgentCreateRequest, CharacterAgentRead, CharacterAgentUpdate,
    EmbodimentEvidence, EmbodimentProposal,
    ProfileUpdateOutput,
    SceneInput, ScenePerspectiveOutput,
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
    return json.dumps({"trait_proposals": [], "aspect_updates": [], "goal_updates": []})


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


def test_embodiment_checkpoint_key_invalidates_changed_evidence_or_model():
    from app.tasks.character_embodiment import _checkpoint_cache_key

    common = {
        "revision": 2,
        "canonical_identity": _canonical(),
        "trait_profile": TraitProfile().model_dump(mode="json"), "trait_evidence": [],
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
            "trait_profile": TraitProfile().model_dump(mode="json"),
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
        if stage=='baseline':
            return json.dumps({'trait_evidence':[]})
        if stage=='character_incorporation':
            result={'perspectives':[dict(scene_id=s['scene_id'],evidence_ids=['scene:'+s['scene_id']],
                source_type='participated',awareness_level=90,confidence=90,
                summary='Returned an untraceable overpayment.',interpretation='Keeping it would exploit another.',
                character_reflection='REFLECTION MUST NOT BECOME EVIDENCE',memory_strength=80,importance=3)
                for s in payload['scenes']]}
            if self.corruption=='future':
                result['perspectives'][0]['evidence_ids']=['scene:'+payload['scenes'][-1]['scene_id']]
            return json.dumps(result)
        if stage=='scene_interpretation':
            return json.dumps({'scene_enrichments':[dict(scene_id=s['scene_id'],evidence_ids=['scene:'+s['scene_id']],
                emotions=[],beliefs=[],impacts=[]) for s in payload['scenes']]})
        if stage=='observations':
            items=[observation(scene=b['scene']['scene_id']).model_dump() for b in payload['scene_bundles']]
            if self.corruption=='unknown': items[0]['evidence_ids']=['scene:foreign']
            return json.dumps({'trait_evidence':items})
        if stage=='profile_update':
            refs=[e['id'] for e in payload['trait_evidence'] if e['eligible']]
            return json.dumps({'trait_proposals':[dict(trait='integrity',point=8,observation_ids=refs,
                justification='Repeated voluntary choices.',addresses_contradictions='No opposing behavior.')],
                'aspect_updates':[],'goal_updates':[]})
        raise AssertionError(stage)


def scenes(count, offset=0):
    return [SceneInput(scene_id=f's{i}',name='Choice',description='Free, known and safe choice.',created_at=f'{i:03}')
            for i in range(offset,offset+count)]


@pytest.mark.asyncio
async def test_source_batch_is_four_calls_with_grounded_cumulative_update():
    llm=BatchLLM()
    result=await _agent(llm).run(source_entity_id='source',source_entity_alias='Source',canonical_identity=_canonical(),
        current_trait_profile=TraitProfile(),current_aspects=[],current_goals=[],scenes=scenes(10),batch_id='batch1')
    assert len(llm.calls)==4 and result.total_llm_calls==4
    assert len(result.perspectives)==10 and len(result.trait_evidence)==10
    assert result.trait_profile.dispositional_traits['integrity'].point==8
    assert result.trait_profile.steadiness.point is None
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
        assert first['current_profile']['trait_profile']['dispositional_traits']['integrity']['point']==(None if offset==0 else 8)
        profile=result.trait_profile;ledger=merge_evidence(ledger,result.trait_evidence);results.append(result)
    timeline=CharacterTimelineProjection.model_validate_json(_build_timeline('e','Mara',_canonical(),TraitProfile(),[],[],None,results))
    assert [r.revision_number for r in timeline.revisions]==[0,1,2]
    assert [p.starting_revision_number for p in timeline.source_projections]==[0,1]
    assert timeline.revisions[0].trait_profile.dispositional_traits['integrity'].point is None
    assert [len(r.trait_evidence) for r in timeline.revisions]==[0,3,3]
    assert len(ledger)==6


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption',['future','unknown'])
async def test_batch_rejects_future_grounding_and_unknown_trait_evidence(corruption):
    with pytest.raises(EmbodimentGenerationError):
        await _agent(BatchLLM(corruption),semantic_correction_attempts=0).run(
            source_entity_id='s',source_entity_alias='S',canonical_identity=_canonical(),
            current_trait_profile=TraitProfile(),current_aspects=[],current_goals=[],scenes=scenes(3))


@pytest.mark.asyncio
async def test_authored_only_initialization_is_one_call_and_unknown_is_preserved():
    llm=BatchLLM()
    profile,evidence,observations=await _agent(llm).initialize(canonical_identity=_canonical(),entity_id='e')
    assert len(llm.calls)==1 and evidence==[]
    assert all(value.point is None for value in profile.dispositional_traits.values())
    payload=json.loads(llm.calls[0]['messages'][1]['content'])
    assert payload['identity']['authored_text']=='Canonical authored biography.'
    assert 'generated_text' not in payload['identity']


def test_complete_prompt_contracts():
    from app.jobs.character_agent.embody_agent_prompts import BASELINE_PROMPT
    for field in ('conditions','diagnosticity','available_after_scene_id','comparison_context'):
        assert field in OBSERVATIONS_PROMPT
    assert 'trait_proposals' in PROFILE_UPDATE_PROMPT
    assert 'evidence_ids' in PERSPECTIVE_PROMPT and 'evidence_ids' in ENRICHMENT_PROMPT
    assert 'authored_disposition' in BASELINE_PROMPT


@pytest.mark.asyncio
async def test_timeline_persists_evidence_even_without_numeric_change():
    from app.jobs.character_agent.profile import _build_timeline
    result=await _agent(BatchLLM()).run(source_entity_id='source',source_entity_alias='Source',canonical_identity=_canonical(),
        current_trait_profile=TraitProfile(),current_aspects=[],current_goals=[],scenes=scenes(1),batch_id='b1')
    timeline=CharacterTimelineProjection.model_validate_json(_build_timeline('e','Mara',_canonical(),TraitProfile(),[],[],None,[result]))
    tx=_TimelineTx()
    await CharacterAgentService(None,None)._persist_timeline_tx(tx,{'id':'a','ontology_id':1,'name':'Mara'},timeline,
        '2026-09-21',provider='test',model='test',prompt_version=PROMPT_VERSION)
    revisions=[params['props'] for q,params in tx.calls if 'CREATE (revision:CharacterIdentityRevision)' in q]
    assert len(revisions)==2 and len(json.loads(revisions[1]['trait_evidence']))==1
    assert json.loads(revisions[1]['trait_profile'])['dispositional_traits']['integrity']['z'] is None
    perspective=next(params for q,params in tx.calls if 'CREATE (perspective:ScenePerspective)' in q)
    assert perspective['revision_id']==revisions[0]['id']
    assert any('SET agent.trait_profile=' in q for q,_ in tx.calls)


@pytest.mark.asyncio
async def test_all_four_stage_checkpoints_resume_without_provider_calls():
    checkpoints = {}
    async def save(stage, payload):
        checkpoints[stage] = payload.model_dump(mode="json")
    kwargs = dict(source_entity_id='source', source_entity_alias='Source',
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(3))
    job = _agent(BatchLLM())
    analysis = await job.analyze(**kwargs, on_checkpoint=save)
    profile_checkpoints = []
    async def save_profile(stage, payload):
        profile_checkpoints.append(payload.model_dump(mode="json"))
    expected = await job.apply_profile_update(analysis=analysis,
        current_trait_profile=TraitProfile(), current_aspects=[], current_goals=[],
        on_checkpoint=save_profile)
    llm = BatchLLM()
    resumed = _agent(llm)
    cached_analysis = await resumed.analyze(**kwargs, stage_checkpoints=checkpoints)
    actual = await resumed.apply_profile_update(analysis=cached_analysis,
        current_trait_profile=TraitProfile(), current_aspects=[], current_goals=[],
        stage_checkpoint=profile_checkpoints[0])
    assert llm.calls == []
    assert actual.trait_profile == expected.trait_profile
    assert actual.trait_evidence == expected.trait_evidence
