"""Tests for database migrations."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.migrations import migrate_jobs_database
from app.models.background_job import AuthorType, JobType
from app.repositories.background_job_repository import BackgroundJobRepository


@pytest.fixture
async def empty_jobs_engine() -> AsyncEngine:
    """Create an empty in-memory engine for migration tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, future=True
    )
    yield engine
    await engine.dispose()


@pytest.fixture
async def legacy_jobs_engine() -> AsyncEngine:
    """Create a jobs database without the ontology_id column (legacy schema)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, future=True
    )

    # Create the table manually without ontology_id column
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE background_jobs (
                    id INTEGER PRIMARY KEY,
                    celery_task_id VARCHAR(255),
                    author_type VARCHAR(50) NOT NULL,
                    author_id VARCHAR(255) NOT NULL,
                    job_type VARCHAR(100) NOT NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'queued',
                    description TEXT NOT NULL,
                    details TEXT,
                    progress FLOAT NOT NULL DEFAULT 0.0,
                    error_message TEXT,
                    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at DATETIME,
                    duration_seconds FLOAT,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_background_jobs_celery_task_id ON background_jobs (celery_task_id)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_background_jobs_author_id ON background_jobs (author_id)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_background_jobs_job_type ON background_jobs (job_type)"
            )
        )
        await conn.execute(
            text("CREATE INDEX ix_background_jobs_status ON background_jobs (status)")
        )

    yield engine
    await engine.dispose()


@pytest.fixture
async def very_legacy_jobs_engine() -> AsyncEngine:
    """Create a jobs database without ontology_id and duration_seconds columns."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, future=True
    )

    # Create the table manually without ontology_id and duration_seconds columns
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE background_jobs (
                    id INTEGER PRIMARY KEY,
                    celery_task_id VARCHAR(255),
                    author_type VARCHAR(50) NOT NULL,
                    author_id VARCHAR(255) NOT NULL,
                    job_type VARCHAR(100) NOT NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'queued',
                    description TEXT NOT NULL,
                    details TEXT,
                    progress FLOAT NOT NULL DEFAULT 0.0,
                    error_message TEXT,
                    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    completed_at DATETIME,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_background_jobs_celery_task_id ON background_jobs (celery_task_id)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_background_jobs_author_id ON background_jobs (author_id)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX ix_background_jobs_job_type ON background_jobs (job_type)"
            )
        )
        await conn.execute(
            text("CREATE INDEX ix_background_jobs_status ON background_jobs (status)")
        )

    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_migrate_jobs_database_adds_ontology_id_column(legacy_jobs_engine):
    """Test that migration adds ontology_id column to legacy database."""
    # Verify column doesn't exist before migration
    async with legacy_jobs_engine.begin() as conn:
        inspector = await conn.run_sync(lambda sync_conn: inspect(sync_conn))
        columns_before = await conn.run_sync(
            lambda sync_conn: inspector.get_columns("background_jobs")
        )
        column_names_before = [col["name"] for col in columns_before]
        assert "ontology_id" not in column_names_before

    # Run migration
    await migrate_jobs_database(legacy_jobs_engine)

    # Verify column exists after migration
    async with legacy_jobs_engine.begin() as conn:
        inspector = await conn.run_sync(lambda sync_conn: inspect(sync_conn))
        columns_after = await conn.run_sync(
            lambda sync_conn: inspector.get_columns("background_jobs")
        )
        column_names_after = [col["name"] for col in columns_after]
        assert "ontology_id" in column_names_after

        # Verify index was created
        indexes = await conn.run_sync(
            lambda sync_conn: inspector.get_indexes("background_jobs")
        )
        index_names = [idx["name"] for idx in indexes]
        assert "ix_background_jobs_ontology_id" in index_names


@pytest.mark.asyncio
async def test_migrate_jobs_database_idempotent(legacy_jobs_engine):
    """Test that migration can be run multiple times without errors."""
    # Run migration twice
    await migrate_jobs_database(legacy_jobs_engine)
    await migrate_jobs_database(legacy_jobs_engine)

    # Verify column still exists and there are no errors
    async with legacy_jobs_engine.begin() as conn:
        inspector = await conn.run_sync(lambda sync_conn: inspect(sync_conn))
        columns = await conn.run_sync(
            lambda sync_conn: inspector.get_columns("background_jobs")
        )
        column_names = [col["name"] for col in columns]
        assert "ontology_id" in column_names


@pytest.mark.asyncio
async def test_migrate_jobs_database_skips_when_no_table(empty_jobs_engine):
    """Test that migration handles missing table gracefully."""
    # Should not raise an error when table doesn't exist
    await migrate_jobs_database(empty_jobs_engine)


@pytest.mark.asyncio
async def test_create_job_with_ontology_id_after_migration(legacy_jobs_engine):
    """Test that jobs can be created with ontology_id after migration."""
    # Run migration
    await migrate_jobs_database(legacy_jobs_engine)

    # Create a job with ontology_id
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_maker = async_sessionmaker(legacy_jobs_engine, expire_on_commit=False)
    async with session_maker() as session:
        repo = BackgroundJobRepository(session)
        job = await repo.create(
            author_type=AuthorType.USER,
            author_id="test-user",
            job_type=JobType.GRAPH_LINK_UPDATE,
            description="Test job with ontology_id",
            ontology_id=123,
        )

        assert job.id is not None
        assert job.ontology_id == 123


@pytest.mark.asyncio
async def test_filter_jobs_by_ontology_id_after_migration(legacy_jobs_engine):
    """Test that jobs can be filtered by ontology_id after migration."""
    # Run migration
    await migrate_jobs_database(legacy_jobs_engine)

    # Create jobs with different ontology_ids
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_maker = async_sessionmaker(legacy_jobs_engine, expire_on_commit=False)
    async with session_maker() as session:
        repo = BackgroundJobRepository(session)

        await repo.create(
            author_type=AuthorType.USER,
            author_id="test-user",
            job_type=JobType.GRAPH_LINK_UPDATE,
            description="Job for ontology 1",
            ontology_id=1,
        )
        await repo.create(
            author_type=AuthorType.USER,
            author_id="test-user",
            job_type=JobType.GRAPH_LINK_UPDATE,
            description="Job for ontology 2",
            ontology_id=2,
        )
        await repo.create(
            author_type=AuthorType.USER,
            author_id="test-user",
            job_type=JobType.GRAPH_LINK_UPDATE,
            description="Job with no ontology",
            ontology_id=None,
        )

        # Filter by ontology_id
        ontology1_jobs = await repo.list_jobs(ontology_id=1)
        assert len(ontology1_jobs) == 1
        assert ontology1_jobs[0].ontology_id == 1

        ontology2_jobs = await repo.list_jobs(ontology_id=2)
        assert len(ontology2_jobs) == 1
        assert ontology2_jobs[0].ontology_id == 2


@pytest.mark.asyncio
async def test_migrate_jobs_database_adds_duration_seconds_column(
    very_legacy_jobs_engine,
):
    """Test that migration adds duration_seconds column to very legacy database."""
    # Verify column doesn't exist before migration
    async with very_legacy_jobs_engine.begin() as conn:
        inspector = await conn.run_sync(lambda sync_conn: inspect(sync_conn))
        columns_before = await conn.run_sync(
            lambda sync_conn: inspector.get_columns("background_jobs")
        )
        column_names_before = [col["name"] for col in columns_before]
        assert "duration_seconds" not in column_names_before
        assert "ontology_id" not in column_names_before

    # Run migration
    await migrate_jobs_database(very_legacy_jobs_engine)

    # Verify both columns exist after migration
    async with very_legacy_jobs_engine.begin() as conn:
        inspector = await conn.run_sync(lambda sync_conn: inspect(sync_conn))
        columns_after = await conn.run_sync(
            lambda sync_conn: inspector.get_columns("background_jobs")
        )
        column_names_after = [col["name"] for col in columns_after]
        assert "duration_seconds" in column_names_after
        assert "ontology_id" in column_names_after


@pytest.mark.asyncio
async def test_duration_seconds_populated_on_job_completion(very_legacy_jobs_engine):
    """Test that duration_seconds is calculated when marking job as done."""
    # Run migration
    await migrate_jobs_database(very_legacy_jobs_engine)

    session_maker = async_sessionmaker(very_legacy_jobs_engine, expire_on_commit=False)
    async with session_maker() as session:
        repo = BackgroundJobRepository(session)

        # Create a job
        job = await repo.create(
            author_type=AuthorType.USER,
            author_id="test-user",
            job_type=JobType.GRAPH_LINK_UPDATE,
            description="Test duration calculation",
        )

        # Mark as running, wait a bit, then mark as done
        await repo.mark_as_running(job.id)
        await asyncio.sleep(0.1)  # Small delay to ensure duration > 0
        completed = await repo.mark_as_done(job.id)

        # Verify duration_seconds was calculated
        assert completed.duration_seconds is not None
        assert completed.duration_seconds >= 0


@pytest.fixture
async def legacy_games_engine() -> AsyncEngine:
    """Create a games database with naive datetime values (no timezone)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, future=True
    )

    # Create tables with naive datetime values
    async with engine.begin() as conn:
        # Create games table
        await conn.execute(
            text(
                """
                CREATE TABLE games (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    ontology_id INTEGER NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME
                )
            """
            )
        )

        # Create game_sessions table
        await conn.execute(
            text(
                """
                CREATE TABLE game_sessions (
                    id INTEGER PRIMARY KEY,
                    game_id INTEGER NOT NULL,
                    title VARCHAR(255) NOT NULL,
                    scheduled_date DATETIME,
                    location VARCHAR(255),
                    summary TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME
                )
            """
            )
        )

        # Create game_session_polls table
        await conn.execute(
            text(
                """
                CREATE TABLE game_session_polls (
                    id INTEGER PRIMARY KEY,
                    session_id INTEGER NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    is_finalized BOOLEAN NOT NULL DEFAULT 0,
                    finalized_option_id INTEGER
                )
            """
            )
        )

        # Create game_session_poll_options table
        await conn.execute(
            text(
                """
                CREATE TABLE game_session_poll_options (
                    id INTEGER PRIMARY KEY,
                    poll_id INTEGER NOT NULL,
                    proposed_start DATETIME NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
        )

        # Create game_session_poll_votes table
        await conn.execute(
            text(
                """
                CREATE TABLE game_session_poll_votes (
                    id INTEGER PRIMARY KEY,
                    option_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """
            )
        )

        # Create game_session_attendance table
        await conn.execute(
            text(
                """
                CREATE TABLE game_session_attendance (
                    session_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    attending BOOLEAN NOT NULL DEFAULT 1,
                    responded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (session_id, user_id)
                )
            """
            )
        )

        # Insert test data with naive datetime strings (no timezone)
        await conn.execute(
            text(
                """
                INSERT INTO games (id, name, ontology_id, created_at, updated_at) 
                VALUES (1, 'Test Game', 1, '2024-01-15 10:30:00', '2024-01-16 14:20:00')
            """
            )
        )

        await conn.execute(
            text(
                """
                INSERT INTO game_sessions (id, game_id, title, scheduled_date, created_at, updated_at)
                VALUES (1, 1, 'Session 1', '2024-02-01 18:00:00', '2024-01-20 09:00:00', '2024-01-21 11:00:00')
            """
            )
        )

        await conn.execute(
            text(
                """
                INSERT INTO game_session_polls (id, session_id, created_at, is_finalized)
                VALUES (1, 1, '2024-01-22 15:30:00', 0)
            """
            )
        )

        await conn.execute(
            text(
                """
                INSERT INTO game_session_poll_options (id, poll_id, proposed_start, created_at)
                VALUES (1, 1, '2024-02-05 19:00:00', '2024-01-22 15:31:00')
            """
            )
        )

        await conn.execute(
            text(
                """
                INSERT INTO game_session_poll_votes (id, option_id, user_id, created_at)
                VALUES (1, 1, 1, '2024-01-23 10:00:00')
            """
            )
        )

        await conn.execute(
            text(
                """
                INSERT INTO game_session_attendance (session_id, user_id, attending, responded_at)
                VALUES (1, 1, 1, '2024-01-24 12:00:00')
            """
            )
        )

    yield engine
    await engine.dispose()


@pytest.mark.asyncio
async def test_migrate_game_datetimes_to_brussels_timezone(legacy_games_engine):
    """Test that migration adds Brussels timezone to naive datetime values."""
    from app.db.migrations import migrate_game_datetimes_to_brussels_timezone

    # Verify datetimes don't have timezone before migration
    async with legacy_games_engine.begin() as conn:
        # Check games table
        result = await conn.execute(text("SELECT created_at FROM games WHERE id = 1"))
        row = result.fetchone()
        assert row[0] == "2024-01-15 10:30:00"
        assert "+01:00" not in row[0]

        # Check game_sessions table
        result = await conn.execute(
            text("SELECT scheduled_date FROM game_sessions WHERE id = 1")
        )
        row = result.fetchone()
        assert row[0] == "2024-02-01 18:00:00"
        assert "+01:00" not in row[0]

    # Run migration
    await migrate_game_datetimes_to_brussels_timezone(legacy_games_engine)

    # Verify datetimes now have timezone
    async with legacy_games_engine.begin() as conn:
        # Check games table
        result = await conn.execute(
            text("SELECT created_at, updated_at FROM games WHERE id = 1")
        )
        row = result.fetchone()
        assert "+01:00" in row[0]
        assert "+01:00" in row[1]

        # Check game_sessions table
        result = await conn.execute(
            text(
                "SELECT scheduled_date, created_at, updated_at FROM game_sessions WHERE id = 1"
            )
        )
        row = result.fetchone()
        assert "+01:00" in row[0]
        assert "+01:00" in row[1]
        assert "+01:00" in row[2]

        # Check game_session_polls table
        result = await conn.execute(
            text("SELECT created_at FROM game_session_polls WHERE id = 1")
        )
        row = result.fetchone()
        assert "+01:00" in row[0]

        # Check game_session_poll_options table
        result = await conn.execute(
            text(
                "SELECT proposed_start, created_at FROM game_session_poll_options WHERE id = 1"
            )
        )
        row = result.fetchone()
        assert "+01:00" in row[0]
        assert "+01:00" in row[1]

        # Check game_session_poll_votes table
        result = await conn.execute(
            text("SELECT created_at FROM game_session_poll_votes WHERE id = 1")
        )
        row = result.fetchone()
        assert "+01:00" in row[0]

        # Check game_session_attendance table
        result = await conn.execute(
            text(
                "SELECT responded_at FROM game_session_attendance WHERE session_id = 1"
            )
        )
        row = result.fetchone()
        assert "+01:00" in row[0]


@pytest.mark.asyncio
async def test_migrate_game_datetimes_idempotent(legacy_games_engine):
    """Test that timezone migration can be run multiple times without errors."""
    from app.db.migrations import migrate_game_datetimes_to_brussels_timezone

    # Run migration twice
    await migrate_game_datetimes_to_brussels_timezone(legacy_games_engine)
    await migrate_game_datetimes_to_brussels_timezone(legacy_games_engine)

    # Verify datetimes still have timezone and weren't double-modified
    async with legacy_games_engine.begin() as conn:
        result = await conn.execute(text("SELECT created_at FROM games WHERE id = 1"))
        row = result.fetchone()
        # Should still have exactly one timezone marker
        assert row[0].count("+01:00") == 1


@pytest.mark.asyncio
async def test_migrate_game_datetimes_skips_when_no_tables(empty_jobs_engine):
    """Test that migration handles missing tables gracefully."""
    from app.db.migrations import migrate_game_datetimes_to_brussels_timezone

    # Should not raise an error when tables don't exist
    await migrate_game_datetimes_to_brussels_timezone(empty_jobs_engine)
