"""Unit tests for BackupService v2 safety and manifest behavior."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from app.services.backup_service import BackupService


def _make_service_with_temp_dirs(tmp_path: Path) -> BackupService:
    service = BackupService()
    service.media_root = tmp_path / "media"
    service.backup_dir = service.media_root / "backups"
    service.download_backup_dir = service.backup_dir / "download"
    service.uploaded_backup_dir = service.backup_dir / "upload"
    service._ensure_dirs()
    return service


def test_safe_extract_rejects_path_traversal(tmp_path: Path) -> None:
    service = _make_service_with_temp_dirs(tmp_path)
    archive_path = tmp_path / "unsafe.tar.gz"
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive_path, "w:gz") as tar:
        payload = b"malicious"
        info = tarfile.TarInfo(name="../escape.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    with tarfile.open(archive_path, "r:gz") as tar:
        with pytest.raises(ValueError, match="Unsafe archive entry"):
            service._safe_extract_tar(tar, extract_dir)


def test_validate_manifest_requires_file(tmp_path: Path) -> None:
    service = _make_service_with_temp_dirs(tmp_path)
    package_root = tmp_path / "package"
    package_root.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="missing manifest.json"):
        service._validate_manifest(package_root)


def test_validate_manifest_detects_checksum_mismatch(tmp_path: Path) -> None:
    service = _make_service_with_temp_dirs(tmp_path)
    package_root = tmp_path / "package"
    package_root.mkdir(parents=True, exist_ok=True)

    payload_path = package_root / "databases.db"
    payload_path.write_text("hello", encoding="utf-8")

    manifest = {
        "version": 2,
        "backup_kind": "full_system",
        "components": {},
        "files": [
            {
                "path": "databases.db",
                "size_bytes": payload_path.stat().st_size,
                "sha256": "deadbeef",
            }
        ],
    }
    (package_root / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    loaded_manifest = service._validate_manifest(package_root)
    with pytest.raises(ValueError, match="Checksum mismatch"):
        service._validate_manifest_checksums(package_root, loaded_manifest)


def test_copy_media_excludes_backups_folder(tmp_path: Path) -> None:
    service = _make_service_with_temp_dirs(tmp_path)
    source_media = service.media_root
    source_media.mkdir(parents=True, exist_ok=True)
    (source_media / "image.png").write_text("img", encoding="utf-8")
    backups_dir = source_media / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    (backups_dir / "should_not_copy.txt").write_text("skip", encoding="utf-8")

    destination = tmp_path / "pkg" / "media"
    summary = service._copy_media(destination)

    assert summary["files"] == 1
    assert (destination / "image.png").exists()
    assert not (destination / "backups").exists()
