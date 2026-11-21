"""Migration Center is read-only by default and writes only reviewed NULL references."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from test_upload_metadata_api import api


def _worlds(path, token):
    assert path == "/worlds"
    assert token == "shrecknet-token"
    return [
        {"id": "books", "name": "Rulebooks", "ontology_ids": [7], "active_ontology_id": 7},
        {"id": "other", "name": "Other", "ontology_ids": [8], "active_ontology_id": 8},
        {"id": "ambiguous", "name": "Legacy ambiguous", "ontology_ids": [9, 10], "active_ontology_id": None},
    ]


def _seed(world_id: str):
    from app.db.session import session_factory
    from app.models import ShreckWorldLibraryItem
    with session_factory()() as db:
        one = ShreckWorldLibraryItem(
            shreckworld_id=world_id, title="Core", pdf_path="/never-move/core.pdf",
            source_sha256="a" * 64, source_revision=3, embedding_status="ready",
        )
        two = ShreckWorldLibraryItem(
            shreckworld_id=world_id, title="Guide", pdf_path="/never-move/guide.pdf",
            source_sha256="b" * 64,
        )
        db.add_all([one, two])
        db.commit()
        return one.id, two.id


def test_preview_apply_audited_and_idempotent(api, monkeypatch):
    from app.db.session import session_factory
    from app.models import ShreckWorldLibraryItem
    from app.models.migration_center import MigrationCenterAudit
    monkeypatch.setattr("app.services.migration_center.shrecknet_read", _worlds)
    client, headers, legacy_id = api
    ids = _seed(legacy_id)
    path = "/admin/migration-center"
    before = client.get(path + "/overview", headers=headers)
    assert before.status_code == 200, before.text
    assert before.json()["mode"] == "read_only"
    assert before.json()["totals"]["sources"] == 2
    assert before.json()["collections"][0]["status"] == "ready"

    payload = {"collection_id": legacy_id, "target_ontology_id": 7}
    inspected = client.post(path + "/source-preview", json=payload, headers=headers)
    assert inspected.status_code == 200, inspected.text
    view = inspected.json()
    assert view["will_update"] == 2
    assert view["blocked"] is False
    assert len(view["preview_fingerprint"]) == 64
    with session_factory()() as db:
        assert all(db.get(ShreckWorldLibraryItem, sid).ontology_id is None for sid in ids)
        assert not db.query(MigrationCenterAudit).count()

    requested = {**payload, "preview_fingerprint": view["preview_fingerprint"],
                 "confirmation": "MIGRATE SOURCES", "backup_confirmed": True}
    assert client.post(path + "/source-apply", headers=headers,
                       json={**requested, "backup_confirmed": False}).status_code == 422
    assert client.post(path + "/source-apply", headers=headers,
                       json={**requested, "confirmation": "migrate sources"}).status_code == 422

    applied = client.post(path + "/source-apply", headers=headers, json=requested)
    assert applied.status_code == 200, applied.text
    assert applied.json()["changed_items"] == 2
    assert applied.json()["audit_id"]
    with session_factory()() as db:
        data = [db.get(ShreckWorldLibraryItem, sid) for sid in ids]
        assert all(x.ontology_id == 7 for x in data)
        assert data[0].pdf_path == "/never-move/core.pdf"
        assert data[0].source_revision == 3
        assert db.query(MigrationCenterAudit).count() == 1

    # Stale previews cannot be replayed, and fresh previews become no-ops.
    assert client.post(path + "/source-apply", headers=headers, json=requested).status_code == 409
    latest = client.post(path + "/source-preview", json=payload, headers=headers).json()
    assert latest["will_update"] == 0
    done = client.post(path + "/source-apply", headers=headers,
                       json={**requested, "preview_fingerprint": latest["preview_fingerprint"]})
    assert done.status_code == 200
    assert done.json()["changed_items"] == 0
    assert done.json()["audit_id"] is None
    report = client.get(path + "/overview", headers=headers).json()
    assert report["history"][0]["changed_items"] == 2
    assert report["collections"][0]["status"] == "already_migrated"


def test_mapping_conflicts_fail_closed_without_changes(api, monkeypatch):
    from app.db.session import session_factory
    from app.models import ShreckWorldLibraryItem
    monkeypatch.setattr("app.services.migration_center.shrecknet_read", _worlds)
    client, headers, legacy_id = api
    ids = _seed(legacy_id)
    endpoint = "/admin/migration-center/source-preview"
    foreign = client.post(endpoint, headers=headers, json={
        "collection_id": legacy_id, "target_ontology_id": 8
    })
    assert foreign.status_code == 409  # Legacy collection linked to Books, not Other

    with session_factory()() as db:
        db.get(ShreckWorldLibraryItem, ids[1]).ontology_id = 8
        db.commit()
    conflict = client.post(endpoint, headers=headers, json={
        "collection_id": legacy_id, "target_ontology_id": 7
    })
    assert conflict.status_code == 200
    detail = conflict.json()
    assert detail["blocked"] is True
    assert detail["conflicts"][0]["id"] == ids[1]
    assert client.post("/admin/migration-center/source-apply", headers=headers, json={
        "collection_id": legacy_id, "target_ontology_id": 7,
        "preview_fingerprint": detail["preview_fingerprint"],
        "confirmation": "MIGRATE SOURCES", "backup_confirmed": True,
    }).status_code == 409
    with session_factory()() as db:
        assert db.get(ShreckWorldLibraryItem, ids[0]).ontology_id is None


def test_preview_invalidated_by_source_revision_change(api, monkeypatch):
    from app.db.session import session_factory
    from app.models import ShreckWorldLibraryItem
    monkeypatch.setattr("app.services.migration_center.shrecknet_read", _worlds)
    client, headers, legacy_id = api
    sid, _ = _seed(legacy_id)
    path = "/admin/migration-center"
    payload = {"collection_id": legacy_id, "target_ontology_id": 7}
    view = client.post(path + "/source-preview", headers=headers, json=payload).json()
    with session_factory()() as db:
        db.get(ShreckWorldLibraryItem, sid).source_revision += 1
        db.commit()
    response = client.post(path + "/source-apply", headers=headers, json={
        **payload, "preview_fingerprint": view["preview_fingerprint"],
        "confirmation": "MIGRATE SOURCES", "backup_confirmed": True
    })
    assert response.status_code == 409
    with session_factory()() as db:
        assert db.get(ShreckWorldLibraryItem, sid).ontology_id is None
