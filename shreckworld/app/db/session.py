from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def _engine():
    settings = get_settings()
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    return create_engine(settings.database_url, connect_args=connect_args)


def create_schema() -> None:
    from app.models import ShreckWorld, ShreckWorldDocumentChunk, ShreckWorldEmbeddingJob, ShreckWorldLibraryItem  # noqa: F401
    from app.db.base import Base

    Base.metadata.create_all(_engine())


def session_factory() -> sessionmaker[Session]:
    return sessionmaker(_engine(), autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    session = session_factory()()
    try:
        yield session
    finally:
        session.close()
