from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from app.services.backup.categories import BackupContext, FileCategory, FileSource, names
from app.services.backup.snapshots import BackupBusy, SnapshotError, SnapshotService


def _make_service(tmp_path: Path) -> tuple[SnapshotService, Path, Path]:
    media = tmp_path / "media"
    data = tmp_path / "data"
    (media / "library" / "1").mkdir(parents=True)
    (media / "library" / "1" / "doc.pdf").write_bytes(b"%PDF-original")
    (media / "avatars").mkdir()
    (media / "avatars" / "a.png").write_bytes(b"png-one")
    (media / "backups").mkdir()
    (media / "backups" / "huge.tar").write_bytes(b"x" * 1000)
    data.mkdir()
    with sqlite3.connect(data / "main.db") as conn:
        conn.execute("create table t (v text)")
        conn.execute("insert into t values ('before')")

    categories = {
        "documents": FileCategory(
            id="documents", label="Docs", description="", owner="shrecknet",
            sources=lambda: [FileSource("media", media, names("library"))],
        ),
        "images": FileCategory(
            id="images", label="Images", description="", owner="shrecknet",
            sources=lambda: [FileSource("media", media, names("avatars"))],
        ),
        "db": FileCategory(
            id="db", label="DB", description="", owner="shrecknet", restart_required=True,
            sources=lambda: [FileSource("databases", data, lambda e: e.suffix == ".db")],
        ),
        "gone": FileCategory(
            id="gone", label="Gone", description="", owner="x",
            sources=lambda: [], requires=lambda: "not mounted",
        ),
    }
    return SnapshotService(root=tmp_path / "backups", categories=categories), media, data


@pytest.mark.asyncio
async def test_estimates_report_size_per_category(tmp_path: Path) -> None:
    service, _media, _data = _make_service(tmp_path)
    described = {item["id"]: item for item in await service.describe_categories(BackupContext())}
    assert described["documents"]["size_bytes"] == len(b"%PDF-original")
    assert described["images"]["file_count"] == 1
    assert described["gone"]["available"] is False
    assert described["gone"]["default_selected"] is False


@pytest.mark.asyncio
async def test_create_only_selected_categories_and_skip_backups_dir(tmp_path: Path) -> None:
    service, _media, _data = _make_service(tmp_path)
    summary = await service.create_snapshot(["documents"], BackupContext())
    assert list(summary["categories"]) == ["documents"]
    snapshot = service.snapshot_path(summary["id"])
    assert (snapshot / "documents" / "media" / "library" / "1" / "doc.pdf").read_bytes() == b"%PDF-original"
    assert not (snapshot / "images").exists()
    assert not list(snapshot.rglob("huge.tar"))


@pytest.mark.asyncio
async def test_restore_replaces_files_and_removes_extras(tmp_path: Path) -> None:
    service, media, _data = _make_service(tmp_path)
    summary = await service.create_snapshot(["documents", "images"], BackupContext())

    (media / "library" / "1" / "doc.pdf").write_bytes(b"corrupted")
    (media / "avatars" / "extra.png").write_bytes(b"new")

    result = await service.restore_snapshot(summary["id"], ["documents"], BackupContext(), safety_snapshot=False)

    assert result["restored_categories"] == ["documents"]
    assert (media / "library" / "1" / "doc.pdf").read_bytes() == b"%PDF-original"
    assert (media / "avatars" / "extra.png").exists()  # images was not selected
    await service.restore_snapshot(summary["id"], ["images"], BackupContext(), safety_snapshot=False)
    assert not (media / "avatars" / "extra.png").exists()
    assert (media / "backups" / "huge.tar").exists()


@pytest.mark.asyncio
async def test_sqlite_is_snapshotted_consistently_and_restored(tmp_path: Path) -> None:
    service, _media, data = _make_service(tmp_path)
    summary = await service.create_snapshot(["db"], BackupContext())
    with sqlite3.connect(data / "main.db") as conn:
        conn.execute("update t set v = 'after'")
    (data / "main.db-wal").write_bytes(b"stale")

    result = await service.restore_snapshot(summary["id"], ["db"], BackupContext())

    assert result["restart_required"] is True
    with sqlite3.connect(data / "main.db") as conn:
        assert conn.execute("select v from t").fetchone() == ("before",)
    assert not (data / "main.db-wal").exists()
    assert result["safety_snapshot_id"] in {item["id"] for item in service.list_snapshots()}


@pytest.mark.asyncio
async def test_incremental_snapshot_hardlinks_unchanged_files(tmp_path: Path) -> None:
    service, media, _data = _make_service(tmp_path)
    first = await service.create_snapshot(["documents"], BackupContext())
    (media / "library" / "2").mkdir()
    (media / "library" / "2" / "new.pdf").write_bytes(b"new")
    second = await service.create_snapshot(["documents"], BackupContext())

    unchanged = Path("documents/media/library/1/doc.pdf")
    first_file = service.snapshot_path(first["id"]) / unchanged
    second_file = service.snapshot_path(second["id"]) / unchanged
    assert second["base_snapshot"] == first["id"]
    assert os.stat(first_file).st_ino == os.stat(second_file).st_ino

    service.delete_snapshot(first["id"])
    assert second_file.read_bytes() == b"%PDF-original"


@pytest.mark.asyncio
async def test_restore_rejects_corrupt_snapshot_before_touching_data(tmp_path: Path) -> None:
    service, media, _data = _make_service(tmp_path)
    summary = await service.create_snapshot(["documents"], BackupContext())
    stored = service.snapshot_path(summary["id"]) / "documents/media/library/1/doc.pdf"
    stored.write_bytes(b"tampered-with-different-size")
    (media / "library" / "1" / "doc.pdf").write_bytes(b"live")

    with pytest.raises(SnapshotError):
        await service.restore_snapshot(summary["id"], ["documents"], BackupContext(), safety_snapshot=False)
    assert (media / "library" / "1" / "doc.pdf").read_bytes() == b"live"


@pytest.mark.asyncio
async def test_unknown_unavailable_and_traversal_are_rejected(tmp_path: Path) -> None:
    service, _media, _data = _make_service(tmp_path)
    with pytest.raises(SnapshotError):
        await service.create_snapshot(["nope"], BackupContext())
    with pytest.raises(SnapshotError):
        await service.create_snapshot(["gone"], BackupContext())
    with pytest.raises(SnapshotError):
        service.snapshot_path("../etc")
    summary = await service.create_snapshot(["documents"], BackupContext())
    with pytest.raises(SnapshotError):
        await service.restore_snapshot(summary["id"], ["images"], BackupContext())


@pytest.mark.asyncio
async def test_tar_export_round_trips_through_import(tmp_path: Path) -> None:
    service, _media, _data = _make_service(tmp_path)
    summary = await service.create_snapshot(["documents", "db"], BackupContext())
    archive = tmp_path / "export.tar"
    archive.write_bytes(b"".join(service.iter_tar(summary["id"])))

    service.delete_snapshot(summary["id"])
    imported = service.import_tar(archive)

    assert imported["id"] == summary["id"]
    service.verify(imported["id"], ["documents", "db"])


def test_import_rejects_non_snapshot_archives(tmp_path: Path) -> None:
    import tarfile

    service, _media, _data = _make_service(tmp_path)
    bad = tmp_path / "bad.tar"
    with tarfile.open(bad, "w") as tar:
        payload = tmp_path / "x"
        payload.write_text("x")
        tar.add(payload, arcname="../evil")
    with pytest.raises(SnapshotError):
        service.import_tar(bad)


def test_only_one_operation_at_a_time(tmp_path: Path) -> None:
    service, _media, _data = _make_service(tmp_path)
    with service.exclusive():
        with pytest.raises(BackupBusy):
            with SnapshotService(root=service.root, categories={}).exclusive():
                pass


def test_retention_keeps_newest(tmp_path: Path) -> None:
    import asyncio

    service, _media, _data = _make_service(tmp_path)
    ids = [asyncio.run(service.create_snapshot(["images"], BackupContext()))["id"] for _ in range(3)]
    removed = service.apply_retention(1)
    assert set(removed) == set(ids[:2]) or len(removed) == 2
    assert len(service.list_snapshots()) == 1
