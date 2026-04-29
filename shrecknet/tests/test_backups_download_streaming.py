from __future__ import annotations

from pathlib import Path

import pytest

from app.api.deps import get_current_admin_user
from app.main import app
from app.models.user import User
from app.services.backup_service import BackupService


@pytest.mark.asyncio
async def test_backup_download_uses_streaming_without_content_length(client, monkeypatch) -> None:
    app.dependency_overrides[get_current_admin_user] = lambda: User(
        id=1,
        username="admin",
        email="admin@example.com",
        hashed_password="x",
        role="admin",
    )

    backup_path = Path("/tmp/full_backup_test.tar.gz")
    backup_path.write_bytes(b"backup-payload")

    monkeypatch.setattr(BackupService, "get_backup_path", lambda self, _filename: backup_path)

    response = await client.get("/backups/full_backup_test.tar.gz/download")

    assert response.status_code == 200, response.text
    assert response.content == b"backup-payload"
    assert response.headers["content-type"].startswith("application/gzip")
    assert "attachment; filename=\"full_backup_test.tar.gz\"" in response.headers["content-disposition"]
    assert "content-length" not in response.headers

