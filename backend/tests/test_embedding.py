"""Tests for embedding functionality."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.background_job import AuthorType, JobStatus, JobType
from app.repositories.background_job_repository import BackgroundJobRepository


@pytest.fixture
async def jobs_engine() -> AsyncEngine:
    """Create a test engine for jobs database."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool, future=True
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def jobs_session(jobs_engine: AsyncEngine):
    """Create a test session for jobs database."""
    session_maker = async_sessionmaker(jobs_engine, expire_on_commit=False)
    async with session_maker() as session:
        yield session


@pytest.mark.asyncio
async def test_create_embedding_job_with_ontology_id(jobs_session):
    """Test creating an embedding job with ontology_id."""
    repo = BackgroundJobRepository(jobs_session)

    job = await repo.create(
        author_type=AuthorType.USER,
        author_id="123",
        job_type=JobType.NEO4J_EMBEDDING,
        description="Embedding job for ontology 1",
        ontology_id=1,
    )

    assert job.id is not None
    assert job.ontology_id == 1
    assert job.job_type == JobType.NEO4J_EMBEDDING
    assert job.status == JobStatus.QUEUED


@pytest.mark.asyncio
async def test_duration_calculation_on_job_completion(jobs_session):
    """Test that duration is calculated when job completes."""
    repo = BackgroundJobRepository(jobs_session)

    job = await repo.create(
        author_type=AuthorType.USER,
        author_id="456",
        job_type=JobType.NEO4J_EMBEDDING,
        description="Duration test",
        ontology_id=2,
    )

    # Mark as running, then done
    await repo.mark_as_running(job.id)
    completed = await repo.mark_as_done(job.id, details='{"nodes_processed": 10}')

    assert completed is not None
    assert completed.status == JobStatus.DONE
    assert completed.duration_seconds is not None
    assert completed.duration_seconds >= 0


@pytest.mark.asyncio
async def test_duration_calculation_on_job_failure(jobs_session):
    """Test that duration is calculated when job fails."""
    repo = BackgroundJobRepository(jobs_session)

    job = await repo.create(
        author_type=AuthorType.USER,
        author_id="789",
        job_type=JobType.NEO4J_EMBEDDING,
        description="Failure duration test",
        ontology_id=3,
    )

    # Mark as running, then failed
    await repo.mark_as_running(job.id)
    failed = await repo.mark_as_failed(job.id, "Test error")

    assert failed is not None
    assert failed.status == JobStatus.FAILED
    assert failed.duration_seconds is not None
    assert failed.duration_seconds >= 0


@pytest.mark.asyncio
async def test_list_jobs_filter_by_ontology_id(jobs_session):
    """Test filtering jobs by ontology_id."""
    repo = BackgroundJobRepository(jobs_session)

    # Create jobs for different ontologies
    await repo.create(
        author_type=AuthorType.USER,
        author_id="user-1",
        job_type=JobType.NEO4J_EMBEDDING,
        description="Embedding ontology 1",
        ontology_id=1,
    )
    await repo.create(
        author_type=AuthorType.USER,
        author_id="user-1",
        job_type=JobType.NEO4J_EMBEDDING,
        description="Embedding ontology 2",
        ontology_id=2,
    )
    await repo.create(
        author_type=AuthorType.USER,
        author_id="user-1",
        job_type=JobType.NEO4J_EMBEDDING,
        description="Embedding ontology 1 again",
        ontology_id=1,
    )

    # Filter by ontology_id
    ontology1_jobs = await repo.list_jobs(ontology_id=1)
    assert len(ontology1_jobs) == 2
    for job in ontology1_jobs:
        assert job.ontology_id == 1

    ontology2_jobs = await repo.list_jobs(ontology_id=2)
    assert len(ontology2_jobs) == 1
    assert ontology2_jobs[0].ontology_id == 2


@pytest.mark.asyncio
async def test_list_embedding_jobs_for_ontology_limit(jobs_session):
    """Test listing embedding jobs with limit."""
    repo = BackgroundJobRepository(jobs_session)

    # Create 15 jobs for the same ontology
    for i in range(15):
        await repo.create(
            author_type=AuthorType.USER,
            author_id="user-1",
            job_type=JobType.NEO4J_EMBEDDING,
            description=f"Embedding job {i}",
            ontology_id=1,
        )

    # List with limit of 10
    jobs = await repo.list_jobs(
        ontology_id=1, job_type=JobType.NEO4J_EMBEDDING, limit=10
    )

    assert len(jobs) == 10


@pytest.mark.asyncio
async def test_embedding_job_with_details(jobs_session):
    """Test embedding job stores processing details."""
    repo = BackgroundJobRepository(jobs_session)

    job = await repo.create(
        author_type=AuthorType.USER,
        author_id="user-1",
        job_type=JobType.NEO4J_EMBEDDING,
        description="Embedding with details",
        ontology_id=5,
        details='{"initial_count": 100}',
    )

    assert job.details == '{"initial_count": 100}'

    # Complete with result details
    completed = await repo.mark_as_done(
        job.id, details='{"nodes_processed": 95, "nodes_failed": 5}'
    )

    assert completed.details == '{"nodes_processed": 95, "nodes_failed": 5}'


@pytest.mark.asyncio
async def test_multiple_ontology_filters(jobs_session):
    """Test combining ontology filter with other filters."""
    repo = BackgroundJobRepository(jobs_session)

    # Create jobs with various combinations
    job1 = await repo.create(
        author_type=AuthorType.USER,
        author_id="user-1",
        job_type=JobType.NEO4J_EMBEDDING,
        description="User job for ontology 1",
        ontology_id=1,
    )
    await repo.create(
        author_type=AuthorType.AGENT,
        author_id="agent-1",
        job_type=JobType.NEO4J_EMBEDDING,
        description="Agent job for ontology 1",
        ontology_id=1,
    )
    await repo.create(
        author_type=AuthorType.USER,
        author_id="user-1",
        job_type=JobType.NEO4J_EMBEDDING,
        description="User job for ontology 2",
        ontology_id=2,
    )
    await repo.mark_as_done(job1.id)

    # Filter by ontology and author type
    user_ont1_jobs = await repo.list_jobs(ontology_id=1, author_type=AuthorType.USER)
    assert len(user_ont1_jobs) == 1
    assert user_ont1_jobs[0].author_type == AuthorType.USER
    assert user_ont1_jobs[0].ontology_id == 1

    # Filter by ontology and status
    done_ont1_jobs = await repo.list_jobs(ontology_id=1, status=JobStatus.DONE)
    assert len(done_ont1_jobs) == 1
    assert done_ont1_jobs[0].status == JobStatus.DONE
