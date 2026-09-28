from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("SHRECKWORLD_DATABASE_URL", f"sqlite:///{tmp_path / 'shreckworld.db'}")
    monkeypatch.setenv("SHRECKWORLD_MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("SHRECKWORLD_ADMIN_TOKEN", "test-token")
    monkeypatch.setenv("SHRECKWORLD_CELERY_TASK_ALWAYS_EAGER", "true")
    from app.main import app
    return TestClient(app), {"X-ShreckWorld-Admin-Token": "test-token"}


def test_admin_creates_and_configures_a_shreckworld(monkeypatch, tmp_path):
    client, headers = _client(monkeypatch, tmp_path)
    forbidden = client.post("/admin/shreckworlds", json={"name": "Arthur 513"})
    assert forbidden.status_code == 403
    created = client.post("/admin/shreckworlds", headers=headers, json={"name": "Arthur 513"})
    assert created.status_code == 201
    world = created.json()
    invalid = client.patch(
        f"/admin/shreckworlds/{world['id']}", headers=headers,
        json={"web_search_enabled": False, "web_search_policy": "fallback"},
    )
    assert invalid.status_code == 422


def test_query_isolated_to_its_shreckworld(monkeypatch, tmp_path):
    client, headers = _client(monkeypatch, tmp_path)
    one = client.post("/admin/shreckworlds", headers=headers, json={"name": "Pendragon"}).json()
    two = client.post("/admin/shreckworlds", headers=headers, json={"name": "Vampire"}).json()
    from app.db.session import session_factory
    from app.models import ShreckWorldDocumentChunk, ShreckWorldLibraryItem
    session = session_factory()()
    try:
        pendragon_item = ShreckWorldLibraryItem(shreckworld_id=one["id"], title="Pendragon Core", authority_tier="core_rules", authority_priority=10, pdf_path="/tmp/pendragon.pdf", source_sha256="a" * 64, embedding_status="ready")
        vampire_item = ShreckWorldLibraryItem(shreckworld_id=two["id"], title="Vampire Core", authority_tier="core_rules", authority_priority=10, pdf_path="/tmp/vampire.pdf", source_sha256="b" * 64, embedding_status="ready")
        session.add_all([pendragon_item, vampire_item]); session.flush()
        session.add_all([
            ShreckWorldDocumentChunk(library_item_id=pendragon_item.id, page_number=8, ordinal=0, text="Mounted combat uses Horsemanship.", embedding_json=json.dumps([1.0, 0.0])),
            ShreckWorldDocumentChunk(library_item_id=vampire_item.id, page_number=9, ordinal=0, text="Vampires use Hunger dice.", embedding_json=json.dumps([0.0, 1.0])),
        ])
        session.commit()
        pendragon_item_id = pendragon_item.id
    finally:
        session.close()

    class Embedder:
        def embed(self, text, *, query=False):
            return [1.0, 0.0]

    monkeypatch.setattr("app.services.query.get_embedder", lambda: Embedder())
    response = client.post(f"/shreckworlds/{one['id']}/query", headers=headers, json={"query": "mounted combat", "include_trace": True})
    assert response.status_code == 200
    payload = response.json()
    assert payload["citations"][0]["title"] == "Pendragon Core"
    assert {citation["library_item_id"] for citation in payload["citations"]} == {pendragon_item_id}
