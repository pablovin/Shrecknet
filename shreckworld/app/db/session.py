from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def _engine():
    settings = get_settings()
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    return create_engine(settings.database_url, connect_args=connect_args)


def create_schema() -> None:
    from app.models import ShreckWorld, ShreckWorldDocumentChunk, ShreckWorldEmbeddingJob, ShreckWorldLibraryItem  # noqa: F401
    from app.db.base import Base

    engine = _engine()
    Base.metadata.create_all(engine)
    # The first prototype used create_all without migrations.  Keep its SQLite
    # data readable while introducing the external Shrecknet-world reference.
    inspector = inspect(engine)
    with engine.begin() as connection:
        if "shreckworlds" in inspector.get_table_names():
            columns = {column["name"] for column in inspector.get_columns("shreckworlds")}
            if "shrecknet_world_id" not in columns:
                connection.execute(text("ALTER TABLE shreckworlds ADD COLUMN shrecknet_world_id VARCHAR(64)"))
                connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_shreckworlds_shrecknet_world_id ON shreckworlds (shrecknet_world_id)"))
        if "shreckworld_library_items" in inspector.get_table_names():
            columns = {column["name"] for column in inspector.get_columns("shreckworld_library_items")}
            additions = {
                "original_filename": "VARCHAR(512)", "mime_type": "VARCHAR(128)",
                "storage_path": "VARCHAR(512)", "file_size": "INTEGER",
                "cover_path": "VARCHAR(512)", "index_status": "VARCHAR(32) DEFAULT 'unindexed'",
                "index_error": "TEXT",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE shreckworld_library_items ADD COLUMN {name} {definition}"))
            connection.execute(text("UPDATE shreckworld_library_items SET storage_path = pdf_path WHERE storage_path IS NULL"))
            connection.execute(text("UPDATE shreckworld_library_items SET index_status = CASE WHEN embedding_status = 'ready' THEN 'ready' WHEN embedding_status = 'failed' THEN 'failed' ELSE 'unindexed' END WHERE index_status IS NULL OR index_status = 'unindexed'"))


def session_factory() -> sessionmaker[Session]:
    return sessionmaker(_engine(), autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    session = session_factory()()
    try:
        yield session
    finally:
        session.close()
