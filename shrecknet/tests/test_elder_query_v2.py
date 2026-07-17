from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.jobs.elder.context_budget import partition_complete_records, serialize_evidence
from app.jobs.elder.evidence import compact_synthesis_evidence, json_safe
from app.jobs.elder.executor import ElderRetrievalExecutor
from app.jobs.elder.grounding import build_grounding_package
from app.jobs.elder.planner import create_retrieval_plan, enforce_complete_source_policy, validate_bounded_cypher
from app.jobs.elder.query_v2 import ElderQueryV2
from app.jobs.elder.schemas import ElderQueryRequest, RetrievedChunk
from app.jobs.elder.v2_schemas import EvidenceCapacityError, EvidenceRecord, RetrievalPlan


class ModelPolicy:
    model_elder = "elder-test"
    model_agents_repair_json = "elder-test"

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
        })
        if len(self.messages) == 1:
            return (
                '{"answer_goal":"answer completely","steps":['
                '{"id":"find","operation":"hybrid_search","query":"long entity",'
                '"target_data_type":"entity","limit":5}]}'
            )
        return "Grounded answer [evidence-1:entity-1]"

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
    orchestrator = ElderQueryV2(llm, ModelPolicy(), Retriever(), default_top_k=5)
    agent = SimpleNamespace(
        id="elder-1", name="Elder", writing_style="careful", ontologies=[SimpleNamespace(id=7)]
    )
    response = await orchestrator.execute(
        agent, ElderQueryRequest(query="Summarize the whole entity", instance_id="world-1")
    )
    assert len(llm.messages) == 2
    assert "BEGIN " in llm.messages[1] and " END" in llm.messages[1]
    assert response.sources[0].evidence_chunks[0].text.endswith(" END")
    assert response.pipeline_version == "elder-query-retrieval-v2"
    assert "intents" not in response.model_dump()
    assert [row["stage"] for row in response.llm_usage] == ["plan", "synthesize"]
    assert response.llm_usage_totals == {
        "calls": 2, "input_tokens": 300, "output_tokens": 20, "total_tokens": 320,
    }
    console = capsys.readouterr().out
    assert "[ELDER_LLM_USAGE]" in console
    assert "stage=plan" in console
    assert "stage=synthesize" in console
    assert "[ELDER_LLM_USAGE_TOTAL]" in console
    assert "total_tokens=320" in console


@pytest.mark.asyncio
async def test_executor_uses_dependency_waves():
    plan = RetrievalPlan.model_validate({
        "answer_goal": "timeline", "steps": [
            {"id": "a", "operation": "hybrid_search", "query": "a"},
            {"id": "b", "operation": "hybrid_search", "query": "b"},
            {"id": "c", "operation": "hydrate_sources", "depends_on": ["a", "b"]},
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
             "traversal": {"relationships": ["DERIVED_FROM", "CONTAINS"], "depth": 1}},
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
async def test_failed_structural_operation_skips_dependants_without_semantic_fallback():
    plan = RetrievalPlan.model_validate({
        "answer_goal": "latest", "steps": [
            {"id": "latest", "operation": "select_nodes", "target_data_type": "entity"},
            {"id": "details", "operation": "traverse_graph", "inputs": ["latest"]},
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
        }]
    })
    with pytest.raises(ValueError, match="unsafe"):
        validate_bounded_cypher(plan, [7])


def test_budget_never_splits_one_evidence_record():
    record = EvidenceRecord(
        evidence_id="huge", node_id="n", display_text="x" * 10_000, complete=True
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


def test_synthesis_projection_removes_internal_fields_html_and_node_id():
    internal_uuid = "ca2dd52f-4990-42e2-9e6e-4d179dbd0cb2"
    record = EvidenceRecord(
        evidence_id=f"evidence-4:{internal_uuid}", node_id=internal_uuid,
        source_kind="entity_text_chunk", display_name="Story",
        display_text='<a href="/content/private" data-ontology-instance="secret">Ernst</a> acts.',
        properties={"date": "2026-03-10", "text_linked": "duplicate", "avatar_url": "private"},
        provenance={"links": [{"entity_id": internal_uuid, "entity_name": "Johnny"}]},
        score=0.99, retrieval_methods=["vector"],
    )
    compact = compact_synthesis_evidence([record], evidence_budget_tokens=10_000)
    payload = compact[0].model_dump_json()
    assert compact[0].evidence_id == "evidence-4"
    assert compact[0].text == "Ernst acts."
    assert compact[0].related_entities == ["Johnny"]
    assert compact[0].canonical_facts == {"date": "2026-03-10"}
    for forbidden in (internal_uuid, "href", "text_linked", "avatar_url", "score"):
        assert forbidden not in payload


def test_complete_source_requires_explicit_source_summary():
    plan = RetrievalPlan.model_validate({"answer_goal": "overview", "steps": [{
        "id": "hydrate", "operation": "hydrate_sources", "hydration_mode": "complete_source",
    }]})
    assert enforce_complete_source_policy(plan, "What can you tell me about Ernst?").steps[0].hydration_mode == "local_context"
    complete = RetrievalPlan.model_validate({"answer_goal": "summary", "steps": [{
        "id": "hydrate", "operation": "hydrate_sources", "hydration_mode": "complete_source",
    }]})
    assert enforce_complete_source_policy(complete, "Summarize the latest story.").steps[0].hydration_mode == "complete_source"


@pytest.mark.asyncio
async def test_resolved_generic_overview_skips_planner_and_hides_internal_ids():
    internal_uuid = "ca2dd52f-4990-42e2-9e6e-4d179dbd0cb2"

    class OverviewRetriever(Retriever):
        async def list_entities_by_ontology(self, _ontology_id, *, skip=0, limit=500):
            return ([{"node_id": internal_uuid, "alias": "Ernst", "entity_definition_id": 8}]
                    if skip == 0 else [])

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
    orchestrator = ElderQueryV2(llm, ModelPolicy(), OverviewRetriever(), default_top_k=5)
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
                '{"id":"primary","operation":"hybrid_search","query":"question"}]}'
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
            return '{"answer_goal":"find Ernst","steps":[{"id":"find","operation":"hybrid_search","query":"Ernst"}]}'

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
