from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.jobs.librarian.librarian import LibrarianOrchestrator
from app.jobs.librarian.schemas import LibrarianQueryRequest


class SequenceLLM:
    def __init__(self, replies: list[str]):
        self.replies = iter(replies)
        self.tags: list[str | None] = []
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.tags.append(kwargs.get("usage_tag"))
        self.calls.append(kwargs)
        return next(self.replies)


class Retrieval:
    def __init__(self):
        self.queries: list[str] = []

    async def retrieve(self, *, query, ontology_id, trace, **_kwargs):
        self.queries.append(query)
        trace.extend([
            {"step": "v2_parallel_retrieve", "data": {"branch_counts": {"vector": 1, "fulltext": 1, "exact": 0}}},
            {"step": "v2_diversity_expand", "data": {"exclusions": []}},
        ])
        index = len(self.queries)
        return [{
            "library_item_id": 7, "chunk_index": index, "chunk_id": f"child-{index}",
            "parent_chunk_id": f"parent-{index}", "page_number": index,
            "physical_page_numbers": [index], "text": f"Evidence for {query}",
            "matched_child_text": query, "score": 1.0 / index,
            "expansion_mode": "complete_parent",
        }]


@pytest.mark.asyncio
async def test_v2_runs_two_follow_up_passes_and_preserves_provenance(tmp_path, monkeypatch):
    llm = SequenceLLM([
        '{"information_needs":["Need A"]}',
        '{"adequate":false,"covered_needs":[],"missing_needs":["Need B"],"reason":"missing B"}',
        '{"adequate":false,"covered_needs":["Need A"],"missing_needs":["Need C"],"reason":"missing C"}',
        '{"adequate":true,"covered_needs":["Need A","Need B","Need C"],"missing_needs":[],"reason":"complete"}',
    ])
    orchestrator = LibrarianOrchestrator(llm_client=llm, debug_artifacts_enabled=True)
    retrieval = Retrieval()
    orchestrator.retrieval = retrieval

    async def items(*_args, **_kwargs): return [7]
    async def metadata(*_args, **_kwargs):
        return {7: {"title": "Rules", "authors": "Author", "vectorized": True}}
    orchestrator._active_items = items
    orchestrator._metadata = metadata
    monkeypatch.setenv("SHRECKNET_DATA_DIR", str(tmp_path))

    response = await orchestrator.execute(
        SimpleNamespace(id="agent", writing_style=None,
                        ontologies=[SimpleNamespace(id=1, rpg_system="System")]),
        LibrarianQueryRequest(query="Question?", mode="context", include_trace=True),
        SimpleNamespace(),
    )

    assert retrieval.queries == ["Need A", "Need B", "Need C"]
    assert len(response.chunks) == 3
    assert response.subqueries == ["Need A"]
    assert [step["data"]["pass"] for step in response.trace if step["step"] == "v2_evidence_validation"] == [0, 1, 2]
    manifest = next((tmp_path / "local_tests" / "librarian").glob("*/manifest.json"))
    assert manifest.exists()


@pytest.mark.asyncio
async def test_planner_invalid_json_falls_back_to_original_query(tmp_path):
    orchestrator = LibrarianOrchestrator(llm_client=SequenceLLM(["not json"]))
    from app.jobs.librarian.debug_artifacts import LibrarianDebugArtifacts
    needs = await orchestrator._plan("¿Cómo funciona?", "Sistema", LibrarianDebugArtifacts(tmp_path))
    assert needs == ["¿Cómo funciona?"]


@pytest.mark.asyncio
async def test_planner_uses_shared_json_repair_service(tmp_path):
    llm = SequenceLLM([
        '{information_needs: ["Need armor rules"]}',
        '{"information_needs":["Need armor rules"]}',
    ])
    orchestrator = LibrarianOrchestrator(llm_client=llm, repair_json_model="repair-model")
    from app.jobs.librarian.debug_artifacts import LibrarianDebugArtifacts

    needs = await orchestrator._plan("How does armor work?", "System", LibrarianDebugArtifacts(tmp_path))

    assert needs == ["Need armor rules"]
    assert llm.tags == ["librarian_plan", "agents.json_repair"]


def test_merge_evidence_keeps_best_hit_and_all_need_pass_provenance():
    orchestrator = LibrarianOrchestrator(llm_client=SimpleNamespace())
    merged = orchestrator._merge(
        [{"parent_chunk_id": "p", "score": 0.2, "matched_needs": ["A"], "retrieval_passes": [0]}],
        [{"parent_chunk_id": "p", "score": 0.9, "matched_needs": ["B"], "retrieval_passes": [1]}],
    )
    assert merged[0]["score"] == 0.9
    assert merged[0]["matched_needs"] == ["A", "B"]
    assert merged[0]["retrieval_passes"] == [0, 1]


@pytest.mark.asyncio
async def test_synthesis_formats_evidence_validation_contract(tmp_path):
    llm = SequenceLLM(["Grounded answer"])
    orchestrator = LibrarianOrchestrator(llm_client=llm)
    from app.jobs.librarian.debug_artifacts import LibrarianDebugArtifacts
    from app.jobs.librarian.query_v2 import EvidenceValidation
    from app.jobs.librarian.schemas import RetrievedChunk

    answer = await orchestrator._synthesize(
        "How does armor work?",
        [RetrievedChunk(library_item_id=1, page_number=2, text="Armor evidence", score=0.9,
                        source_id="source-1", book_title="Rules")],
        None,
        "System",
        [],
        LibrarianDebugArtifacts(tmp_path),
        validation=EvidenceValidation(False, ["Armor basics"], ["Armor exceptions"], "Missing exceptions"),
        warning="Missing exceptions",
    )

    assert answer == "Grounded answer"
    assert llm.tags == ["librarian_answer"]
    synthesis_prompt = llm.calls[0]["messages"][1]["content"]
    assert '"missing_needs": [' in synthesis_prompt
    assert "Armor exceptions" in synthesis_prompt


def test_synthesis_evidence_budget_keeps_one_chunk_that_crosses_the_limit(monkeypatch):
    from app.jobs.librarian.query_v2 import LibrarianQueryV2
    from app.jobs.librarian.schemas import RetrievedChunk

    def fake_estimate(value: str) -> int:
        if "at-budget" in value:
            return 30_000
        if "near-budget" in value:
            return 29_000
        if "crosses-budget" in value:
            return 11_000
        return 500

    monkeypatch.setattr("app.jobs.librarian.query_v2.estimate_tokens", fake_estimate)
    chunks = [
        RetrievedChunk(
            library_item_id=index,
            page_number=index,
            text=text,
            score=1.0 - index / 10,
            source_id=f"source-{index}",
        )
        for index, text in enumerate(["near-budget", "crosses-budget", "must-not-be-included"], 1)
    ]

    selected, estimated_tokens = LibrarianQueryV2._select_synthesis_evidence(chunks)

    assert [chunk.text for chunk in selected] == ["near-budget", "crosses-budget"]
    assert estimated_tokens == 40_000

    at_budget, exact_tokens = LibrarianQueryV2._select_synthesis_evidence([
        chunks[0].model_copy(update={"text": "at-budget"}),
        chunks[1],
        chunks[2],
    ])
    assert [chunk.text for chunk in at_budget] == ["at-budget", "crosses-budget"]
    assert exact_tokens == 41_000
