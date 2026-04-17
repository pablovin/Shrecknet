from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config_store import get_settings

_engine: Engine | None = None
_sessionmaker: sessionmaker[Session] | None = None
_engine_key: tuple[str, bool] | None = None
_sqlite_fingerprint: tuple[int, int, int] | None = None


class AsyncSessionCompat:
    """
    Async-compatible wrapper over a sync SQLAlchemy Session.

    This keeps the service/endpoint surface async-friendly while remaining stable
    on SQLite environments where native aiosqlite drivers are unavailable/unreliable.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    # Sync-style helpers often used directly by service code.
    def add(self, instance: object) -> None:
        self._session.add(instance)

    def add_all(self, instances: list[object]) -> None:
        self._session.add_all(instances)

    async def execute(self, *args: Any, **kwargs: Any):
        return await asyncio.to_thread(self._session.execute, *args, **kwargs)

    async def scalar(self, *args: Any, **kwargs: Any):
        return await asyncio.to_thread(self._session.scalar, *args, **kwargs)

    async def get(self, *args: Any, **kwargs: Any):
        return await asyncio.to_thread(self._session.get, *args, **kwargs)

    async def commit(self) -> None:
        await asyncio.to_thread(self._session.commit)

    async def rollback(self) -> None:
        await asyncio.to_thread(self._session.rollback)

    async def flush(self) -> None:
        await asyncio.to_thread(self._session.flush)

    async def refresh(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.to_thread(self._session.refresh, *args, **kwargs)

    async def delete(self, instance: object) -> None:
        await asyncio.to_thread(self._session.delete, instance)

    def merge(self, instance: object):
        return self._session.merge(instance)

    def query(self, *entities: Any):
        return self._session.query(*entities)

    async def close(self) -> None:
        await asyncio.to_thread(self._session.close)

    async def __aenter__(self) -> "AsyncSessionCompat":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc is not None:
            await self.rollback()
        await self.close()


def _sqlite_path_from_url(url: str) -> Path | None:
    prefixes = ("sqlite:///", "sqlite+aiosqlite:///")
    for prefix in prefixes:
        if not url.startswith(prefix):
            continue
        raw_path = unquote(url[len(prefix) :])
        if not raw_path:
            return None
        if raw_path.startswith("./"):
            return Path(raw_path[2:]).resolve()
        if raw_path.startswith("/"):
            return Path(raw_path)
        return Path(raw_path).resolve()
    return None


def _fingerprint_sqlite_file(path: Path | None) -> tuple[int, int, int] | None:
    if path is None:
        return None
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_ino, stat.st_mtime_ns, stat.st_size)


def _reset_cached_engine() -> None:
    global _engine, _sessionmaker, _engine_key, _sqlite_fingerprint
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _sessionmaker = None
    _engine_key = None
    _sqlite_fingerprint = None


def get_engine() -> Engine:
    global _engine, _sessionmaker, _engine_key, _sqlite_fingerprint
    settings = get_settings()
    engine_key = (settings.database_url, settings.debug)
    sqlite_path = _sqlite_path_from_url(settings.database_url)
    sqlite_fingerprint = _fingerprint_sqlite_file(sqlite_path)
    if _engine is not None and _engine_key == engine_key and sqlite_path is not None:
        if _sqlite_fingerprint != sqlite_fingerprint:
            _reset_cached_engine()
        else:
            _sqlite_fingerprint = sqlite_fingerprint

    if _engine is None or _engine_key != engine_key:
        _engine = create_engine(settings.database_url, echo=settings.debug)
        _sessionmaker = sessionmaker(_engine, autocommit=False, autoflush=False)
        _engine_key = engine_key
        _sqlite_fingerprint = sqlite_fingerprint
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    # Always resolve engine first so SQLite fingerprint checks can invalidate
    # stale engine/sessionmaker state after import workflows replace DB files.
    get_engine()
    if _sessionmaker is None:
        raise RuntimeError("sessionmaker unavailable")
    return _sessionmaker


def get_session() -> Generator[Session, None, None]:
    sessionmaker_ = get_sessionmaker()
    session = sessionmaker_()
    try:
        yield session
    finally:
        session.close()


def AsyncSessionMaker() -> AsyncSessionCompat:
    sessionmaker_ = get_sessionmaker()
    return AsyncSessionCompat(sessionmaker_())


async def get_db_session() -> AsyncGenerator[AsyncSessionCompat, None]:
    sessionmaker_ = get_sessionmaker()
    session = sessionmaker_()
    compat = AsyncSessionCompat(session)
    try:
        yield compat
    finally:
        await compat.close()
