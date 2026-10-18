from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def _engine():
    settings = get_settings()
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    engine = create_engine(settings.database_url, connect_args=connect_args)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
    return engine


def create_schema() -> None:
    from app.models import (  # noqa: F401
        ShreckWorld, ShreckWorldAgentJob, ShreckWorldDocumentChunk,
        ShreckWorldEmbeddingJob, ShreckWorldLibraryItem,
        ShreckWorldSettings,
    )
    from app.db.base import Base

    engine = _engine()
    Base.metadata.create_all(engine)
    # The first prototype used create_all without migrations.  Keep its SQLite
    # data readable while introducing the external Shrecknet-world reference.
    inspector = inspect(engine)
    with engine.begin() as connection:
        if "shreckworld_document_chunks" in inspector.get_table_names():
            columns = {column["name"] for column in inspector.get_columns("shreckworld_document_chunks")}
            additions = {
                "document_id": "VARCHAR(36)", "chunk_role": "VARCHAR(16) DEFAULT 'child'",
                "parent_chunk_id": "VARCHAR(36)", "section_id": "VARCHAR(36)",
                "content_type": "VARCHAR(32)", "embedding_text": "TEXT",
                "heading_path": "JSON DEFAULT '[]'", "source_block_ids": "JSON DEFAULT '[]'",
                "page_numbers": "JSON DEFAULT '[]'", "token_count": "INTEGER",
            }
            for name, definition in additions.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE shreckworld_document_chunks ADD COLUMN {name} {definition}"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_shreckworld_document_chunks_document_id ON shreckworld_document_chunks(document_id)"))
        if engine.dialect.name == "sqlite":
            connection.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS shreckworld_chunk_fts USING fts5(chunk_id UNINDEXED, document_id UNINDEXED, title, heading_path, text, tokenize='unicode61')"))
            connection.execute(text("CREATE TRIGGER IF NOT EXISTS shreckworld_document_fts_delete AFTER DELETE ON shreckworld_documents BEGIN DELETE FROM shreckworld_chunk_fts WHERE document_id = old.id; END"))
        if "shreckworld_embedding_jobs" in inspector.get_table_names():
            columns = {column["name"] for column in inspector.get_columns("shreckworld_embedding_jobs")}
            for name, definition in {
                "started_at": "DATETIME", "updated_at": "DATETIME", "metrics_json": "TEXT",
            }.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE shreckworld_embedding_jobs ADD COLUMN {name} {definition}"))
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
