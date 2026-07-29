from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.jobs.elder.context_budget import partition_complete_records, serialize_evidence
from app.jobs.elder.evidence import (
    assemble_budgeted_evidence,
    assemble_evidence,
    json_safe,
    select_synthesis_evidence,
)
from app.jobs.elder.executor import ElderRetrievalExecutor
from app.jobs.elder.grounding import build_grounding_package
from app.jobs.elder.planner import create_retrieval_plan, fallback_plan, validate_bounded_cypher
from app.jobs.elder.query_v2 import ElderQueryV2
from app.jobs.elder.schemas import ElderQueryRequest, RetrievedChunk
from app.jobs.elder.v2_schemas import (
    EVIDENCE_TARGET_TOKENS,
    EvidenceCapacityError,
    EvidenceRecord,
    RetrievalPlan,
)


class ModelPolicy:
    model_elder_planner = "elder-planner-test"
    model_elder_synthesis = "elder-synthesis-test"
    model_agents_repair_json = "elder-test"
    model_elder_character_incorporation = None

    def get_model(self, _task):
        return "elder-test"


class LLM:
    def __init__(self):
        self.messages = []
        self.events = []

    async def chat(self, *, messages, usage_tag=None, model=None, **_kwargs):
        self.messages.append(messages[-1]["content"])
        self.events.append({
            "model": str(model), "prompt_tokens": 100 * len(self.messages),
            "completion_tokens": 10, "total_tokens": 100 * len(self.messages) + 10,
            "usage_tag": usage_tag,
            "wait_ms": 1250.5,
        })
        if str(usage_tag or "").endswith(".plan"):
            return (
                '{"answer_goal":"answer completely","target_language":"en","steps":['
                '{"id":"find","operation":"hybrid_search","query":"long entity",'
                '"target_data_type":"entity","limit":5,'
                '"evidence_type":"standard_summary"}]}'
            )
        return (
            '{"claims":[{"id":"claim-1","text":"Grounded answer",'
            '"citations":["evidence-1"]}],"uncertainty":null}'
        )

    def get_usage_event_count(self):
        return len(self.events)

    def get_usage_events_since(self, start_index):
        return self.events[start_index:]


class Retriever:
    async def instance_summaries(self, _ontology_ids):
        return [{"name": "World", "hint": "Active"}]

    async def list_entities_by_ontology(self, _ontology_id, *, skip=0, limit=500):
        return []

    async def search(self, **_kwargs):
        return [
            RetrievedChunk(
                node_id="entity-1", node_label="EntityInstance", node_name="Entity",
                instance_id="world-1", chunk_id="chunk-1", chunk_type="description",
                chunk_index=0, text="semantic match", score=0.9, confidence_pct=90,
            )
        ]

    async def hydrate_evidence_nodes(self, _node_ids, **_kwargs):
        complete = "BEGIN " + ("complete evidence " * 2000) + " END"
        return {
            "entity-1": {
                "source_kind": "EntityInstance", "display_name": "Entity",
                "display_text": complete, "properties": {"description": complete},
                "chunks": [{"chunk_id": "all", "chunk_type": "canonical", "chunk_index": 0,
                            "text": complete}],
            }
        }


@pytest.mark.asyncio
async def test_normal_v2_path_is_two_calls_and_sends_complete_evidence(capsys):
    llm = LLM()
    orchestrator = ElderQueryV2(llm, ModelPolicy(), Retriever())
    agent = SimpleNamespace(
        id="elder-1", name="Elder", writing_style="careful", ontologies=[SimpleNamespace(id=7)]
    )
    response = await orchestrator.execute(
        agent, ElderQueryRequest(query="Summarize the whole entity", instance_id="world-1")
    )
    assert len(llm.messages) == 2
    assert "BEGIN " in llm.messages[1] and " END" in llm.messages[1]
    assert response.sources[0].evidence_chunks[0].text.endswith(" END")
    assert response.answer == "Grounded answer ¹"
    assert response.sources[0].evidence_id == "evidence-1"
    assert response.pipeline_version == "elder-query-retrieval-v3"
    assert "intents" not in response.model_dump()
    assert [row["stage"] for row in response.llm_usage] == ["plan", "synthesize"]
    assert response.llm_usage_totals == {
        "calls": 2, "input_tokens": 300, "output_tokens": 20, "total_tokens": 320,
    }
    console = capsys.readouterr().out
    assert "[ELDER_LLM_USAGE]" in console
    assert "stage=plan" in console
    assert "stage=synthesize" in console
    assert "wait_ms=1250.50" in console
    assert "[ELDER_LLM_USAGE_TOTAL]" in console
    assert "total_tokens=320" in console


@pytest.mark.asyncio
async def test_executor_uses_dependency_waves():
    plan = RetrievalPlan.model_validate({
        "answer_goal": "timeline", "steps": [
            {"id": "a", "operation": "hybrid_search", "query": "a"},
            {"id": "b", "operation": "hybrid_search", "query": "b"},
                {"id": "c", "operation": "hydrate_sources", "depends_on": ["a", "b"],
                 "evidence_type": "standard_summary"},
        ]
    })
    results, waves, _debug = await ElderRetrievalExecutor(Retriever()).execute(
        plan=plan, ontology_ids=[7], instance_id="world-1", candidate_limit=20, rerank_limit=10
    )
    assert waves == [["a", "b"], ["c"]]
    assert results["c"]


@pytest.mark.asyncio
async def test_executor_selects_latest_story_and_traverses_derived_context():
    calls = []

    class StructuralRetriever(Retriever):
        async def select_nodes(self, **kwargs):
            calls.append(("select", kwargs))
            return [RetrievedChunk(
                node_id="story-latest", node_label="EntityInstance", node_name="Latest story",
                instance_id="world-1", text="Latest story", score=1.0, confidence_pct=100,
            )]

        async def traverse_graph(self, **kwargs):
            calls.append(("traverse", kwargs))
            return [RetrievedChunk(
                node_id="scene-1", node_label="Scene", node_name="Opening",
                instance_id="world-1", text="The investigators arrived.",
                score=0.9, confidence_pct=90,
            )]

    plan = RetrievalPlan.model_validate({
        "answer_goal": "summarize latest session",
        "steps": [
            {"id": "concept", "operation": "resolve_concept", "query": "Stories",
             "target_data_type": "ontology_definition"},
            {"id": "latest", "operation": "select_nodes", "target_data_type": "entity",
             "filters": {"source_kinds": ["Stories"]},
             "temporal": {"mode": "latest"}, "limit": 1},
            {"id": "details", "operation": "traverse_graph", "inputs": ["latest"],
             "traversal": {"relationships": ["DERIVED_FROM", "CONTAINS"], "depth": 1},
             "evidence_type": "standard_summary"},
        ],
    })
    definitions = [{
        "definition_id": 3, "name": "Stories",
        "properties": [{"property_id": 2, "name": "Session Date", "data_type": "date"}],
    }]
    results, _waves, debug = await ElderRetrievalExecutor(StructuralRetriever()).execute(
        plan=plan, ontology_ids=[1], instance_id="world-1", candidate_limit=20,
        rerank_limit=10, definitions=definitions,
    )

    assert results["concept"] == []
    assert [chunk.node_id for chunk in results["details"]] == ["story-latest", "scene-1"]
    assert calls[0][1]["entity_definition_ids"] == [3]
    assert calls[0][1]["temporal_property_ids"] == [2]
    assert calls[0][1]["temporal_mode"] == "latest"
    assert calls[1][1]["anchors"][0].node_id == "story-latest"
    assert all(row["status"] == "success" for row in debug)


@pytest.mark.asyncio
async def test_executor_passes_planner_recency_controls_and_limit_to_timeline_expansion():
    calls = []

    class TemporalRetriever(Retriever):
        async def search_aliases(self, *_args, **_kwargs):
            return [RetrievedChunk(
                node_id="ernst", node_label="EntityInstance", node_name="Ernst",
                instance_id="world-1", text="Ernst", score=1.0, confidence_pct=100,
            )]

        async def expand_timeline_context(self, **kwargs):
            calls.append(kwargs)
            return []

    plan = RetrievalPlan.model_validate({
        "answer_goal": "Explain what happened to Ernst lately",
        "steps": [
            {"id": "ernst", "operation": "resolve_entity", "query": "Ernst"},
            {
                "id": "recent",
                "operation": "expand_temporal_context",
                "inputs": ["ernst"],
                "temporal": {
                    "mode": "latest",
                    "ordering": "recency",
                    "direction": "descending",
                },
                "limit": 13,
                "evidence_type": "timeline_or_history",
            },
        ],
    })

    await ElderRetrievalExecutor(TemporalRetriever()).execute(
        plan=plan,
        ontology_ids=[1],
        instance_id="world-1",
        candidate_limit=20,
        rerank_limit=10,
    )

    assert calls == [{
        "query": "Ernst",
        "ontology_ids": [1],
        "entity_scores": {"ernst": 1.0},
        "max_scenes": 13,
        "max_milestones": 13,
        "max_total": 13,
        "temporal_mode": "latest",
        "temporal_ordering": "recency",
        "temporal_direction": "descending",
    }]


@pytest.mark.asyncio
async def test_evidence_preserves_temporal_rank_and_metadata():
    class Hydrator:
        def __init__(self):
            self.kwargs = None

        async def hydrate_evidence_nodes(self, node_ids, **_kwargs):
            self.kwargs = _kwargs
            return {
                node_id: {
                    "source_kind": "Scene",
                    "display_name": node_id,
                    "display_text": node_id,
                }
                for node_id in node_ids
            }

    plan = RetrievalPlan.model_validate({
        "answer_goal": "Recent events",
        "steps": [{
            "id": "recent",
            "operation": "expand_temporal_context",
            "temporal": {
                "mode": "latest",
                "ordering": "recency",
                "direction": "descending",
            },
                "limit": 2,
                "evidence_type": "timeline_or_history",
        }],
    })
    step_results = {
        "recent": [
            RetrievedChunk(
                node_id="new", node_label="Scene", node_name="New", text="new",
                score=0.5, confidence_pct=50,
                properties={
                    "created_at": "2026-01-02T00:00:00Z",
                    "updated_at": "2026-01-03T00:00:00Z",
                    "_elder_order_rank": 0,
                    "temporal_position": {"rank": 0, "comparable": True},
                },
            ),
            RetrievedChunk(
                node_id="old", node_label="Scene", node_name="Old", text="old",
                score=0.99, confidence_pct=99,
                properties={
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "_elder_order_rank": 1,
                    "temporal_position": {"rank": 1, "comparable": True},
                },
            ),
        ],
    }

    hydrator = Hydrator()
    evidence, _sources = await assemble_evidence(
        retriever=hydrator,
        plan=plan,
        step_results=step_results,
        ontology_ids=[1],
        instance_id=None,
    )

    assert [record.node_id for record in evidence] == ["new", "old"]
    assert evidence[0].temporal_position == {"rank": 0, "comparable": True}
    assert "_elder_order_rank" not in evidence[0].properties
    assert hydrator.kwargs["hydration_mode"] == "complete_source"
    assert "max_tokens_per_source" not in hydrator.kwargs


@pytest.mark.asyncio
async def test_failed_structural_operation_skips_dependants_without_semantic_fallback():
    plan = RetrievalPlan.model_validate({
        "answer_goal": "latest", "steps": [
            {"id": "latest", "operation": "select_nodes", "target_data_type": "entity"},
            {"id": "details", "operation": "traverse_graph", "inputs": ["latest"],
             "evidence_type": "brief_fact"},
        ],
    })
    _results, _waves, debug = await ElderRetrievalExecutor(Retriever()).execute(
        plan=plan, ontology_ids=[1], instance_id=None, candidate_limit=20, rerank_limit=10,
    )
    assert debug[0]["status"] == "failed"
    assert debug[0]["operation"] == "select_nodes"
    assert debug[1]["status"] == "skipped_dependency_failed"


def test_unsafe_or_unscoped_cypher_is_rejected():
    plan = RetrievalPlan.model_validate({
        "answer_goal": "x", "steps": [{
            "id": "x", "operation": "bounded_read_cypher",
            "cypher": "MATCH (n) DELETE n RETURN n LIMIT 5",
            "evidence_type": "brief_fact",
        }]
    })
    with pytest.raises(ValueError, match="unsafe"):
        validate_bounded_cypher(plan, [7])


def test_budget_never_splits_one_evidence_record():
    record = EvidenceRecord(
        evidence_id="huge", node_id="n", display_text="x" * 10_000
    )
    with pytest.raises(EvidenceCapacityError):
        partition_complete_records(
            [record], fixed_prompt="prompt", context_tokens=1000, reserved_tokens=100
        )


def test_evidence_serialization_supports_database_temporal_values():
    class Neo4jDateTimeLike:
        def __str__(self):
            return "2026-07-18T12:34:56Z"

    record = EvidenceRecord(
        evidence_id="dated",
        node_id="node-1",
        display_text="Complete evidence",
        properties={
            "python_datetime": datetime(2026, 7, 18, tzinfo=timezone.utc),
            "neo4j_datetime": Neo4jDateTimeLike(),
        },
    )
    serialized = serialize_evidence(record)
    assert "2026-07-18" in serialized
    assert "2026-07-18T12:34:56Z" in serialized
    safe = json_safe(record.properties)
    assert safe["python_datetime"].startswith("2026-07-18")
    assert safe["neo4j_datetime"] == "2026-07-18T12:34:56Z"


def test_synthesis_projection_keeps_domain_properties_but_removes_internal_fields():
    internal_uuid = "ca2dd52f-4990-42e2-9e6e-4d179dbd0cb2"
    record = EvidenceRecord(
        evidence_id=f"evidence-4:{internal_uuid}", node_id=internal_uuid,
        source_kind="entity_text_chunk", display_name="Story",
        display_text='<a href="/content/private" data-ontology-instance="secret">Ernst</a> acts.',
        properties={"date": "2026-03-10", "text_linked": "duplicate", "avatar_url": "private"},
        provenance={"links": [{"entity_id": internal_uuid, "entity_name": "Johnny"}]},
        score=0.99, retrieval_methods=["vector"],
    )
    compact, stats = select_synthesis_evidence([record], evidence_budget_tokens=10_000)
    payload = compact[0].model_dump_json()
    assert compact[0].evidence_id == "evidence-4"
    assert compact[0].text == "Ernst acts."
    assert compact[0].related_entities == ["Johnny"]
    assert compact[0].canonical_facts == {
        "date": "2026-03-10",
        "text_linked": "duplicate",
    }
    assert stats["included_sources"] == 1
    for forbidden in (internal_uuid, "href", "avatar_url", "score"):
        assert forbidden not in payload


def test_synthesis_budget_includes_the_crossing_source_then_stops():
    records = [
        EvidenceRecord(
            evidence_id=f"evidence-{index}",
            node_id=f"node-{index}",
            display_text=("source text " * 100),
        )
        for index in range(1, 5)
    ]
    _one, one_stats = select_synthesis_evidence(
        records[:1], evidence_budget_tokens=1_000_000
    )
    selected, stats = select_synthesis_evidence(
        records,
        evidence_budget_tokens=one_stats["evidence_tokens"],
    )
    assert len(selected) == 2
    assert stats["crossing_source_included"] is True
    assert stats["omitted_sources"] == 2


def test_evidence_types_have_fixed_server_owned_targets():
    assert EVIDENCE_TARGET_TOKENS == {
        "brief_fact": 12_000,
        "relationship_or_local_event": 20_000,
        "standard_summary": 35_000,
        "timeline_or_history": 60_000,
        "deep_comparison_or_mixed": 100_000,
        "exhaustive": 100_000,
    }


def test_terminal_evidence_type_is_required_and_intermediate_type_is_rejected():
    with pytest.raises(ValueError, match="requires evidence_type"):
        RetrievalPlan.model_validate({
            "answer_goal": "fact",
            "steps": [{"id": "fact", "operation": "hybrid_search"}],
        })
    with pytest.raises(ValueError, match="cannot set evidence_type"):
        RetrievalPlan.model_validate({
            "answer_goal": "fact",
            "steps": [
                {"id": "resolve", "operation": "resolve_entity",
                 "evidence_type": "brief_fact"},
                {"id": "fact", "operation": "hybrid_search", "inputs": ["resolve"],
                 "evidence_type": "brief_fact"},
            ],
        })


@pytest.mark.asyncio
async def test_terminal_step_budgets_merge_and_globally_deduplicate_sources():
    class Hydrator:
        async def hydrate_evidence_nodes(self, node_ids, **_kwargs):
            return {
                node_id: {
                    "source_kind": "Scene",
                    "display_name": node_id,
                    "display_text": f"Canonical {node_id}",
                }
                for node_id in node_ids
            }

    plan = RetrievalPlan.model_validate({
        "answer_goal": "Compare local events",
        "steps": [
            {"id": "first", "operation": "hybrid_search",
             "evidence_type": "brief_fact"},
            {"id": "second", "operation": "hybrid_search",
             "evidence_type": "relationship_or_local_event"},
        ],
    })
    shared = RetrievedChunk(
        node_id="shared", node_label="Scene", node_name="Shared",
        text="shared", score=0.8, confidence_pct=80,
    )
    stronger_shared = shared.model_copy(update={"score": 0.95})
    unique = RetrievedChunk(
        node_id="unique", node_label="Scene", node_name="Unique",
        text="unique", score=0.7, confidence_pct=70,
    )

    records, sources, synthesis, stats = await assemble_budgeted_evidence(
        retriever=Hydrator(),
        plan=plan,
        step_results={"first": [shared], "second": [stronger_shared, unique]},
        ontology_ids=[1],
        instance_id=None,
    )

    assert [record.node_id for record in records] == ["shared", "unique"]
    assert records[0].score == 0.95
    assert [source.evidence_id for source in sources] == ["evidence-1", "evidence-2"]
    assert [record.evidence_id for record in synthesis] == ["evidence-1", "evidence-2"]
    assert [row["evidence_target_tokens"] for row in stats["per_step"]] == [12_000, 20_000]
@pytest.mark.asyncio
async def test_resolved_generic_overview_skips_planner_and_hides_internal_ids():
    internal_uuid = "ca2dd52f-4990-42e2-9e6e-4d179dbd0cb2"

    class OverviewRetriever(Retriever):
        async def list_entities_by_ontology(self, _ontology_id, *, skip=0, limit=500):
            return ([
                {"node_id": internal_uuid, "alias": "Ernst", "entity_definition_id": 8},
                {"node_id": "fuzzy-johan", "alias": "Johan", "entity_definition_id": 8},
            ] if skip == 0 else [])

        async def search_aliases(self, query, ontology_ids, top_k=10):
            return [RetrievedChunk(
                node_id=internal_uuid, node_label="EntityInstance", node_name="Ernst",
                instance_id="world-private", chunk_id="profile", chunk_index=0,
                text="Ernst is a careful investigator.", score=1.0, confidence_pct=100,
            )]

        async def hydrate_evidence_nodes(self, _node_ids, **_kwargs):
            return {internal_uuid: {
                "source_kind": "EntityInstance", "display_name": "Ernst",
                "display_text": "Ernst is a careful investigator.",
                "properties": {"source_type": "Character", "instance_id": "world-private"},
                "provenance": {"links": [{"entity_id": "friend-private", "entity_name": "Johnny"}]},
                "chunks": [],
            }}

    llm = LLM()
    orchestrator = ElderQueryV2(llm, ModelPolicy(), OverviewRetriever())
    agent = SimpleNamespace(id="elder-private", name="Elder", writing_style="careful",
                            ontologies=[SimpleNamespace(id=7)])
    response = await orchestrator.execute(
        agent, ElderQueryRequest(query="What can you tell me about Ernst?")
    )
    assert len(llm.messages) == 1
    assert [row["stage"] for row in response.llm_usage] == ["synthesize"]
    prompt = llm.messages[0]
    assert "Ernst is a careful investigator" in prompt
    assert "Johnny" in prompt
    for forbidden in (internal_uuid, "world-private", "friend-private", "elder-private"):
        assert forbidden not in prompt


def test_planner_fallback_uses_exact_resolved_entity_instead_of_broad_query():
    plan = fallback_plan(
        "Dear professor. What can you tell me about Hans?",
        {
            "resolved_entities": [
                {"node_id": "hans-id", "alias": "Hans", "confidence": 1.0},
                {"node_id": "johan-id", "alias": "Johan", "confidence": 0.63},
            ]
        },
    )

    assert [step.operation for step in plan.steps] == ["exact_lookup", "hybrid_search"]
    assert all(step.entity_refs == ["Hans"] for step in plan.steps)
    assert plan.steps[0].query == "Hans"


@pytest.mark.asyncio
async def test_planner_schema_repair_uses_global_repair_model():
    class BrokenPlannerLLM:
        def __init__(self):
            self.calls = []

        async def chat(self, *, model, usage_tag, **_kwargs):
            self.calls.append((usage_tag, model))
            if usage_tag.endswith(".plan"):
                return '{"answer_goal":"invalid","steps":"broken"}'
            if usage_tag.endswith(".plan.json_repair"):
                return (
                    '{"answer_goal":"grounded answer","steps":['
                    '{"id":"find","operation":"hybrid_search","query":"question",'
                    '"target_data_type":"entity","limit":5,'
                    '"evidence_type":"brief_fact"}]}'
                )
            return (
                '{"claims":[{"id":"claim-1","text":"Grounded answer",'
                '"citations":["evidence-1"]}],"uncertainty":null}'
            )

    llm = BrokenPlannerLLM()
    orchestrator = ElderQueryV2(llm, ModelPolicy(), Retriever())
    agent = SimpleNamespace(
        id="elder-1", name="Elder", writing_style="careful",
        ontologies=[SimpleNamespace(id=7)],
    )
    await orchestrator.execute(
        agent,
        ElderQueryRequest(query="Answer this question", instance_id="world-1"),
    )

    assert llm.calls[0][1].name == "elder-planner-test"
    assert llm.calls[1][1] == "elder-test"
    assert llm.calls[1][0].endswith(".plan.schema_repair")
    assert llm.calls[2][1].name == "elder-synthesis-test"


@pytest.mark.asyncio
async def test_synthesis_schema_repair_uses_global_repair_model():
    class BrokenThenRepairedLLM:
        def __init__(self):
            self.models = []

        async def chat(self, *, model, **_kwargs):
            self.models.append(model)
            if len(self.models) == 1:
                return '{"claims":"invalid"}'
            return (
                '{"claims":[{"id":"claim-1","text":"Grounded answer",'
                '"citations":["evidence-1"]}],"uncertainty":null}'
            )

    llm = BrokenThenRepairedLLM()
    orchestrator = ElderQueryV2(llm, ModelPolicy(), Retriever())
    answer = await orchestrator._neutral_synthesis_call(
        model="synthesis-model",
        prompt="prompt",
        temperature=0.1,
        usage_tag="elder.v2.test.synthesize",
        evidence_ids={"evidence-1"},
    )

    assert answer.claims[0].text == "Grounded answer"
    assert llm.models[0] == "synthesis-model"
    assert llm.models[1] == "elder-test"


@pytest.mark.asyncio
async def test_planner_repairs_malformed_json_with_shared_service():
    class BrokenThenRepairedLLM:
        def __init__(self):
            self.calls = []

        async def chat(self, *, messages, usage_tag, **_kwargs):
            self.calls.append({"prompt": messages[-1]["content"], "usage_tag": usage_tag})
            if len(self.calls) == 1:
                return "```json\n{answer_goal: broken]\n```"
            return (
                '{"answer_goal":"repaired","steps":['
                '{"id":"primary","operation":"hybrid_search","query":"question",'
                '"evidence_type":"brief_fact"}]}'
            )

    llm = BrokenThenRepairedLLM()
    plan = await create_retrieval_plan(
        llm_client=llm,
        model="planner-model",
        repair_model="repair-model",
        query="question",
        grounding={"ontology_ids": [7]},
    )
    assert plan.answer_goal == "repaired"
    assert [call["usage_tag"] for call in llm.calls] == [
        "elder.v2.plan",
        "elder.v2.plan.json_repair",
    ]
    assert '"answer_goal"' in llm.calls[0]["prompt"]
    assert '"steps"' in llm.calls[0]["prompt"]
    assert '"query_intent"' in llm.calls[0]["prompt"]
    assert '"filters"' in llm.calls[0]["prompt"]
    assert '"temporal"' in llm.calls[0]["prompt"]


@pytest.mark.asyncio
async def test_planner_prompt_contains_readable_grounding_but_no_internal_ids():
    internal_uuid = "ca2dd52f-4990-42e2-9e6e-4d179dbd0cb2"

    class PlannerLLM:
        def __init__(self):
            self.prompt = ""

        async def chat(self, *, messages, **_kwargs):
            self.prompt = messages[-1]["content"]
            return (
                '{"answer_goal":"find Ernst","steps":[{"id":"find",'
                '"operation":"hybrid_search","query":"Ernst",'
                '"evidence_type":"brief_fact"}]}'
            )

    llm = PlannerLLM()
    await create_retrieval_plan(
        llm_client=llm, model="planner", repair_model="repair", query="What did Ernst do?",
        grounding={
            "ontology_ids": [7], "active_instance_id": "world-private",
            "definitions": [{"definition_id": 8, "name": "Character"}],
            "resolved_entities": [{"node_id": internal_uuid, "alias": "Ernst", "confidence": 1.0}],
        },
    )
    assert "Ernst" in llm.prompt and "Character" in llm.prompt
    for forbidden in (internal_uuid, "world-private", '"ontology_ids"', '"instance_id"', '"node_id"'):
        assert forbidden not in llm.prompt


@pytest.mark.asyncio
async def test_grounding_uses_structured_definitions_and_definition_aware_entities():
    definitions = [{
        "definition_id": 4,
        "name": "Story",
        "description": "Narrative events",
        "properties": [{"property_id": 31, "name": "content", "data_type": "TEXT"}],
        "relationships": [{
            "relationship_definition_id": 18,
            "name": "belongs to",
            "target_definition_id": 7,
        }],
    }]
    resolved = [{
        "node_id": "entity-1", "alias": "Valens", "entity_definition_id": 8,
        "confidence": 1.0,
    }]
    grounding = await build_grounding_package(
        retriever=Retriever(),
        ontology_ids=[12],
        instance_id="instance-uuid",
        definitions=definitions,
        resolved_entities=resolved,
        chat_history=[],
    )
    assert grounding == {
        "ontology_ids": [12],
        "definitions": definitions,
        "active_instance_id": "instance-uuid",
        "resolved_entities": resolved,
    }
