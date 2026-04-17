"""Database session management for background jobs database."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config_store import get_settings
from app.db.session import AsyncSessionCompat

_jobs_engine: Engine | None = None
_jobs_sessionmaker: sessionmaker[Session] | None = None
_jobs_engine_key: tuple[str, bool] | None = None


def get_jobs_engine() -> Engine:
    global _jobs_engine, _jobs_sessionmaker, _jobs_engine_key
    settings = get_settings()
    engine_key = (settings.jobs_database_url, settings.debug)
    if _jobs_engine is None or _jobs_engine_key != engine_key:
        _jobs_engine = create_engine(settings.jobs_database_url, echo=settings.debug)
        _jobs_sessionmaker = sessionmaker(_jobs_engine, autocommit=False, autoflush=False)
        _jobs_engine_key = engine_key
    return _jobs_engine


def _get_jobs_sessionmaker() -> sessionmaker[Session]:
    # Always resolve engine first so settings changes are reflected before
    # creating sessions.
    get_jobs_engine()
    if _jobs_sessionmaker is None:
        raise RuntimeError("Jobs session maker unavailable")
    return _jobs_sessionmaker


def JobsSessionMaker() -> AsyncSessionCompat:
    session = _get_jobs_sessionmaker()()
    return AsyncSessionCompat(session)


async def get_jobs_session() -> AsyncGenerator[AsyncSessionCompat, None]:
    session = JobsSessionMaker()
    try:
        yield session
    finally:
        await session.close()
