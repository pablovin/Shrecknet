from __future__ import annotations

from pathlib import Path

import pytest

from app.api.deps import get_current_admin_user
from app.main import app
from app.models.user import User
from app.services.backup_service import BackupService


@pytest.mark.asyncio
async def test_signed_backup_download_flow(client, monkeypatch) -> None:
    app.dependency_overrides[get_current_admin_user] = lambda: User(
        id=1,
        username="admin",
        email="admin@example.com",
        hashed_password="x",
        role="admin",
    )
    backup_path = Path("/tmp/full_backup_signed_test.tar.gz")
    backup_path.write_bytes(b"signed-backup-payload")
    monkeypatch.setattr(BackupService, "get_backup_path", lambda self, _filename: backup_path)

    link_resp = await client.post("/backups/full_backup_signed_test.tar.gz/download-link")
    assert link_resp.status_code == 200, link_resp.text
    url = link_resp.json()["url"]

    download_resp = await client.get(url)
    assert download_resp.status_code == 200, download_resp.text
    assert download_resp.content == b"signed-backup-payload"


@pytest.mark.asyncio
async def test_signed_backup_download_rejects_tampered_signature(client, monkeypatch) -> None:
    app.dependency_overrides[get_current_admin_user] = lambda: User(
        id=1,
        username="admin",
        email="admin@example.com",
        hashed_password="x",
        role="admin",
    )
    backup_path = Path("/tmp/full_backup_signed_test_2.tar.gz")
    backup_path.write_bytes(b"signed-backup-payload")
    monkeypatch.setattr(BackupService, "get_backup_path", lambda self, _filename: backup_path)

    link_resp = await client.post("/backups/full_backup_signed_test_2.tar.gz/download-link")
    assert link_resp.status_code == 200, link_resp.text
    url = link_resp.json()["url"] + "tampered"

    download_resp = await client.get(url)
    assert download_resp.status_code == 401

