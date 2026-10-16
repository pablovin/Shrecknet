from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("SHRECKWORLD_DATABASE_URL", f"sqlite:///{tmp_path / 'shreckworld.db'}")
    monkeypatch.setenv("SHRECKWORLD_MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("SHRECKWORLD_CELERY_TASK_ALWAYS_EAGER", "true")
    monkeypatch.setenv("SHRECKWORLD_RERANKER_ENABLED", "false")
    from app.db.session import create_schema
    from app.main import app
    from app.api.deps import ShrecknetPrincipal, get_current_user, require_world_manager
    create_schema()
    principal = ShrecknetPrincipal(user_id="1", role="admin")
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[require_world_manager] = lambda: principal
    return TestClient(app), {"Authorization": "Bearer shrecknet-token"}


def test_admin_creates_and_configures_a_shreckworld(monkeypatch, tmp_path):
    client, headers = _client(monkeypatch, tmp_path)
    created = client.post("/admin/shreckworlds", headers=headers, json={"name": "Arthur 513", "shrecknet_world_id": "arthur-513"})
    assert created.status_code == 201
    world = created.json()
    invalid = client.patch(
        f"/admin/shreckworlds/{world['id']}", headers=headers,
        json={"web_search_enabled": False, "web_search_policy": "fallback"},
    )
    assert invalid.status_code == 422


def test_query_isolated_to_its_shreckworld(monkeypatch, tmp_path):
    client, headers = _client(monkeypatch, tmp_path)
    one = client.post("/admin/shreckworlds", headers=headers, json={"name": "Pendragon", "shrecknet_world_id": "pendragon"}).json()
    two = client.post("/admin/shreckworlds", headers=headers, json={"name": "Vampire", "shrecknet_world_id": "vampire"}).json()
    from app.db.session import session_factory
    from app.models import ShreckWorldDocument, ShreckWorldDocumentChunk, ShreckWorldLibraryItem
    session = session_factory()()
    try:
        pendragon_item = ShreckWorldLibraryItem(shreckworld_id=one["id"], title="Pendragon Core", authority_tier="core_rules", authority_priority=10, pdf_path="/tmp/pendragon.pdf", source_sha256="a" * 64, embedding_status="ready")
        vampire_item = ShreckWorldLibraryItem(shreckworld_id=two["id"], title="Vampire Core", authority_tier="core_rules", authority_priority=10, pdf_path="/tmp/vampire.pdf", source_sha256="b" * 64, embedding_status="ready")
        session.add_all([pendragon_item, vampire_item]); session.flush()
        docs = []
        for item in (pendragon_item, vampire_item):
            docs.append(ShreckWorldDocument(id=str(uuid4()), library_item_id=item.id, is_active=True,
                source_sha256=item.source_sha256, parser_name="docling", parser_version="test",
                embedding_version="docling-e5-context-v1", embedding_model="test-e5", embedding_fingerprint="a" * 64,
                embedding_dimension=2, manifest_path="/tmp/test-manifest"))
        session.add_all(docs); session.flush()
        session.add_all([
            ShreckWorldDocumentChunk(document_id=docs[0].id,library_item_id=pendragon_item.id, page_number=8, ordinal=0, text="Mounted combat uses Horsemanship.", embedding_json=json.dumps([1.0, 0.0])),
            ShreckWorldDocumentChunk(document_id=docs[1].id, library_item_id=vampire_item.id, page_number=9, ordinal=0, text="Vampires use Hunger dice.", embedding_json=json.dumps([0.0, 1.0])),
        ])
        session.commit()
        pendragon_item_id = pendragon_item.id
    finally:
        session.close()

    class Embedder:
        settings = SimpleNamespace(embedding_model_id="test-e5")
        def ensure_ready(self):
            return {"fingerprint": "a" * 64}
        def embed(self, text, *, query=False):
            return [1.0, 0.0]

    monkeypatch.setattr("app.services.query.get_embedder", lambda: Embedder())
    def unavailable(**kwargs):
        raise RuntimeError("Qdrant offline in unit test")
    monkeypatch.setattr("app.services.query.search_chunks", unavailable)
    response = client.post(f"/shreckworlds/{one['id']}/query", headers=headers, json={"query": "mounted combat", "include_trace": True})
    assert response.status_code == 200
    payload = response.json()
    assert payload["citations"][0]["title"] == "Pendragon Core"
    assert {citation["library_item_id"] for citation in payload["citations"]} == {pendragon_item_id}
