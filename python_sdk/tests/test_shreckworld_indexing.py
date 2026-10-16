import asyncio

import httpx

from shrecknet_client import KnowledgeAnswer, Shrecknet


def test_indexing_polling_and_readiness_preserve_model_diagnostics_and_auth():
    async def scenario():
        async with Shrecknet(token="manager-token") as sdk:
            calls = []
            async def handler(request):
                assert request.headers["Authorization"] == "Bearer manager-token"
                calls.append(str(request.url))
                if request.url.path == "/ready":
                    return httpx.Response(200, json={"status": "ready", "embedding": {
                        "ready": True, "backend": "sentence_transformers", "model": "intfloat/multilingual-e5-small",
                        "fingerprint": "a" * 64, "revision": "revision", "dimension": 384, "device": "cpu"},
                        "parser": "docling", "parser_artifacts_present": True})
                return httpx.Response(202 if request.method == "POST" else 200, json={
                    "id": "job/a", "library_item_id": "book/a", "status": "completed", "progress": 100,
                    "created_at": "2026-10-15T12:00:00Z", "processing_seconds": 12.5,
                    "metrics": {"phase": "completed", "embedding_backend": "sentence_transformers",
                                "embedding_fingerprint": "a" * 64, "parent_chunks": 4, "total_chunks": 10,
                                "phase_seconds": {"parsing": 10.0, "embedding": 2.5}}})
            await sdk.shreckworld._transport._client.aclose()
            sdk.shreckworld._transport._client = httpx.AsyncClient(base_url="http://world.test", transport=httpx.MockTransport(handler))
            assert (await sdk.shreckworld.readiness()).embedding.dimension == 384
            job = await sdk.shreckworld.start_indexing("world/a", "book/a")
            result = await sdk.shreckworld.indexing_job("world/a", "book/a", job_id=job.id)
            assert result.metrics.embedding_fingerprint == "a" * 64
            assert result.processing_seconds == 12.5 and result.metrics.parent_chunks == 4
            assert "/world%2Fa/library-items/book%2Fa/index-jobs/job%2Fa" in calls[-1]
            await sdk.shreckworld.indexing_job("world/a", "book/a")
            assert calls[-1].endswith("/index-status")
    asyncio.run(scenario())


def test_structured_citation_geometry_and_parent_evidence_are_typed():
    answer = KnowledgeAnswer.model_validate({"shreckworld_id": "world", "query": "rule", "answer": "evidence",
        "trace": {"embedding_backend": "sentence_transformers", "reranker_fallback": False},
        "citations": [{"source_id": "source-1", "library_item_id": "book", "title": "Rules", "page_number": 2,
            "chunk_id": "child", "excerpt": "Rule", "authority_priority": 100, "parent_chunk_id": "parent",
            "content_type": "table", "heading_path": ["Combat"], "page_numbers": [2], "evidence_text": "Full rule",
            "content_url": "/shreckworlds/world/library-items/book/content#page=2",
            "provenance": [{"block_id": "block", "page_number": 2, "page_width": 600, "page_height": 800,
                "bounding_box": {"left": 10, "top": 20, "right": 30, "bottom": 40, "coord_origin": "TOPLEFT"}, "charspan": [0, 4]}],
            "rectangles": [{"page_number": 2, "left": 1, "top": 2, "width": 3, "height": 4}]}]})
    assert answer.citations[0].provenance[0].charspan == (0, 4)
    assert answer.citations[0].rectangles[0].height == 4
    assert answer.citations[0].evidence_text == "Full rule"
