"""File-level primitives for folder snapshots: fast copy, hardlink reuse, sizing, atomic swap."""

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

FICLONE = 0x40049409
SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


@dataclass(frozen=True)
class FileRecord:
    path: str
    size_bytes: int
    mtime_ns: int
    sha256: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"path": self.path, "size_bytes": self.size_bytes, "mtime_ns": self.mtime_ns, "sha256": self.sha256}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_sqlite_sidecar(path: Path) -> bool:
    return path.name.endswith(SQLITE_SIDECAR_SUFFIXES)


def iter_files(root: Path) -> Iterator[Path]:
    """Yield regular files below ``root`` without following symlinks."""
    if root.is_symlink() or not root.exists():
        return
    if root.is_file():
        yield root
        return
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(files):
            path = Path(current) / name
            if not path.is_symlink():
                yield path


def tree_size(root: Path) -> tuple[int, int]:
    """Return ``(bytes, file_count)`` for a file or directory tree."""
    total = 0
    count = 0
    for path in iter_files(root):
        if is_sqlite_sidecar(path):
            continue
        try:
            total += path.stat().st_size
        except OSError:
            continue
        count += 1
    return total, count


def fast_copy(source: Path, target: Path) -> None:
    """Copy one file preferring a copy-on-write clone, then a buffered copy."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(source, "rb") as src, open(target, "wb") as dst:
            try:
                fcntl.ioctl(dst.fileno(), FICLONE, src.fileno())
            except OSError:
                shutil.copyfileobj(src, dst, length=8 * 1024 * 1024)
        shutil.copystat(source, target)
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def sqlite_snapshot(source: Path, target: Path) -> None:
    """Consistent copy of a live SQLite database through the online backup API."""
    target.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        target_conn = sqlite3.connect(target.as_posix())
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        source_conn.close()


def link_or_copy(previous: Path, target: Path) -> None:
    """Reuse an unchanged file from the previous snapshot; hardlink if possible."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(previous, target)
    except OSError:
        fast_copy(previous, target)


def copy_tree_into(
    source: Path,
    target_root: Path,
    relative_prefix: str,
    previous: dict[str, FileRecord] | None = None,
    previous_root: Path | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[FileRecord]:
    """Copy ``source`` to ``target_root/relative_prefix`` (a file lands exactly at the prefix).

    Unchanged files (same size and mtime as in ``previous``) are hardlinked from the
    previous snapshot, never from the live tree, so later edits cannot alter a snapshot.
    SQLite databases go through the online backup API and their sidecars are skipped.
    """
    records: list[FileRecord] = []
    for path in iter_files(source):
        if is_sqlite_sidecar(path):
            continue
        if should_stop and should_stop():
            raise RuntimeError("Backup cancelled")
        if source.is_dir():
            relative = f"{relative_prefix}/{path.relative_to(source).as_posix()}"
        else:
            relative = relative_prefix
        target = target_root / relative
        stat = path.stat()
        known = previous.get(relative) if previous else None
        is_db = path.suffix == ".db"
        if (
            known is not None
            and not is_db
            and previous_root is not None
            and known.size_bytes == stat.st_size
            and known.mtime_ns == stat.st_mtime_ns
            and (previous_root / relative).is_file()
        ):
            link_or_copy(previous_root / relative, target)
            records.append(FileRecord(relative, known.size_bytes, known.mtime_ns, known.sha256))
            continue
        if is_db:
            try:
                sqlite_snapshot(path, target)
            except sqlite3.DatabaseError:
                fast_copy(path, target)
            target_stat = target.stat()
            records.append(FileRecord(relative, target_stat.st_size, target_stat.st_mtime_ns, sha256_file(target)))
        else:
            fast_copy(path, target)
            records.append(FileRecord(relative, stat.st_size, stat.st_mtime_ns))
    return records


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def swap_in(source: Path, destination: Path) -> None:
    """Replace ``destination`` with a copy of ``source`` using stage-then-rename.

    The new content is fully written next to the destination first, so a failure
    mid-copy leaves the existing data untouched.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:8]
    staged = destination.with_name(f".{destination.name}.restoring-{token}")
    retired = destination.with_name(f".{destination.name}.retired-{token}")
    try:
        if source.is_dir():
            staged.mkdir(parents=True, exist_ok=True)
            for path in iter_files(source):
                fast_copy(path, staged / path.relative_to(source))
        else:
            fast_copy(source, staged)
        if destination.exists() or destination.is_symlink():
            destination.rename(retired)
        staged.rename(destination)
    except BaseException:
        remove_path(staged)
        if retired.exists() and not destination.exists():
            retired.rename(destination)
        raise
    remove_path(retired)
    if destination.suffix == ".db":
        for suffix in SQLITE_SIDECAR_SUFFIXES:
            Path(f"{destination}{suffix}").unlink(missing_ok=True)
