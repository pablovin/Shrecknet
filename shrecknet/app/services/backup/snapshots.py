"""Folder-based backup snapshots: create, list, verify, restore, delete, export and import."""

from __future__ import annotations

import fcntl
import asyncio
import inspect
import json
import logging
import re
import shutil
import tarfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from app.core.config_store import get_settings
from app.services.backup.categories import (
    BackupCategory,
    BackupContext,
    Estimate,
    ProgressCallback,
    category_map,
)
from app.services.backup.fileops import FileRecord, remove_path, sha256_file

logger = logging.getLogger(__name__)

SNAPSHOT_FORMAT = "shrecknet-snapshot"
SNAPSHOT_VERSION = 3
SNAPSHOT_ID_PATTERN = re.compile(r"^snapshot_\d{8}_\d{6}(_\d+)?$")
MANIFEST_NAME = "manifest.json"
INDEX_DIR = ".index"
SAFETY_LABEL = "pre-restore safety snapshot"


class SnapshotError(Exception):
    """Raised for user-correctable snapshot problems (bad id, bad category, corrupt data)."""


class SnapshotNotFound(SnapshotError):
    pass


class BackupBusy(SnapshotError):
    pass


def resolve_backup_root() -> Path:
    settings = get_settings()
    root = Path(settings.backup_root) if settings.backup_root else Path(settings.media_root) / "backups" / "snapshots"
    root.mkdir(parents=True, exist_ok=True)
    return root


async def _report(callback: ProgressCallback | None, phase: str, progress: float, status: str) -> None:
    if callback is None:
        return
    result = callback(phase, progress, status)
    if inspect.isawaitable(result):
        await result


class SnapshotService:
    def __init__(self, root: Path | None = None, categories: dict[str, BackupCategory] | None = None) -> None:
        self.root = root or resolve_backup_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.categories = categories if categories is not None else category_map()

    # ---- discovery -------------------------------------------------------------------

    async def describe_categories(self, ctx: BackupContext) -> list[dict[str, Any]]:
        async def one(category: BackupCategory) -> dict[str, Any]:
            reason = category.unavailable_reason()
            estimate = Estimate(0, 0) if reason else await category.estimate(ctx)
            return {
                "id": category.id,
                "label": category.label,
                "description": category.description,
                "owner": category.owner,
                "sensitive": category.sensitive,
                "default_selected": category.default_selected and reason is None,
                "restart_required": category.restart_required,
                "available": reason is None,
                "unavailable_reason": reason,
                "size_bytes": estimate.size_bytes,
                "file_count": estimate.file_count,
                "approximate": estimate.approximate,
            }

        return list(await asyncio.gather(*(one(category) for category in self.categories.values())))

    def disk_usage(self) -> dict[str, int]:
        usage = shutil.disk_usage(self.root)
        return {"total_bytes": usage.total, "free_bytes": usage.free}

    # ---- storage ---------------------------------------------------------------------

    def snapshot_path(self, snapshot_id: str) -> Path:
        if not SNAPSHOT_ID_PATTERN.match(snapshot_id):
            raise SnapshotNotFound(f"Invalid snapshot id: {snapshot_id}")
        path = self.root / snapshot_id
        if not (path / MANIFEST_NAME).is_file():
            raise SnapshotNotFound(f"Snapshot not found: {snapshot_id}")
        return path

    def read_manifest(self, snapshot_id: str) -> dict[str, Any]:
        with open(self.snapshot_path(snapshot_id) / MANIFEST_NAME, encoding="utf-8") as handle:
            manifest = json.load(handle)
        if manifest.get("format") != SNAPSHOT_FORMAT:
            raise SnapshotError("Unsupported snapshot format")
        return manifest

    def read_index(self, snapshot_id: str, category_id: str) -> dict[str, FileRecord]:
        index_path = self.snapshot_path(snapshot_id) / INDEX_DIR / f"{category_id}.json"
        if not index_path.is_file():
            return {}
        with open(index_path, encoding="utf-8") as handle:
            return {item["path"]: FileRecord(**item) for item in json.load(handle)}

    def list_snapshots(self) -> list[dict[str, Any]]:
        snapshots: list[dict[str, Any]] = []
        for path in sorted(self.root.iterdir(), reverse=True):
            if not SNAPSHOT_ID_PATTERN.match(path.name) or not (path / MANIFEST_NAME).is_file():
                continue
            try:
                manifest = self.read_manifest(path.name)
            except (OSError, ValueError, SnapshotError):
                logger.warning("Skipping unreadable snapshot %s", path.name)
                continue
            snapshots.append(self._summary(manifest))
        return snapshots

    @staticmethod
    def _summary(manifest: dict[str, Any]) -> dict[str, Any]:
        categories = manifest.get("categories", {})
        return {
            "id": manifest["id"],
            "label": manifest.get("label"),
            "created_at": manifest["created_at"],
            "incremental": manifest.get("incremental", False),
            "base_snapshot": manifest.get("base_snapshot"),
            "size_bytes": sum(item["size_bytes"] for item in categories.values()),
            "categories": {
                key: {
                    "label": item.get("label"),
                    "size_bytes": item["size_bytes"],
                    "file_count": item["file_count"],
                    "restart_required": item.get("restart_required", False),
                    "sensitive": item.get("sensitive", False),
                }
                for key, item in categories.items()
            },
        }

    def delete_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        path = self.snapshot_path(snapshot_id)
        remove_path(path)
        return {"status": "deleted", "id": snapshot_id}

    def apply_retention(self, keep: int) -> list[str]:
        if keep <= 0:
            return []
        removed: list[str] = []
        for summary in self.list_snapshots()[keep:]:
            removed.append(self.delete_snapshot(summary["id"])["id"])
        return removed

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Only one create/restore may touch the backup root at a time."""
        lock_path = self.root / ".lock"
        handle = open(lock_path, "w")
        try:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise BackupBusy("Another backup or restore is already running") from exc
            yield
        finally:
            handle.close()

    # ---- create ----------------------------------------------------------------------

    def _new_snapshot_id(self) -> str:
        base = f"snapshot_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        candidate = base
        suffix = 1
        while (self.root / candidate).exists():
            suffix += 1
            candidate = f"{base}_{suffix}"
        return candidate

    def _latest_snapshot_id(self) -> str | None:
        snapshots = self.list_snapshots()
        return snapshots[0]["id"] if snapshots else None

    def _resolve_selection(self, selected: list[str]) -> list[BackupCategory]:
        if not selected:
            raise SnapshotError("Select at least one category")
        chosen: list[BackupCategory] = []
        for category_id in dict.fromkeys(selected):
            category = self.categories.get(category_id)
            if category is None:
                raise SnapshotError(f"Unknown backup category: {category_id}")
            reason = category.unavailable_reason()
            if reason:
                raise SnapshotError(f"Category '{category_id}' is unavailable: {reason}")
            chosen.append(category)
        return chosen

    @staticmethod
    def _note_for(progress: ProgressCallback | None, category_id: str, heading: str, start: float, span: float):
        async def note(text: str, fraction: float | None = None) -> None:
            await _report(progress, f"category:{category_id}", start + span * (fraction or 0), f"{heading} - {text}")

        return note

    async def create_snapshot(
        self,
        selected: list[str],
        ctx: BackupContext,
        *,
        incremental: bool = True,
        label: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        chosen = self._resolve_selection(selected)
        snapshot_id = self._new_snapshot_id()
        staging = self.root / f".tmp-{snapshot_id}"
        final = self.root / snapshot_id
        base_id = self._latest_snapshot_id() if incremental else None
        remove_path(staging)
        (staging / INDEX_DIR).mkdir(parents=True)
        manifest_categories: dict[str, Any] = {}

        try:
            for position, category in enumerate(chosen):
                start = 0.05 + 0.85 * position / len(chosen)
                heading = f"Backing up {category.label} ({position + 1}/{len(chosen)})"
                await _report(progress, f"category:{category.id}", start, heading)
                ctx.note = self._note_for(progress, category.id, heading, start, 0.85 / len(chosen))
                ctx.previous_records = {}
                ctx.previous_root = None
                if base_id and base_id != snapshot_id:
                    previous_index = self.read_index(base_id, category.id)
                    if previous_index:
                        ctx.previous_records = previous_index
                        ctx.previous_root = self.snapshot_path(base_id) / category.id
                result = await category.export(staging / category.id, ctx)
                with open(staging / INDEX_DIR / f"{category.id}.json", "w", encoding="utf-8") as handle:
                    json.dump([record.to_dict() for record in result.records], handle)
                manifest_categories[category.id] = {
                    "label": category.label,
                    "owner": category.owner,
                    "sensitive": category.sensitive,
                    "restart_required": category.restart_required,
                    "size_bytes": sum(record.size_bytes for record in result.records),
                    "file_count": len(result.records),
                    "details": result.details,
                }

            manifest = {
                "format": SNAPSHOT_FORMAT,
                "version": SNAPSHOT_VERSION,
                "id": snapshot_id,
                "label": label,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "incremental": bool(base_id),
                "base_snapshot": base_id,
                "categories": manifest_categories,
            }
            with open(staging / MANIFEST_NAME, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle, indent=2)
            staging.rename(final)
        except BaseException:
            remove_path(staging)
            raise

        await _report(progress, "finalizing", 0.98, "Snapshot written")
        return self._summary(manifest)

    # ---- verify / restore ------------------------------------------------------------

    def verify(self, snapshot_id: str, category_ids: list[str]) -> None:
        """Check that every indexed file exists with the recorded size (and hash when known)."""
        base = self.snapshot_path(snapshot_id)
        for category_id in category_ids:
            for relative, record in self.read_index(snapshot_id, category_id).items():
                path = base / category_id / relative
                if not path.is_file() or path.stat().st_size != record.size_bytes:
                    raise SnapshotError(f"Snapshot is corrupt: {category_id}/{relative} is missing or has the wrong size")
                if record.sha256 and sha256_file(path) != record.sha256:
                    raise SnapshotError(f"Snapshot is corrupt: checksum mismatch for {category_id}/{relative}")

    async def restore_snapshot(
        self,
        snapshot_id: str,
        selected: list[str],
        ctx: BackupContext,
        *,
        safety_snapshot: bool = True,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        manifest = self.read_manifest(snapshot_id)
        if not selected:
            raise SnapshotError("Select at least one category to restore")
        missing = [item for item in selected if item not in manifest["categories"]]
        if missing:
            raise SnapshotError(f"Snapshot does not contain: {', '.join(missing)}")
        chosen = self._resolve_selection(selected)

        await _report(progress, "verifying", 0.05, "Verifying snapshot integrity")
        await asyncio.to_thread(self.verify, snapshot_id, [category.id for category in chosen])

        safety_id = None
        if safety_snapshot:
            await _report(progress, "safety", 0.15, "Saving a safety snapshot of the current data")
            safety = await self.create_snapshot([category.id for category in chosen], ctx, incremental=True, label=SAFETY_LABEL)
            safety_id = safety["id"]

        base = self.snapshot_path(snapshot_id)
        summary: dict[str, Any] = {}
        for position, category in enumerate(chosen):
            heading = f"Restoring {category.label} ({position + 1}/{len(chosen)})"
            start = 0.3 + 0.65 * position / len(chosen)
            await _report(progress, f"category:{category.id}", start, heading)
            ctx.note = self._note_for(progress, category.id, heading, start, 0.65 / len(chosen))
            ctx.previous_records, ctx.previous_root = {}, None
            summary[category.id] = await category.restore(base / category.id, ctx)

        return {
            "status": "success",
            "snapshot_id": snapshot_id,
            "restored_categories": [category.id for category in chosen],
            "safety_snapshot_id": safety_id,
            "restart_required": any(category.restart_required for category in chosen),
            "restored_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
        }

    # ---- export / import -------------------------------------------------------------

    def iter_tar(self, snapshot_id: str) -> Iterator[bytes]:
        """Stream an uncompressed tar of a snapshot without staging it on disk."""
        base = self.snapshot_path(snapshot_id)
        for path in sorted(base.rglob("*")):
            relative = f"{snapshot_id}/{path.relative_to(base).as_posix()}"
            info = tarfile.TarInfo(relative)
            stat = path.stat()
            info.mtime = int(stat.st_mtime)
            if path.is_dir():
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                yield info.tobuf(tarfile.PAX_FORMAT)
                continue
            info.size = stat.st_size
            info.mode = 0o644
            yield info.tobuf(tarfile.PAX_FORMAT)
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    yield chunk
            padding = (-info.size) % tarfile.BLOCKSIZE
            if padding:
                yield b"\0" * padding
        yield b"\0" * (tarfile.BLOCKSIZE * 2)

    def import_tar(
        self, archive: Path, on_progress: Callable[[str, float, str], None] | None = None
    ) -> dict[str, Any]:
        """Register an exported snapshot tar as a snapshot in the backup root.

        on_progress(phase, fraction 0..1, text) is called synchronously between steps.
        """

        def notify(phase: str, fraction: float, text: str) -> None:
            if on_progress is not None:
                on_progress(phase, fraction, text)

        staging = self.root / f".import-{datetime.now(timezone.utc).strftime('%H%M%S%f')}"
        remove_path(staging)
        staging.mkdir(parents=True)
        try:
            try:
                with tarfile.open(archive, "r:") as tar:
                    members = tar.getmembers()
                    roots = {Path(member.name).parts[0] for member in members if member.name}
                    if len(roots) != 1:
                        raise SnapshotError("Invalid snapshot archive: expected a single snapshot folder")
                    snapshot_id = roots.pop()
                    if not SNAPSHOT_ID_PATTERN.match(snapshot_id):
                        raise SnapshotError("Invalid snapshot archive: unrecognised snapshot id")
                    for member in members:
                        parts = Path(member.name).parts
                        if Path(member.name).is_absolute() or ".." in parts or not (member.isfile() or member.isdir()):
                            raise SnapshotError("Unsafe entry in snapshot archive")
                    total = sum(member.size for member in members) or 1
                    done = 0
                    step = max(total // 100, 1)
                    next_report = step
                    notify("extracting", 0.05, "Extracting archive")
                    for member in members:
                        tar.extract(member, staging, filter="data")
                        done += member.size
                        if done >= next_report:
                            next_report = done + step
                            notify("extracting", 0.05 + 0.65 * done / total, f"Extracting archive ({done * 100 // total}%)")
            except tarfile.TarError as exc:
                raise SnapshotError("Invalid snapshot archive. Expected an uncompressed .tar export") from exc
            extracted = staging / snapshot_id
            if not (extracted / MANIFEST_NAME).is_file():
                raise SnapshotError("Invalid snapshot archive: manifest.json is missing")
            target = self.root / snapshot_id
            if target.exists():
                raise SnapshotError(f"Snapshot {snapshot_id} already exists")
            extracted.rename(target)
            notify("verifying", 0.75, "Verifying checksums")
            try:
                self.verify(snapshot_id, list(self.read_manifest(snapshot_id)["categories"]))
            except BaseException:
                remove_path(target)
                raise
            return self._summary(self.read_manifest(snapshot_id))
        finally:
            remove_path(staging)


__all__ = ["SnapshotService", "SnapshotError", "SnapshotNotFound", "BackupBusy", "resolve_backup_root"]
