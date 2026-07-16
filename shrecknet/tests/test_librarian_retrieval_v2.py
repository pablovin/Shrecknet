from __future__ import annotations

import asyncio

import pytest

from app.jobs.librarian.retrieval_strategies import LibrarianRetrievalStrategyV2


def _row(chunk_id: str, score: float = 1.0, **props):
    return {"props": {"chunk_id": chunk_id, "library_item_id": 1, "chunk_index": 1, **props}, "score": score}


def test_rrf_uses_rank_not_raw_scores_and_merges_chunk_ids() -> None:
    fused = LibrarianRetrievalStrategyV2.reciprocal_rank_fusion({
        "vector": [_row("a", 0.01), _row("b", 999999.0)],
        "fulltext": [_row("a", 0.00001)],
        "exact": [_row("c", 10**20)],
    })
    assert [item["chunk_id"] for item in fused] == ["a", "c", "b"]
    assert fused[0]["branch_ranks"] == {"vector": 1, "fulltext": 1}


@pytest.mark.asyncio
async def test_reranker_failure_preserves_rrf_order(monkeypatch) -> None:
    strategy = LibrarianRetrievalStrategyV2()
    monkeypatch.setattr(strategy, "_load_reranker", lambda: None)
    candidates = [
        {"chunk_id": str(i), "props": {"display_text": str(i)}, "rrf_score": 1 / (60 + i), "branch_ranks": {}}
        for i in range(1, 31)
    ]
    result, fallback = await strategy._rerank("Named Term", candidates)
    assert fallback is True
    assert result == candidates


@pytest.mark.asyncio
async def test_retrieve_prefixes_once_runs_branches_concurrently_and_clamps_selection(monkeypatch) -> None:
    strategy = LibrarianRetrievalStrategyV2()
    embedded: list[str] = []
    started: set[str] = set()
    release = asyncio.Event()

    class Runtime:
        async def embed_query(self, text, **_kwargs):
            embedded.append(text)
            return [0.1, 0.2]

    async def branch(name):
        started.add(name)
        if len(started) == 3:
            release.set()
        await asyncio.wait_for(release.wait(), 1)
        return [_row(f"{name}-{i}", display_text=f"unique {name} text {i}", parent_chunk_id=f"p-{name}-{i}") for i in range(8)]

    async def ready(): return Runtime()
    async def noop(): return None
    async def identity_rerank(_query, candidates): return candidates, False
    async def identity_expand(selected, _table_like):
        return [{"chunk_id": item["chunk_id"], "library_item_id": 1, "page_number": 1,
                 "score": item["rrf_score"], "expansion_mode": "complete_parent"} for item in selected]

    monkeypatch.setattr("app.jobs.librarian.retrieval_strategies.get_ready_embedding_runtime", ready)
    monkeypatch.setattr(strategy, "ensure_indexes", noop)
    monkeypatch.setattr(strategy, "_vector", lambda *_args: branch("vector"))
    monkeypatch.setattr(strategy, "_fulltext", lambda *_args: branch("fulltext"))
    monkeypatch.setattr(strategy, "_exact", lambda *_args: branch("exact"))
    monkeypatch.setattr(strategy, "_rerank", identity_rerank)
    monkeypatch.setattr(strategy, "_expand", identity_expand)

    result = await strategy.retrieve(query="The Named Term", ontology_id=2, library_item_ids=None,
                                     active_library_item_ids=[1], top_k=50)
    assert embedded == ["query: The Named Term"]
    assert started == {"vector", "fulltext", "exact"}
    assert len(result) == 8


@pytest.mark.asyncio
async def test_fulltext_passes_lucene_query_without_colliding_with_cypher_argument(monkeypatch) -> None:
    captured = {}

    class EmptyResult:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class Session:
        async def run(self, cypher, parameters):
            captured["cypher"] = cypher
            captured["params"] = parameters
            return EmptyResult()

    class SessionContext:
        async def __aenter__(self):
            return Session()

        async def __aexit__(self, *_args):
            return None

    strategy = LibrarianRetrievalStrategyV2(session_factory=SessionContext)
    await strategy._fulltext(
        "Sanity recovery?",
        {"ontology_id": 1, "requested_ids": [], "active_ids": [19]},
    )

    assert "db.index.fulltext.queryNodes" in captured["cypher"]
    assert captured["params"]["query"] == "Sanity OR recovery"
    assert captured["params"]["ontology_id"] == 1


@pytest.mark.asyncio
async def test_startup_preload_warms_the_same_shared_strategy_used_by_queries(monkeypatch) -> None:
    import app.jobs.librarian.retrieval_strategies as module

    class Model:
        def __init__(self): self.calls = []
        def predict(self, pairs): self.calls.append(pairs); return [0.5]

    model = Model()
    strategy = LibrarianRetrievalStrategyV2()
    monkeypatch.setattr(strategy, "_load_reranker", lambda: model)
    monkeypatch.setattr(module, "_shared_strategy", strategy)

    assert await module.preload_librarian_reranker() is True
    assert module.get_librarian_retrieval_strategy() is strategy
    assert model.calls == [[("warmup query", "warmup passage")]]
