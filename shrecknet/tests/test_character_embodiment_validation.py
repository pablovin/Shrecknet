"""Failure-boundary regression coverage for the unified embodiment lifecycle."""
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator

from app.jobs.character_agent.embody_agent import (
    EmbodyAgent, EmbodimentGenerationError, _PerspectivesLLMContainer,
    _SceneTraitInterpretationsLLMOutput, _parse_embodiment_json, _model_output_schema,
)
from app.jobs.character_agent.consolidation import ConsolidationEnvelope, prepare_consolidation, ProfileReference
from app.jobs.character_agent.embodiment_debug_artifacts import EmbodimentDebugArtifacts
from app.jobs.character_agent.profile import _apply_goal_ops
from app.schemas.character_agent import ProfileEventOutput, ScenePerspectiveOutput, GoalUpdateData
from app.integrations.llm.shreckllm_client import LLMProviderUnavailableError
from app.integrations.llm.structured_output import structured_output_is_unsupported


class Responses:
    def __init__(self, *responses):
        self.responses = responses
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response


def agent(provider, **kwargs):
    return EmbodyAgent(llm_client=provider, character_incorporation_model="m",
                      scene_interpretation_model="m", **kwargs)


VALID = json.dumps({"perspectives": [{"source_type": "participated", "perspective": "I chose to help."}]})


async def generate(job, **kwargs):
    return await job._call(prompt="contract", payload={"scenes": ["canonical"]},
                           schema=_PerspectivesLLMContainer, stage="test", usage_tag="test",
                           max_tokens=10000, model="m", **kwargs)


@pytest.mark.parametrize("text", [
    '{"outer": {"a": 1}', '{"a":1} {"a":2}', 'explanation {"a":1}',
    '{"a":1} trailing', '{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}',
])
def test_ambiguous_or_malformed_json_is_rejected(text):
    with pytest.raises(ValueError):
        _parse_embodiment_json(text)


def test_complete_fenced_document_is_lossless():
    assert _parse_embodiment_json(' \n```json\n{"a": [1]}\n``` ') == {"a": [1]}


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ['{"perspectives": [', "", '{"perspectives": []}'])
async def test_invalid_output_regenerates_with_original_context(first, tmp_path):
    provider = Responses(first, VALID)
    job = agent(provider, debug_artifacts=EmbodimentDebugArtifacts(tmp_path))
    result = await generate(job, output_binding={"collection": "perspectives", "scene_ids": ["s1"]})
    assert len(result.perspectives) == 1
    assert len(provider.calls) == len(job.llm_calls) == 2
    assert provider.calls[1]["usage_tag"] == "test.validation_retry"
    correction = json.loads(provider.calls[1]["messages"][1]["content"])
    assert correction["original_input"] == {"scenes": ["canonical"]}
    assert correction["validation_errors"]
    log = (tmp_path / "baseline.log").read_text()
    assert '"attempt": 1' in log
    assert '"call_kind": "validation_retry"' in log
    assert '"raw_output"' in log


@pytest.mark.asyncio
async def test_json_then_semantic_error_exhausts_one_shared_budget():
    provider = Responses('{"broken":', VALID, VALID)
    job = agent(provider)

    def reject(_):
        raise ValueError("unknown target")

    with pytest.raises(EmbodimentGenerationError, match="unknown target") as raised:
        await generate(job, semantic_validator=reject)
    assert raised.value.category == "semantic_reference"
    assert raised.value.attempt == 2 and not raised.value.retryable
    assert len(provider.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("retries", [0, 1, 3])
async def test_configured_budget_is_exact(retries):
    provider = Responses("{}")
    with pytest.raises(EmbodimentGenerationError) as raised:
        await generate(agent(provider, validation_retries=retries))
    assert len(provider.calls) == retries + 1
    assert raised.value.category == "schema" and not raised.value.retryable


@pytest.mark.asyncio
@pytest.mark.parametrize("body,category", [("", "empty_response"), (VALID, "truncated")])
async def test_corrected_response_metadata_is_checked(body, category):
    provider = Responses("{}", {"text": body, "response_metadata": {"finish_reason": "length" if body else "stop"}})
    with pytest.raises(EmbodimentGenerationError) as raised:
        await generate(agent(provider))
    assert raised.value.category == category
    assert raised.value.attempt == 2 and not raised.value.retryable
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_length_stopped_empty_response_does_not_start_content_retry():
    provider = Responses({"text": "", "response_metadata": {"finish_reason": "length"}})
    with pytest.raises(EmbodimentGenerationError) as raised:
        await generate(agent(provider))
    assert raised.value.category == "truncated"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,category", [
    (TimeoutError("watchdog exceeded"), "provider_timeout"),
    (RuntimeError("unauthorized"), "transport"),
    (LLMProviderUnavailableError(provider_id="p", reason="offline"), "provider_unavailable"),
])
@pytest.mark.parametrize("on_retry", [False, True])
async def test_provider_errors_are_categorized_without_content_retry(failure, category, on_retry):
    provider = Responses(*(["{}", failure] if on_retry else [failure]))
    with pytest.raises(EmbodimentGenerationError) as raised:
        await generate(agent(provider))
    assert raised.value.category == category
    assert len(provider.calls) == (2 if on_retry else 1)


@pytest.mark.asyncio
async def test_explicit_native_fallback_then_validation_retry():
    provider = Responses(RuntimeError("provider does not support response_format"), "{}", VALID)
    job = agent(provider)
    await generate(job)
    assert len(provider.calls) == 3
    assert "response_format" not in provider.calls[1]
    assert provider.calls[1]["usage_tag"].endswith("structured_fallback")
    assert provider.calls[2]["usage_tag"].endswith("validation_retry")
    assert len(job.llm_calls) == 2  # Both returned responses, not the rejected capability request.


@pytest.mark.parametrize("message", ["unsupported model", "unsupported model for json_schema", "json_schema requested; model not supported", "response_format request timed out", "invalid json_schema", "unauthorized"])
def test_unrelated_provider_errors_never_trigger_fallback(message):
    assert not structured_output_is_unsupported(RuntimeError(message))


@pytest.mark.parametrize("message", [
    "anthropic does not support response_format", "Unsupported parameter: 'response_format'",
    "response_format is unsupported", "Invalid parameter: 'response_format' of type 'json_schema' is not supported with this model.",
    "unexpected keyword argument 'response_format'",
])
def test_explicit_format_incompatibility_triggers_fallback(message):
    assert structured_output_is_unsupported(RuntimeError(message))


@pytest.mark.asyncio
async def test_duplicate_traits_are_corrected_inside_validation():
    candidate = {"trait": "integrity", "situation_type": "unspecified", "polarity": "high", "justification": "A voluntary fair choice."}
    invalid = {"scene_trait_interpretations": [{"trait_candidates": [candidate, candidate]}]}
    valid = {"scene_trait_interpretations": [{"trait_candidates": [candidate]}]}
    provider = Responses(json.dumps(invalid), json.dumps(valid))
    result = await agent(provider)._call(prompt="contract", payload={}, schema=_SceneTraitInterpretationsLLMOutput,
        stage="traits", usage_tag="traits", max_tokens=10000, model="m",
        output_binding={"collection": "scene_trait_interpretations", "scene_ids": ["s1"]})
    assert len(result.scene_trait_interpretations[0].trait_candidates) == 1
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_truncation_splits_have_finite_units_and_keep_order():
    class SplitProvider:
        def __init__(self):
            self.calls = []

        async def chat(self, **kwargs):
            self.calls.append(kwargs)
            scenes = json.loads(kwargs["messages"][1]["content"])["scenes"]
            body = {"scene_enrichments": [{"emotions": [], "beliefs": [], "profile_events": []} for _ in scenes]}
            return {"text": json.dumps(body), "response_metadata": {"finish_reason": "length" if len(scenes) > 1 else "stop"}}

    provider = SplitProvider()
    perspectives = [ScenePerspectiveOutput(scene_id=f"s{i}", source_type="participated", perspective="I helped.", evidence_ids=[f"scene:s{i}"]) for i in range(5)]
    result = await agent(provider)._analyze_psychological_batch(source_entity_id="source", source_entity_alias="Source",
        perspectives=perspectives, aspects=[], goals=[], scene_contexts=[{"position": i + 1} for i in range(5)])
    assert [item.scene_id for item in result] == [item.scene_id for item in perspectives]
    assert len(provider.calls) == 2 * 5 - 1


def response(operations=None, focus=None):
    return {"consolidation": {"aspect_operations": [], "goal_operations": operations or [], "focused_aspects": [], "focused_goals": focus or []}}


ADD = {"operation": "add", "title": "Find my maker", "description": None, "goal_type": "objective", "in_focus": False, "justification": "A clear commitment.", "event_references": ["event-001"]}
NEW = {"scope": "new", "index": 1}
EVENTS = [ProfileEventOutput(kind="goal", description="A clear commitment.", scene_id="s1")]


@pytest.mark.parametrize("index", [0, -1, True, "1", 1.5])
def test_reference_indexes_are_strict(index):
    with pytest.raises(ValueError):
        ProfileReference(scope="new", index=index)


@pytest.mark.parametrize("operations,focus,match", [
    ([ADD, ADD], [], "duplicate"),
    ([{"operation": "status", "target": NEW, "status": "completed", "justification": "Done", "event_references": ["event-001"]}, ADD], [], "unavailable"),
    ([ADD], [NEW, NEW], "scope"),
    ([ADD], [{"scope": "existing", "index": 1}], "unavailable"),
    ([{**ADD, "event_references": ["event-999"]}], [], "unknown event"),
])
def test_binding_rejects_invalid_decisions_without_mutation(operations, focus, match):
    aspects, goals = [], []
    with pytest.raises(ValueError, match=match):
        prepare_consolidation(ConsolidationEnvelope.model_validate(response(operations, focus)), events=EVENTS, aspects=aspects, goals=goals)
    assert aspects == goals == []


def test_stable_id_collision_with_existing_is_rejected():
    goals = [{"id": "goal:find-my-maker", "title": "Find my maker!", "status": "active"}]
    original = copy.deepcopy(goals)
    with pytest.raises(ValueError, match="duplicate"):
        prepare_consolidation(ConsolidationEnvelope.model_validate(response([ADD])), events=EVENTS, aspects=[], goals=goals)
    assert goals == original


def test_non_ascii_slug_collision_is_rejected_without_inventing_suffixes():
    additions = [{**ADD, "title": "中"}, {**ADD, "title": "漢"}]
    with pytest.raises(ValueError, match="duplicate"):
        prepare_consolidation(ConsolidationEnvelope.model_validate(response(additions)), events=EVENTS, aspects=[], goals=[])


@pytest.mark.asyncio
async def test_consolidation_exposes_only_local_references_and_corrects_invalid_focus():
    from test_character_embodiment import _analysis
    status = {"operation": "status", "target": {"scope": "existing", "index": 1},
              "status": "completed", "justification": "The maker was found.", "event_references": ["event-001"]}
    invalid = response([status], [{"scope": "existing", "index": 1}])
    provider = Responses(json.dumps(invalid), json.dumps(response([status])))
    current = [{"id": "private-canonical-goal-id", "title": "Find my maker", "status": "active", "in_focus": True}]
    result = await agent(provider)._consolidate_profile(analysis=_analysis(EVENTS), current_aspects=[], current_goals=current)
    initial_payload = provider.calls[0]["messages"][1]["content"]
    assert "private-canonical-goal-id" not in initial_payload
    assert '"scene_id"' not in initial_payload
    assert result["goal_updates"][0].target_id == "private-canonical-goal-id"
    assert result["focused_goals"] == []
    assert current[0]["status"] == "active" and current[0]["in_focus"]
    assert len(provider.calls) == 2


def test_new_goal_rename_resolution_and_reactivation_use_same_reducers():
    operations = [{**ADD, "in_focus": True},
        {"operation": "update", "target": NEW, "title": "Meet my maker", "description": None, "goal_type": None, "justification": "A refined commitment.", "event_references": ["event-001"]},
        {"operation": "status", "target": NEW, "status": "completed", "justification": "Done", "event_references": ["event-001"]},
        {"operation": "status", "target": NEW, "status": "active", "justification": "Explicitly renewed", "event_references": ["event-001"]}]
    prepared = prepare_consolidation(ConsolidationEnvelope.model_validate(response(operations, [])), events=EVENTS, aspects=[], goals=[])
    goals = []
    _apply_goal_ops(goals, prepared["goal_updates"], focused_ids=prepared["focused_goals"])
    assert goals[0]["id"] == "goal:find-my-maker"
    assert goals[0]["title"] == "Meet my maker"
    assert goals[0]["status"] == "active" and goals[0]["in_focus"]
    assert prepared["goal_updates"][2].title == "Meet my maker"
    # Existing stored update objects keep their original public shapes.
    assert GoalUpdateData.model_validate_json(prepared["goal_updates"][0].model_dump_json()).candidate_id == "goal:find-my-maker"


def test_focus_eligibility_and_atomicity_belong_to_reducer():
    goals = [{"id": "g1", "title": "Find", "status": "active", "in_focus": True}]
    original = copy.deepcopy(goals)
    update = GoalUpdateData(operation="status", target_id="g1", title="Find", status="completed", justification="Done", evidence_ids=["scene:s1"])
    with pytest.raises(ValueError, match="cannot be focused"):
        _apply_goal_ops(goals, [update], focused_ids=["g1"])
    assert goals == original


def test_provider_schema_matches_runtime_typed_contract():
    schema = _model_output_schema(ConsolidationEnvelope, None)
    validator = Draft202012Validator(schema)
    validator.check_schema(schema)
    valid = response([ADD], [])
    validator.validate(valid)
    assert ConsolidationEnvelope.model_validate(valid)
    invalid_focus = response([ADD], [NEW])
    assert list(validator.iter_errors(invalid_focus))
    invalid = response([{**ADD, "status": "completed"}])
    assert list(validator.iter_errors(invalid))
    with pytest.raises(ValueError):
        ConsolidationEnvelope.model_validate(invalid)


@pytest.mark.parametrize("operation", ["update", "reinforce", "status"])
@pytest.mark.parametrize("kind", ["aspect", "goal"])
def test_all_existing_operation_variants_use_kind_specific_contracts(kind, operation):
    current = {"id": "existing", "status": "active", "in_focus": False}
    current["name" if kind == "aspect" else "title"] = "I guard the archive" if kind == "aspect" else "Guard the archive"
    item = {"operation": operation, "target": {"scope": "existing", "index": 1},
            "justification": "A meaningful development.", "event_references": ["event-001"]}
    if operation == "status":
        item["status"] = "inactive" if kind == "aspect" else "abandoned"
    else:
        item.update({("name" if kind == "aspect" else "title"): None,
                     "description": "A refined duty." if operation == "update" else None,
                     ("category" if kind == "aspect" else "goal_type"): None})
    raw = response()
    raw["consolidation"][f"{kind}_operations"] = [item]
    prepared = prepare_consolidation(ConsolidationEnvelope.model_validate(raw),
        events=[ProfileEventOutput(kind=kind, description="An event.", scene_id="s1")],
        aspects=[current] if kind == "aspect" else [], goals=[current] if kind == "goal" else [])
    assert prepared[f"{kind}_updates"][0].target_id == "existing"
    assert current["status"] == "active"  # Acceptance never publishes its working copy.
    invalid = copy.deepcopy(raw)
    if operation == "status":
        invalid["consolidation"][f"{kind}_operations"][0]["status"] = "completed" if kind == "aspect" else "inactive"
    else:
        invalid["consolidation"][f"{kind}_operations"][0]["status"] = "active"
    with pytest.raises(ValueError):
        ConsolidationEnvelope.model_validate(invalid)


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_source", [False, True])
async def test_append_failure_never_writes_failed_source_and_preserves_prior_commits(monkeypatch, prior_source):
    from app.core.config_store import Settings
    from app.schemas.character_traits import TraitProfile
    from app.services.character_embodiment_service import CharacterEmbodimentService
    from app.services.character_agent_service import CharacterAgentService
    from test_character_embodiment import BatchLLM, _canonical, scenes

    class Rows:
        def __aiter__(self):
            async def rows():
                for row in []:
                    yield row
            return rows()

    graph = SimpleNamespace(run=AsyncMock(return_value=Rows()), execute_write=AsyncMock())
    service = CharacterEmbodimentService(None, graph)
    count = 2 if prior_source else 1
    scene_values = [scene.model_dump() for scene in scenes(count)]
    identity = {"identity_summary": "Mara is a person.", "psychological_summary": "Mara values fairness.", "personality_traits": []}
    inputs = {
        "agent_id": "a", "processed_scene_ids": set(), "scenes": scene_values,
        "source_groups": [{"source_id": f"source-{i}", "source_alias": f"Source {i}", "scenes": [scene]} for i, scene in enumerate(scene_values)],
        "trait_profile": TraitProfile(), "trait_evidence": [], "current_aspects": [], "current_goals": [],
        "latest_revision": 0, "canonical_identity": _canonical(), "identity_description": identity,
        "source_entity_alias": "Mara",
    }
    service.load_embodiment_input = AsyncMock(return_value=inputs)
    monkeypatch.setattr(CharacterAgentService, "get_agent", AsyncMock(return_value=SimpleNamespace(model_dump=lambda **kwargs: {"id": "a"})))

    class Provider(BatchLLM):
        consolidations = 0

        async def chat(self, **kwargs):
            tag = kwargs["usage_tag"]
            if "psychological_consolidation" in tag:
                self.calls.append(kwargs)
                self.consolidations += 1
                if prior_source and self.consolidations == 1:
                    return json.dumps(response([ADD]))
                return json.dumps(response([{**ADD, "event_references": ["event-999"]}]))
            if tag.endswith("scene_interpretation"):
                self.calls.append(kwargs)
                return json.dumps({"scene_enrichments": [{"emotions": [], "beliefs": [], "profile_events": [{"kind": "goal", "description": "Vows to find their maker."}]}]})
            return await super().chat(**kwargs)

    with pytest.raises(EmbodimentGenerationError, match="unknown event"):
        await service.append_created_scenes(agent_id="a", entity_id="e", ontology_id=1,
            created_scene_ids={scene["scene_id"] for scene in scene_values}, llm_client=Provider(), settings=Settings())
    assert graph.execute_write.await_count == int(prior_source)
    assert len(inputs["current_goals"]) == int(prior_source)
    assert len(inputs["current_aspects"]) == 0


def test_celery_failure_preserves_validation_diagnostics(monkeypatch):
    from app.tasks import character_embodiment as task
    failure = EmbodimentGenerationError("unknown event", category="semantic_reference", stage="psychological consolidation", attempt=2)
    mark_failed, fail_draft = AsyncMock(), AsyncMock()
    monkeypatch.setattr(task, "_generate", AsyncMock(side_effect=failure))
    monkeypatch.setattr(task, "_fail", fail_draft)
    monkeypatch.setattr(task, "mark_job_running", AsyncMock())
    monkeypatch.setattr(task, "mark_job_failed", mark_failed)
    mark_done = AsyncMock()
    monkeypatch.setattr(task, "mark_job_done", mark_done)
    with pytest.raises(EmbodimentGenerationError):
        task.generate_character_embodiment.run(draft_id="draft", revision=1, job_id=9)
    fail_draft.assert_awaited_once()
    mark_done.assert_not_awaited()
    details = mark_failed.await_args.args[2]
    assert details["failure_category"] == "semantic_reference"
    assert details["attempt"] == 2 and details["retryable"] is False


@pytest.mark.asyncio
async def test_single_agent_analysis_calls_are_not_counted_twice():
    from app.schemas.character_traits import TraitProfile
    from test_character_embodiment import BatchLLM, _canonical, scenes
    provider = BatchLLM()
    job = agent(provider)
    result = await job.run(source_entity_id="source", source_entity_alias="Source",
        canonical_identity=_canonical(), current_trait_profile=TraitProfile(),
        current_aspects=[], current_goals=[], scenes=scenes(1))
    assert len(provider.calls) == len(result.llm_calls) == result.total_llm_calls == 3


def test_scene_checkpoint_requires_matching_fingerprint_and_valid_analysis():
    from app.tasks.character_embodiment import _checkpoint_analysis
    from test_character_embodiment import _analysis
    analysis = _analysis([]).model_dump(mode="json")
    checkpoint = {"version": 1, "key": "same", "analysis": analysis}
    assert _checkpoint_analysis(checkpoint, checkpoint_key="same") is not None
    assert _checkpoint_analysis(checkpoint, checkpoint_key="changed") is None
    assert _checkpoint_analysis({**checkpoint, "analysis": {"bad": True}}, checkpoint_key="same") is None
