import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from qdrant_client import QdrantClient
from sqlalchemy import select, text

from app.db.session import session_factory
from app.models import ShreckWorldDocument, ShreckWorldDocumentChunk, ShreckWorldLibraryItem
from app.services.embedding import EmbeddingUnavailable
from app.services.citation_regions import citation_rectangles
from test_indexing_jobs import api, queued_job, run, runtime


def query(api, **kwargs):
    client, headers, world_id = api
    return client.post(f"/shreckworlds/{world_id}/query", headers=headers, json={"query": "Combat", "include_trace": True, **kwargs})


def test_hybrid_retrieval_parent_evidence_and_viewer_provenance(api, runtime, monkeypatch):
    prefix, item_id, job_id = queued_job(api, monkeypatch, content=b"Combat uses percentile dice. Roll below the skill.")
    run(item_id, job_id)
    with session_factory()() as session:
        child_id = session.scalar(select(ShreckWorldDocumentChunk.id).where(ShreckWorldDocumentChunk.chunk_role == "child"))
    monkeypatch.setattr("app.services.query.search_chunks", lambda **kwargs: ["foreign-or-stale", child_id])
    response = query(api, library_item_id=item_id)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["trace"]["vector_candidates"] == 1
    assert result["trace"]["fulltext_candidates"] == 1 and result["trace"]["exact_candidates"] == 1
    citation = result["citations"][0]
    assert citation["document_id"] == job_id and citation["parent_chunk_id"]
    assert citation["heading_path"] == ["Combat"]
    assert citation["page_number"] == 2 and citation["page_numbers"] == [2]
    assert citation["page_label"] == "1"
    assert citation["source_sha256"] and len(citation["source_sha256"]) == 64
    assert citation["rectangles"][0]["top"] == 12.5
    assert citation["provenance"][0]["bounding_box"]["coord_origin"] == "BOTTOMLEFT"
    assert citation["evidence_text"] == "Combat uses percentile dice. Roll below the skill."
    assert "passage:" not in citation["excerpt"]
    # The prior active generation remains searchable while a new attempt queues.
    client, headers, _ = api
    assert client.post(f"{prefix}/index", headers=headers).status_code == 202
    assert query(api, library_item_id=item_id).json()["citations"][0]["document_id"] == job_id


def test_legacy_and_wrong_model_vectors_require_reindex(api, runtime, monkeypatch):
    _, item_id, job_id = queued_job(api, monkeypatch)
    run(item_id, job_id)
    with session_factory()() as session:
        document = session.get(ShreckWorldDocument, job_id)
        document.embedding_fingerprint = "b" * 64
        session.commit()
    result = query(api).json()
    assert result["citations"] == []
    assert result["trace"]["sources_requiring_reindex"] == 1
    with session_factory()() as session:
        session.execute(text("UPDATE shreckworld_document_chunks SET document_id = NULL"))
        session.execute(text("UPDATE shreckworld_documents SET is_active = 0"))
        session.commit()
    # Even with the same vector dimension, unknown legacy/hash identity is rejected.
    result = query(api).json()
    assert result["citations"] == [] and result["trace"]["sources_requiring_reindex"] == 1


def test_sql_semantic_fallback_is_explicit_and_book_filter_is_enforced(api, runtime, monkeypatch):
    _, one_id, one_job = queued_job(api, monkeypatch, title="One")
    run(one_id, one_job)
    _, two_id, two_job = queued_job(api, monkeypatch, title="Two", content=b"Different evidence")
    run(two_id, two_job)
    def offline(**kwargs):
        raise ConnectionError("Qdrant unavailable")
    monkeypatch.setattr("app.services.query.search_chunks", offline)
    result = query(api, library_item_id=one_id).json()
    assert result["trace"]["qdrant_fallback"]
    assert {citation["library_item_id"] for citation in result["citations"]} == {one_id}
    assert result["trace"]["embedding_backend"] == "sentence_transformers"


def test_real_qdrant_projection_search_counts_and_retired_cleanup(api, runtime, monkeypatch):
    from app.services.retrieval_index import upsert_item_chunks, delete_document_chunks
    client = QdrantClient(":memory:")
    monkeypatch.setattr("app.services.retrieval_index.get_client", lambda: client)
    monkeypatch.setattr("app.services.ingestion.upsert_item_chunks", upsert_item_chunks)
    monkeypatch.setattr("app.services.ingestion.delete_document_chunks", delete_document_chunks)
    prefix, item_id, first_id = queued_job(api, monkeypatch)
    run(item_id, first_id)
    assert query(api).json()["citations"][0]["document_id"] == first_id
    http, headers, _ = api
    second_id = http.post(f"{prefix}/index", headers=headers).json()["id"]
    run(item_id, second_id)
    from app.core.config import get_settings
    points, _ = client.scroll(get_settings().qdrant_collection, limit=100)
    assert {point.payload["document_id"] for point in points} == {second_id}
    result = query(api).json()
    assert result["citations"][0]["document_id"] == second_id and not result["trace"]["qdrant_fallback"]
    # SQL cascade and the FTS cleanup trigger remove private derived records.
    assert http.delete(prefix, headers=headers).status_code == 204
    with session_factory()() as session:
        assert list(session.scalars(select(ShreckWorldDocument))) == []
        assert session.execute(text("SELECT count(*) FROM shreckworld_chunk_fts")).scalar_one() == 0
    assert client.count(get_settings().qdrant_collection, exact=True).count == 0
    client.close()


def test_invalid_qdrant_configuration_cannot_activate(api, runtime, monkeypatch):
    from app.services.retrieval_index import upsert_item_chunks
    from qdrant_client.models import Distance, VectorParams
    from app.core.config import get_settings
    client = QdrantClient(":memory:")
    client.create_collection(get_settings().qdrant_collection, vectors_config=VectorParams(size=3, distance=Distance.COSINE))
    monkeypatch.setattr("app.services.retrieval_index.get_client", lambda: client)
    monkeypatch.setattr("app.services.ingestion.upsert_item_chunks", upsert_item_chunks)
    prefix, item_id, job_id = queued_job(api, monkeypatch)
    run(item_id, job_id)
    http, headers, _ = api
    result = http.get(f"{prefix}/index-jobs/{job_id}", headers=headers).json()
    assert result["status"] == "failed" and "configuration" in result["detail"]
    with session_factory()() as session:
        assert session.get(ShreckWorldDocument, job_id) is None
    client.close()


def test_indexing_fails_fast_and_reading_survives_model_failure(api, monkeypatch):
    from test_upload_metadata_api import upload
    item = upload(api, params={"title": "Source"}).json()
    class Unavailable:
        def ensure_ready(self):
            raise EmbeddingUnavailable("missing model")
    monkeypatch.setattr("app.api.routers.shreckworlds.get_embedder", Unavailable)
    monkeypatch.setattr("app.main.get_embedder", Unavailable)
    client, headers, world_id = api
    prefix = f"/admin/shreckworlds/{world_id}/library-items/{item['id']}"
    assert client.post(f"{prefix}/index", headers=headers).status_code == 503
    assert client.get("/ready").status_code == 503
    assert client.get("/health").status_code == 200
    assert client.get(f"/shreckworlds/{world_id}/library-items/{item['id']}/content", headers=headers).status_code == 200
    with session_factory()() as session:
        assert session.get(ShreckWorldLibraryItem, item['id']).index_status == "unindexed"


@pytest.mark.parametrize("origin,top,bottom,expected", [("BOTTOMLEFT", 700, 600, 12.5), ("TOPLEFT", 100, 200, 12.5), ("UNKNOWN", 100, 200, None)])
def test_viewer_coordinate_origins_and_unknown_rejection(origin, top, bottom, expected):
    spans = [{"page_number": 2, "page_width": 600, "page_height": 800,
              "bounding_box": {"left": 10, "right": 500, "top": top, "bottom": bottom, "coord_origin": origin}}]
    areas = citation_rectangles(spans)
    if expected is None:
        assert areas == []
    else:
        assert areas[0].top == expected
        assert areas[0].height == 12.5
