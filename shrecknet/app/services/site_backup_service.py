from __future__ import annotations

import io
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, status

from app.core.config_store import _default_data_dir, get_settings
from app.db.jobs_session import get_jobs_engine
from app.db.session import get_engine


class SiteBackupService:
    backup_prefix = "shrecknet_backup"
    backup_kind = "shrecknet"

    def __init__(self) -> None:
        settings = get_settings()
        self.media_root = Path(settings.media_root)
        self.data_root = _default_data_dir()

    def build_backup_bytes(self) -> tuple[str, bytes]:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{self.backup_prefix}_{timestamp}.zip"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            self._write_directory(archive, self.media_root, "media")
            self._write_directory(archive, self.data_root, "datasets")
        return filename, buffer.getvalue()

    async def restore_backup_bytes(self, payload: bytes) -> dict[str, object]:
        await get_engine().dispose()
        await get_jobs_engine().dispose()

        with tempfile.TemporaryDirectory(prefix="shrecknet_restore_") as tmp_dir:
            extract_root = Path(tmp_dir)
            self._extract_zip(payload, extract_root)

            media_source = extract_root / "media"
            datasets_source = extract_root / "datasets"
            if not media_source.exists() or not datasets_source.exists():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid shrecknet backup archive structure",
                )

            self._replace_directory_contents(self.media_root, media_source, skip_names={"backups"})
            self._replace_directory_contents(self.data_root, datasets_source)

        return {"status": "success", "backup_kind": self.backup_kind}

    def _write_directory(self, archive: zipfile.ZipFile, source: Path, archive_root: str) -> None:
        if not source.exists():
            archive.writestr(f"{archive_root}/", b"")
            return
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source).as_posix()
            if archive_root == "media" and relative.split("/", 1)[0] == "backups":
                continue
            arcname = f"{archive_root}/{relative}"
            if path.is_dir():
                archive.writestr(f"{arcname}/", b"")
            else:
                archive.write(path, arcname)

    def _extract_zip(self, payload: bytes, destination: Path) -> None:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
            for member in archive.infolist():
                normalized = Path(member.filename)
                if normalized.is_absolute() or ".." in normalized.parts:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Unsafe backup archive entry detected",
                    )
            archive.extractall(destination)

    def _replace_directory_contents(self, target_root: Path, source_root: Path, skip_names: set[str] | None = None) -> None:
        skip_names = skip_names or set()
        target_root.mkdir(parents=True, exist_ok=True)

        for entry in list(target_root.iterdir()):
            if entry.name in skip_names:
                continue
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()

        for entry in sorted(source_root.iterdir()):
            if entry.name in skip_names:
                continue
            destination = target_root / entry.name
            if entry.is_dir():
                shutil.copytree(entry, destination)
            else:
                shutil.copy2(entry, destination)
