from datetime import datetime, timedelta, timezone
from itertools import count

import pytest
from sqlalchemy import select, text

from app.db.session import session_factory
from app.models import ShreckWorldDocument, ShreckWorldDocumentChunk, ShreckWorldDocumentBlock, ShreckWorldEmbeddingJob, ShreckWorldLibraryItem
from app.schemas.shreckworld import EmbeddingJobRead
from app.services.embedding import EmbeddingUnavailable
from app.services.ingestion import embed_library_item
from semantic_helpers import SemanticRuntime, parsed_source
from test_upload_metadata_api import api, upload


@pytest.fixture
def runtime(monkeypatch):
    model = SemanticRuntime()
    monkeypatch.setattr("app.services.ingestion.get_embedder", lambda: model)
    monkeypatch.setattr("app.services.query.get_embedder", lambda: model)
    monkeypatch.setattr("app.api.routers.shreckworlds.get_embedder", lambda: model)
    monkeypatch.setattr("app.services.ingestion.parse_source", parsed_source)
    monkeypatch.setattr("app.services.ingestion.upsert_item_chunks", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.services.ingestion.delete_document_chunks", lambda *args: None)
    return model


def queued_job(api, monkeypatch, *, content=b"one two three", title="Book"):
    client, headers, world_id = api
    response = upload(api, "notes.txt", content, data={"title": title})
    assert response.status_code == 201, response.text
    item_id = response.json()["id"]
    from app.tasks.embedding import embed_shreckworld_library_item
    monkeypatch.setattr(embed_shreckworld_library_item, "delay", lambda *args: None)
    prefix = f"/admin/shreckworlds/{world_id}/library-items/{item_id}"
    job = client.post(f"{prefix}/index", headers=headers)
    assert job.status_code == 202, job.text
    return prefix, item_id, job.json()["id"]


def run(item_id, job_id):
    with session_factory()() as session:
        embed_library_item(session, library_item_id=item_id, job_id=job_id)


def test_polling_job_timing_and_isolation(api, runtime, monkeypatch):
    client, headers, world_id = api
    prefix, item_id, job_id = queued_job(api, monkeypatch)
    job = client.get(f"{prefix}/index-jobs/{job_id}", headers=headers).json()
    assert job["status"] == "queued" and job["metrics"] is None
    assert job["processing_seconds"] is None and job["elapsed_seconds"] >= 0
    assert job["queue_wait_seconds"] >= 0 and "metrics_json" not in job
    assert client.get(f"{prefix}/index-status", headers=headers).json()["id"] == job_id
    assert client.get(f"{prefix}/embedding-status", headers=headers).json()["id"] == job_id
    assert client.post(f"{prefix}/index", headers=headers).status_code == 409
    other_prefix, _, _ = queued_job(api, monkeypatch, title="Other")
    assert client.get(f"{other_prefix}/index-jobs/{job_id}", headers=headers).status_code == 404
    assert client.get(f"{prefix}/index-jobs/missing", headers=headers).status_code == 404
    other_world = client.post("/admin/shreckworlds", headers=headers, json={"name": "Other", "shrecknet_world_id": "other"}).json()
    assert client.get(f"{prefix.replace(world_id, other_world['id'])}/index-jobs/{job_id}", headers=headers).status_code == 404
    from app.main import app
    from app.api.deps import get_current_user, require_world_manager, ShrecknetPrincipal
    app.dependency_overrides.pop(require_world_manager)
    app.dependency_overrides[get_current_user] = lambda: ShrecknetPrincipal(user_id="2", role="player")
    assert client.get(f"{prefix}/index-jobs/{job_id}", headers=headers).status_code == 403
    app.dependency_overrides.pop(get_current_user)
    assert client.get(f"{prefix}/index-jobs/{job_id}").status_code == 401


def test_live_progress_structural_chunks_and_final_metrics(api, runtime, monkeypatch):
    client, headers, _ = api
    prefix, item_id, job_id = queued_job(api, monkeypatch, content=("text " * 350).encode())
    ticks, snapshots = count(), []
    monkeypatch.setattr("app.services.ingestion.monotonic", lambda: next(ticks) * 1.1)
    def observe():
        with session_factory()() as observer:
            snapshots.append(EmbeddingJobRead.model_validate(observer.get(ShreckWorldEmbeddingJob, job_id)).model_dump())
            assert list(observer.scalars(select(ShreckWorldDocumentChunk))) == []
    runtime.before_batch = observe
    run(item_id, job_id)
    final = client.get(f"{prefix}/index-jobs/{job_id}", headers=headers).json()
    assert final["status"] == "completed", final
    metrics = final["metrics"]
    assert final["progress"] == 100 and final["processing_seconds"] >= 0
    assert final["updated_at"] == final["completed_at"]
    assert metrics["processed_chunks"] == metrics["total_chunks"] > 1
    assert [snapshot["metrics"]["processed_chunks"] for snapshot in snapshots] == list(range(metrics["total_chunks"]))
    assert all(snapshot["metrics"]["phase"] == "embedding" for snapshot in snapshots)
    assert metrics["processed_pages"] == metrics["total_pages"] == 2
    assert metrics["parent_chunks"] == 1 and metrics["blocks"] == 2
    assert metrics["empty_pages"] == 1 and metrics["text_pages"] == 1
    assert metrics["embedding_backend"] == "sentence_transformers"
    assert metrics["parser_cache_hit"] is False and metrics["parser_seconds"] == {}
    assert metrics["embedding_batch_size"] == runtime.settings.embedding_batch_size
    assert metrics["embedding_fingerprint"] == runtime.fingerprint
    assert set(metrics["phase_seconds"]) == {"validating_model", "parsing", "chunking", "embedding", "storing"}
    with session_factory()() as session:
        chunks = list(session.scalars(select(ShreckWorldDocumentChunk)))
        assert sum(chunk.chunk_role == "parent" for chunk in chunks) == 1
        assert all("passage:" not in chunk.text for chunk in chunks)
        assert all(chunk.token_count <= runtime.token_limit for chunk in chunks if chunk.chunk_role == "child")
        blocks = list(session.scalars(select(ShreckWorldDocumentBlock)))
        assert next(block for block in blocks if block.block_type == "paragraph").provenance[0]["page_no"] == 2
        assert session.get(ShreckWorldDocument, job_id).is_active
        assert session.execute(text("SELECT count(*) FROM shreckworld_chunk_fts")).scalar_one() == metrics["total_chunks"]


@pytest.mark.parametrize("failure", ["model", "empty", "parsing", "embedding", "storage", "activation", "coverage", "source_changed"])
def test_failed_reindex_preserves_active_generation(api, runtime, monkeypatch, failure):
    client, headers, _ = api
    prefix, item_id, first_id = queued_job(api, monkeypatch)
    run(item_id, first_id)
    with session_factory()() as session:
        original_ids = list(session.scalars(select(ShreckWorldDocumentChunk.id)))
    retry = client.post(f"{prefix}/index", headers=headers).json()["id"]
    def fail(*args, **kwargs):
        raise EmbeddingUnavailable("test failure")
    if failure == "model":
        monkeypatch.setattr(runtime, "ensure_ready", fail)
    elif failure == "empty":
        def empty(*args, **kwargs):
            result = parsed_source(*args, **kwargs)
            for block in result.document.blocks:
                if block.block_type != "heading":
                    block.display_text = ""
            return result
        monkeypatch.setattr("app.services.ingestion.parse_source", empty)
    elif failure == "parsing":
        monkeypatch.setattr("app.services.ingestion.parse_source", fail)
    elif failure == "embedding":
        monkeypatch.setattr(runtime, "embed_batch", fail)
    elif failure == "storage":
        monkeypatch.setattr("app.services.ingestion.upsert_item_chunks", fail)
    elif failure == "coverage":
        from shreckdoc.pipeline import build_chunks
        def omit_text_provenance(*args, **kwargs):
            chunks = build_chunks(*args, **kwargs)
            heading = kwargs["document"].blocks[0].block_id
            for chunk in chunks:
                if chunk.chunk_role == "child":
                    chunk.source_block_ids = [heading]
            return chunks
        monkeypatch.setattr("app.services.ingestion.build_chunks", omit_text_provenance)
    elif failure == "activation":
        from app.repositories.documents import activate_document
        def fail_after_sql(*args, **kwargs):
            activate_document(*args, **kwargs)
            raise RuntimeError("activation failed before commit")
        monkeypatch.setattr("app.services.ingestion.activate_document", fail_after_sql)
    else:
        with session_factory()() as session:
            item = session.get(ShreckWorldLibraryItem, item_id)
            from pathlib import Path
            Path(item.storage_path).write_text("changed bytes")
    run(item_id, retry)
    job = client.get(f"{prefix}/index-jobs/{retry}", headers=headers).json()
    assert job["status"] == job["metrics"]["phase"] == "failed", job
    assert job["completed_at"] and job["progress"] < 100 and job["processing_seconds"] >= 0
    with session_factory()() as session:
        item = session.get(ShreckWorldLibraryItem, item_id)
        assert item.index_status == "failed" and item.embedding_status == "ready"
        assert set(session.scalars(select(ShreckWorldDocumentChunk.id))) == set(original_ids)
        assert session.get(ShreckWorldDocument, first_id).is_active
        assert session.get(ShreckWorldDocument, retry) is None


def test_new_generation_replaces_previous_chunks_and_fts(api, runtime, monkeypatch):
    client, headers, _ = api
    prefix, item_id, first_id = queued_job(api, monkeypatch)
    run(item_id, first_id)
    new_id = client.post(f"{prefix}/index", headers=headers).json()["id"]
    run(item_id, new_id)
    with session_factory()() as session:
        assert not session.get(ShreckWorldDocument, first_id).is_active
        assert session.get(ShreckWorldDocument, new_id).is_active
        assert set(session.scalars(select(ShreckWorldDocumentChunk.document_id))) == {new_id}
        assert set(session.execute(text("SELECT document_id FROM shreckworld_chunk_fts")).scalars()) == {new_id}
    # A duplicate delivery of an already completed job must not re-run inference.
    monkeypatch.setattr(runtime, "ensure_ready", lambda: pytest.fail("duplicate delivery"))
    run(item_id, new_id)


def test_parsing_is_indeterminate_and_heartbeat_is_visible(api, runtime, monkeypatch):
    prefix, item_id, job_id = queued_job(api, monkeypatch)
    def observe(path, **kwargs):
        kwargs["heartbeat"](2)
        with session_factory()() as session:
            metrics = EmbeddingJobRead.model_validate(session.get(ShreckWorldEmbeddingJob, job_id)).metrics
            assert metrics.phase == "parsing" and metrics.progress_mode == "indeterminate"
            assert metrics.total_pages == 2 and metrics.processed_pages == 0
        return parsed_source(path, **kwargs)
    monkeypatch.setattr("app.services.ingestion.parse_source", observe)
    run(item_id, job_id)


def test_duration_uses_utc_and_legacy_jobs_have_unknown_processing_time():
    start = datetime(2026, 10, 15, 12, 0)
    job = EmbeddingJobRead(id="job", library_item_id="item", status="completed", progress=100, detail=None,
                           created_at=start, started_at=start + timedelta(seconds=2), completed_at=start + timedelta(seconds=8))
    assert job.model_dump(mode="json")["started_at"].endswith("Z")
    assert job.elapsed_seconds == 8 and job.queue_wait_seconds == 2 and job.processing_seconds == 6
    legacy = job.model_copy(update={"started_at": None})
    assert legacy.metrics is None and legacy.processing_seconds is None and legacy.queue_wait_seconds is None


def test_schema_upgrade_is_idempotent_and_preserves_historical_jobs(api):
    from app.db.session import _engine, create_schema
    with _engine().begin() as connection:
        connection.execute(text("DROP TABLE shreckworld_embedding_jobs"))
        connection.execute(text("CREATE TABLE shreckworld_embedding_jobs (id VARCHAR(36) PRIMARY KEY, library_item_id VARCHAR(36), status VARCHAR(32), progress INTEGER, detail TEXT, created_at DATETIME, completed_at DATETIME)"))
        connection.execute(text("INSERT INTO shreckworld_embedding_jobs VALUES ('old', 'item', 'completed', 100, 'done', '2026-10-15 12:00:00', '2026-10-15 12:00:06')"))
    create_schema()
    create_schema()
    with session_factory()() as session:
        old = EmbeddingJobRead.model_validate(session.get(ShreckWorldEmbeddingJob, "old"))
        assert old.elapsed_seconds == 6 and old.processing_seconds is None and old.metrics is None


def test_measured_processing_survives_wall_clock_adjustments():
    import json
    start = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    job = EmbeddingJobRead(
        id="job", library_item_id="item", status="completed", progress=100, detail=None,
        created_at=start - timedelta(seconds=2), started_at=start,
        completed_at=start + timedelta(seconds=10), updated_at=start + timedelta(seconds=10),
        metrics_json=json.dumps({"phase": "completed", "phase_seconds": {"parsing": 30, "embedding": 20.5}}),
    )
    assert job.processing_seconds == 50.5
    assert job.elapsed_seconds == 52.5
    # A backward wall-clock jump cannot make recorded work disappear.
    live = job.model_copy(update={"status": "running", "completed_at": None,
                                  "updated_at": datetime.now(timezone.utc) + timedelta(hours=1)})
    assert live.processing_seconds == 50.5
    live = live.model_copy(update={"updated_at": datetime.now(timezone.utc) - timedelta(seconds=3)})
    assert 53.5 <= live.processing_seconds < 54.5
