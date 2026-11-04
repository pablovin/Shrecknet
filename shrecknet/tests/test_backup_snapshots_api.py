from __future__ import annotations

import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.api.deps import get_current_admin_user
from app.api.routers import backup_snapshots
from app.graph.neo4j import get_optional_neo4j_session
from app.main import app
from app.models.user import User
from app.services.backup.categories import BackupContext, QdrantEmbeddingsCategory, build_categories
from app.services.backup.snapshots import SnapshotService
from test_backup_snapshots import _make_service


@pytest.fixture()
def admin_override():
    app.dependency_overrides[get_current_admin_user] = lambda: User(
        id=1, username="admin", email="a@example.com", hashed_password="x", role="admin"
    )

    async def no_neo4j():
        yield None

    app.dependency_overrides[get_optional_neo4j_session] = no_neo4j
    yield
    app.dependency_overrides.pop(get_current_admin_user, None)
    app.dependency_overrides.pop(get_optional_neo4j_session, None)


@pytest.fixture()
def service(tmp_path: Path, monkeypatch) -> SnapshotService:
    svc, _media, _data = _make_service(tmp_path)
    monkeypatch.setattr(backup_snapshots, "SnapshotService", lambda: svc)
    return svc


@pytest.mark.asyncio
async def test_categories_endpoint_lists_sizes_and_disk(client, admin_override, service) -> None:
    response = await client.get("/backups/categories")
    assert response.status_code == 200, response.text
    body = response.json()
    by_id = {item["id"]: item for item in body["categories"]}
    assert by_id["documents"]["size_bytes"] > 0
    assert by_id["gone"]["available"] is False
    assert body["disk"]["free_bytes"] > 0
    assert body["backup_root"] == str(service.root)


@pytest.mark.asyncio
async def test_create_queues_job_and_validates_selection(client, admin_override, service, monkeypatch) -> None:
    queued = {}
    monkeypatch.setattr(
        backup_snapshots.create_snapshot_task,
        "delay",
        lambda **kwargs: queued.update(kwargs) or SimpleNamespace(id="task-1"),
    )
    ok = await client.post("/backups/snapshots", json={"categories": ["documents", "images"], "incremental": False})
    assert ok.status_code == 202, ok.text
    assert queued["categories"] == ["documents", "images"] and queued["incremental"] is False

    assert (await client.post("/backups/snapshots", json={"categories": ["nope"]})).status_code == 400
    assert (await client.post("/backups/snapshots", json={"categories": ["gone"]})).status_code == 400
    assert (await client.post("/backups/snapshots", json={"categories": []})).status_code == 422


@pytest.mark.asyncio
async def test_list_download_import_delete_round_trip(client, admin_override, service, monkeypatch) -> None:
    summary = await service.create_snapshot(["documents"], BackupContext())

    listed = await client.get("/backups/snapshots")
    assert [item["id"] for item in listed.json()] == [summary["id"]]

    download = await client.get(f"/backups/snapshots/{summary['id']}/download")
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/x-tar"
    assert "content-length" not in download.headers
    with tarfile.open(fileobj=io.BytesIO(download.content)) as tar:
        assert f"{summary['id']}/manifest.json" in tar.getnames()

    link = await client.post(f"/backups/snapshots/{summary['id']}/download-link")
    signed = await client.get(link.json()["url"])
    assert signed.status_code == 200 and signed.content == download.content
    assert (await client.get(link.json()["url"].replace("sig=", "sig=0"))).status_code == 401

    assert (await client.delete(f"/backups/snapshots/{summary['id']}")).status_code == 200
    assert (await client.get(f"/backups/snapshots/{summary['id']}")).status_code == 404

    queued = {}
    monkeypatch.setattr(
        backup_snapshots.import_snapshot_task,
        "delay",
        lambda **kwargs: queued.update(kwargs) or SimpleNamespace(id="import-task"),
    )
    imported = await client.post(
        "/backups/snapshots/import?filename=x.tar",
        content=download.content,
        headers={"content-type": "application/octet-stream"},
    )
    assert imported.status_code == 202, imported.text
    assert imported.json()["received_bytes"] == len(download.content)
    staged = Path(queued["upload_path"])
    assert staged.parent == service.root and staged.read_bytes() == download.content
    assert queued["original_name"] == "x.tar" and queued["admin_user_id"] == 1

    # The queued job (run inline here) registers the snapshot from the staged upload.
    registered = service.import_tar(staged, on_progress=lambda *_: None)
    assert registered["id"] == summary["id"]
    staged.unlink()
    bad = await client.post("/backups/snapshots/import?filename=x.zip", content=b"zip")
    assert bad.status_code == 400
    empty = await client.post("/backups/snapshots/import?filename=x.tar", content=b"")
    assert empty.status_code == 400
    assert not list(service.root.glob(".upload-*"))


@pytest.mark.asyncio
async def test_restore_validates_categories_before_queueing(client, admin_override, service, monkeypatch) -> None:
    summary = await service.create_snapshot(["documents"], BackupContext())
    queued = {}
    monkeypatch.setattr(
        backup_snapshots.restore_snapshot_task,
        "delay",
        lambda **kwargs: queued.update(kwargs) or SimpleNamespace(id="task-2"),
    )
    ok = await client.post(f"/backups/snapshots/{summary['id']}/restore", json={"categories": ["documents"]})
    assert ok.status_code == 202 and queued["safety_snapshot"] is True
    bad = await client.post(f"/backups/snapshots/{summary['id']}/restore", json={"categories": ["images"]})
    assert bad.status_code == 400
    missing = await client.post("/backups/snapshots/snapshot_20200101_000000/restore", json={"categories": ["documents"]})
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_snapshot_endpoints_require_admin(client) -> None:
    app.dependency_overrides.pop(get_current_admin_user, None)
    assert (await client.get("/backups/snapshots")).status_code in {401, 403}
    assert (await client.get("/backups/categories")).status_code in {401, 403}


@pytest.mark.asyncio
async def test_qdrant_category_round_trips_snapshot_files(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        path = request.url.path
        if path == "/collections":
            return httpx.Response(200, json={"result": {"collections": [{"name": "worlds"}]}})
        if request.method == "POST" and path == "/collections/worlds/snapshots":
            return httpx.Response(200, json={"result": {"name": "s1"}})
        if request.method == "GET" and path == "/collections/worlds/snapshots/s1":
            return httpx.Response(200, content=b"vector-bytes")
        if request.method == "DELETE":
            return httpx.Response(200, json={"result": True})
        if path == "/collections/worlds/snapshots/upload":
            assert request.url.params["priority"] == "snapshot"
            assert b"vector-bytes" in request.content
            return httpx.Response(200, json={"result": True})
        return httpx.Response(404)

    category = QdrantEmbeddingsCategory(lambda: SimpleNamespace(qdrant_url="http://qdrant:6333"))
    monkeypatch.setattr(
        category,
        "_client",
        lambda: httpx.AsyncClient(base_url="http://qdrant:6333", transport=httpx.MockTransport(handler)),
    )
    result = await category.export(tmp_path / "embeddings", BackupContext())
    assert (tmp_path / "embeddings" / "worlds.snapshot").read_bytes() == b"vector-bytes"
    assert result.details == {"collections": ["worlds"]}
    assert ("DELETE", "/collections/worlds/snapshots/s1") in calls

    restored = await category.restore(tmp_path / "embeddings", BackupContext())
    assert restored == {"collections": ["worlds"]}


def test_registry_groups_dependent_data_into_bundles() -> None:
    categories = {category.id: category for category in build_categories()}
    assert set(categories) == {
        "shrecknet_worlds", "shreckworlds", "shreckrpg", "shrecknet_config", "shreckllm_config", "secrets",
    }
    assert [part.id for part in categories["shrecknet_worlds"].parts] == [
        "shrecknet_db", "graph", "documents", "images", "companion",
    ]
    assert [part.id for part in categories["shreckworlds"].parts] == ["shreckworld_files", "embeddings"]
    assert categories["secrets"].sensitive and not categories["secrets"].default_selected
