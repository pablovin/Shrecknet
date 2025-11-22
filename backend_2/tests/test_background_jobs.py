"""Tests for background jobs functionality."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.jobs_session import JobsSessionMaker
from app.models.background_job import AuthorType, JobStatus, JobType, BackgroundJob
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
async def test_create_background_job(jobs_session):
    """Test creating a background job."""
    repo = BackgroundJobRepository(jobs_session)

    job = await repo.create(
        author_type=AuthorType.USER,
        author_id="123",
        job_type=JobType.GRAPH_LINK_UPDATE,
        description="Test job",
        celery_task_id="task-123",
        details='{"key": "value"}',
    )

    assert job.id is not None
    assert job.author_type == AuthorType.USER
    assert job.author_id == "123"
    assert job.job_type == JobType.GRAPH_LINK_UPDATE
    assert job.status == JobStatus.QUEUED
    assert job.progress == 0.0
    assert job.description == "Test job"
    assert job.celery_task_id == "task-123"


@pytest.mark.asyncio
async def test_get_job_by_id(jobs_session):
    """Test retrieving a job by ID."""
    repo = BackgroundJobRepository(jobs_session)

    job = await repo.create(
        author_type=AuthorType.AGENT,
        author_id="agent-1",
        job_type=JobType.NEO4J_EMBEDDING,
        description="Embedding job",
    )

    retrieved = await repo.get_by_id(job.id)
    assert retrieved is not None
    assert retrieved.id == job.id
    assert retrieved.author_type == AuthorType.AGENT


@pytest.mark.asyncio
async def test_update_job_status(jobs_session):
    """Test updating job status."""
    repo = BackgroundJobRepository(jobs_session)

    job = await repo.create(
        author_type=AuthorType.USER,
        author_id="456",
        job_type=JobType.GRAPH_LINK_UPDATE,
        description="Status update test",
    )

    updated = await repo.update_status(job.id, JobStatus.RUNNING, progress=0.5)
    assert updated is not None
    assert updated.status == JobStatus.RUNNING
    assert updated.progress == 0.5


@pytest.mark.asyncio
async def test_mark_job_as_done(jobs_session):
    """Test marking a job as done."""
    repo = BackgroundJobRepository(jobs_session)

    job = await repo.create(
        author_type=AuthorType.AGENT,
        author_id="agent-2",
        job_type=JobType.NEO4J_EMBEDDING,
        description="Completion test",
    )

    await repo.mark_as_running(job.id)
    completed = await repo.mark_as_done(job.id, details='{"result": "success"}')

    assert completed is not None
    assert completed.status == JobStatus.DONE
    assert completed.progress == 1.0
    assert completed.completed_at is not None
    assert completed.details == '{"result": "success"}'


@pytest.mark.asyncio
async def test_mark_job_as_failed(jobs_session):
    """Test marking a job as failed."""
    repo = BackgroundJobRepository(jobs_session)

    job = await repo.create(
        author_type=AuthorType.USER,
        author_id="789",
        job_type=JobType.GRAPH_LINK_UPDATE,
        description="Failure test",
    )

    await repo.mark_as_running(job.id)
    failed = await repo.mark_as_failed(job.id, "Something went wrong")

    assert failed is not None
    assert failed.status == JobStatus.FAILED
    assert failed.error_message == "Something went wrong"
    assert failed.completed_at is not None


@pytest.mark.asyncio
async def test_list_jobs_with_filters(jobs_session):
    """Test listing jobs with various filters."""
    repo = BackgroundJobRepository(jobs_session)

    # Create multiple jobs
    await repo.create(
        author_type=AuthorType.USER,
        author_id="user-1",
        job_type=JobType.GRAPH_LINK_UPDATE,
        description="Job 1",
    )
    await repo.create(
        author_type=AuthorType.AGENT,
        author_id="agent-1",
        job_type=JobType.NEO4J_EMBEDDING,
        description="Job 2",
    )
    job3 = await repo.create(
        author_type=AuthorType.USER,
        author_id="user-1",
        job_type=JobType.GRAPH_LINK_UPDATE,
        description="Job 3",
    )
    await repo.mark_as_running(job3.id)

    # Filter by author_type
    user_jobs = await repo.list_jobs(author_type=AuthorType.USER)
    assert len(user_jobs) == 2

    # Filter by job_type
    link_jobs = await repo.list_jobs(job_type=JobType.GRAPH_LINK_UPDATE)
    assert len(link_jobs) == 2

    # Filter by status
    running_jobs = await repo.list_jobs(status=JobStatus.RUNNING)
    assert len(running_jobs) == 1

    # Filter by author_id
    user1_jobs = await repo.list_jobs(author_id="user-1")
    assert len(user1_jobs) == 2


@pytest.mark.asyncio
async def test_delete_completed_jobs(jobs_session):
    """Test deleting completed jobs."""
    repo = BackgroundJobRepository(jobs_session)

    # Create jobs with different statuses
    job1 = await repo.create(
        author_type=AuthorType.USER,
        author_id="user-1",
        job_type=JobType.GRAPH_LINK_UPDATE,
        description="Job 1",
    )
    await repo.mark_as_done(job1.id)

    job2 = await repo.create(
        author_type=AuthorType.USER,
        author_id="user-1",
        job_type=JobType.GRAPH_LINK_UPDATE,
        description="Job 2",
    )
    await repo.mark_as_failed(job2.id, "Error")

    job3 = await repo.create(
        author_type=AuthorType.USER,
        author_id="user-1",
        job_type=JobType.GRAPH_LINK_UPDATE,
        description="Job 3",
    )
    await repo.mark_as_running(job3.id)

    # Try to delete all jobs (only completed ones should be deleted)
    deleted_count = await repo.delete_jobs([job1.id, job2.id, job3.id])
    assert deleted_count == 2  # Only done and failed jobs

    # Verify running job still exists
    remaining = await repo.get_by_id(job3.id)
    assert remaining is not None


@pytest.mark.asyncio
async def test_update_job_progress(jobs_session):
    """Test updating job progress."""
    repo = BackgroundJobRepository(jobs_session)

    job = await repo.create(
        author_type=AuthorType.AGENT,
        author_id="agent-1",
        job_type=JobType.NEO4J_EMBEDDING,
        description="Progress test",
    )

    await repo.mark_as_running(job.id)
    updated = await repo.update_progress(
        job.id, 0.75, details='{"step": "almost done"}'
    )

    assert updated is not None
    assert updated.progress == 0.75
    assert updated.details == '{"step": "almost done"}'


@pytest.mark.asyncio
async def test_jobs_api_list_endpoint(client: AsyncClient, admin_token: str):
    """Test the jobs list API endpoint."""
    headers = {"Authorization": f"Bearer {admin_token}"}

    response = await client.get("/jobs/", headers=headers)
    assert response.status_code == 200
    jobs = response.json()
    assert isinstance(jobs, list)


@pytest.mark.asyncio
async def test_jobs_api_create_endpoint(client: AsyncClient, admin_token: str):
    """Test creating a job via API."""
    headers = {"Authorization": f"Bearer {admin_token}"}
    job_data = {
        "author_type": "user",
        "author_id": "123",
        "job_type": "graph_link_update",
        "description": "Test job via API",
        "celery_task_id": "task-456",
    }

    response = await client.post("/jobs/", json=job_data, headers=headers)
    assert response.status_code == 201
    job = response.json()
    assert job["description"] == "Test job via API"
    assert job["status"] == "queued"


@pytest.mark.asyncio
async def test_jobs_api_get_endpoint(client: AsyncClient, admin_token: str):
    """Test getting a specific job via API."""
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Create a job first
    job_data = {
        "author_type": "agent",
        "author_id": "agent-1",
        "job_type": "neo4j_embedding",
        "description": "Test get endpoint",
    }
    create_response = await client.post("/jobs/", json=job_data, headers=headers)
    assert create_response.status_code == 201
    created_job = create_response.json()

    # Get the job
    get_response = await client.get(f"/jobs/{created_job['id']}", headers=headers)
    assert get_response.status_code == 200
    retrieved_job = get_response.json()
    assert retrieved_job["id"] == created_job["id"]
    assert retrieved_job["description"] == "Test get endpoint"


@pytest.mark.asyncio
async def test_jobs_api_update_endpoint(client: AsyncClient, admin_token: str):
    """Test updating a job via API."""
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Create a job first
    job_data = {
        "author_type": "user",
        "author_id": "123",
        "job_type": "graph_link_update",
        "description": "Test update endpoint",
    }
    create_response = await client.post("/jobs/", json=job_data, headers=headers)
    created_job = create_response.json()

    # Update the job
    update_data = {"status": "running", "progress": 0.5}
    update_response = await client.patch(
        f"/jobs/{created_job['id']}", json=update_data, headers=headers
    )
    assert update_response.status_code == 200
    updated_job = update_response.json()
    assert updated_job["status"] == "running"
    assert updated_job["progress"] == 0.5


@pytest.mark.asyncio
async def test_jobs_api_delete_endpoint(client: AsyncClient, admin_token: str):
    """Test deleting jobs via API."""
    headers = {"Authorization": f"Bearer {admin_token}"}

    # Create and complete two jobs
    job_ids = []
    for i in range(2):
        job_data = {
            "author_type": "user",
            "author_id": "123",
            "job_type": "graph_link_update",
            "description": f"Test delete endpoint {i}",
        }
        create_response = await client.post("/jobs/", json=job_data, headers=headers)
        job = create_response.json()

        # Mark as done
        await client.patch(
            f"/jobs/{job['id']}", json={"status": "done"}, headers=headers
        )
        job_ids.append(job["id"])

    # Delete the jobs
    delete_response = await client.request(
        "DELETE",
        "/jobs/",
        json={
            "jobs": [
                {"kind": "graph_link_update", "job_id": str(jid)} for jid in job_ids
            ]
        },
        headers=headers,
    )
    assert delete_response.status_code == 200
    result = delete_response.json()
    assert result["deleted_count"] == 2


@pytest.mark.asyncio
async def test_jobs_api_requires_auth(client: AsyncClient):
    """Test that jobs API endpoints require authentication."""
    # Try to list jobs without auth
    response = await client.get("/jobs/")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_delete_agent_embedding_job(jobs_session):
    """Test deleting a specific embedding job for an agent."""
    repo = BackgroundJobRepository(jobs_session)

    # Create an embedding job for an agent
    job = await repo.create(
        author_type=AuthorType.AGENT,
        author_id="agent-123",
        job_type=JobType.NEO4J_EMBEDDING,
        description="Test embedding job",
        ontology_id=1,
    )

    # Create a non-embedding job for the same agent (should not be deletable)
    other_job = await repo.create(
        author_type=AuthorType.AGENT,
        author_id="agent-123",
        job_type=JobType.GRAPH_LINK_UPDATE,
        description="Non-embedding job",
    )

    # Delete the embedding job
    from sqlalchemy import delete, select
    from app.models.background_job import BackgroundJob

    result = await jobs_session.execute(
        select(BackgroundJob).where(
            BackgroundJob.id == job.id,
            BackgroundJob.author_type == AuthorType.AGENT,
            BackgroundJob.author_id == "agent-123",
            BackgroundJob.job_type.in_(
                [JobType.NEO4J_EMBEDDING, JobType.PDF_BOOK_EMBEDDING]
            ),
        )
    )
    job_to_delete = result.scalar_one_or_none()
    assert job_to_delete is not None

    await jobs_session.delete(job_to_delete)
    await jobs_session.commit()

    # Verify the embedding job was deleted
    deleted_job = await repo.get_by_id(job.id)
    assert deleted_job is None

    # Verify the non-embedding job still exists
    remaining_job = await repo.get_by_id(other_job.id)
    assert remaining_job is not None


@pytest.mark.asyncio
async def test_delete_agent_embedding_jobs_bulk(jobs_session):
    """Test bulk deletion of embedding jobs for an agent."""
    repo = BackgroundJobRepository(jobs_session)

    # Create multiple embedding jobs for an agent
    agent_id = "agent-456"

    # Stuck jobs (queued/running)
    stuck_job1 = await repo.create(
        author_type=AuthorType.AGENT,
        author_id=agent_id,
        job_type=JobType.NEO4J_EMBEDDING,
        description="Stuck embedding job 1",
        ontology_id=1,
    )

    stuck_job2 = await repo.create(
        author_type=AuthorType.AGENT,
        author_id=agent_id,
        job_type=JobType.PDF_BOOK_EMBEDDING,
        description="Stuck PDF job",
        ontology_id=1,
    )
    await repo.mark_as_running(stuck_job2.id)

    # Completed jobs
    done_job = await repo.create(
        author_type=AuthorType.AGENT,
        author_id=agent_id,
        job_type=JobType.NEO4J_EMBEDDING,
        description="Completed embedding job",
        ontology_id=1,
    )
    await repo.mark_as_done(done_job.id)

    # Job for different ontology
    other_ontology_job = await repo.create(
        author_type=AuthorType.AGENT,
        author_id=agent_id,
        job_type=JobType.NEO4J_EMBEDDING,
        description="Different ontology job",
        ontology_id=2,
    )

    # Non-embedding job (should not be deleted)
    non_embedding_job = await repo.create(
        author_type=AuthorType.AGENT,
        author_id=agent_id,
        job_type=JobType.GRAPH_LINK_UPDATE,
        description="Non-embedding job",
    )

    # Test 1: Delete all embedding jobs for the agent
    from sqlalchemy import delete, select
    from app.models.background_job import BackgroundJob

    query = select(BackgroundJob).where(
        BackgroundJob.author_type == AuthorType.AGENT,
        BackgroundJob.author_id == agent_id,
        BackgroundJob.job_type.in_(
            [JobType.NEO4J_EMBEDDING, JobType.PDF_BOOK_EMBEDDING]
        ),
    )
    result = await jobs_session.execute(query)
    all_embedding_jobs = list(result.scalars().all())
    assert len(all_embedding_jobs) == 4  # All embedding jobs

    # Test 2: Delete only stuck jobs (queued/running) for ontology 1
    stuck_query = select(BackgroundJob).where(
        BackgroundJob.author_type == AuthorType.AGENT,
        BackgroundJob.author_id == agent_id,
        BackgroundJob.job_type.in_(
            [JobType.NEO4J_EMBEDDING, JobType.PDF_BOOK_EMBEDDING]
        ),
        BackgroundJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
        BackgroundJob.ontology_id == 1,
    )
    result = await jobs_session.execute(stuck_query)
    stuck_jobs = list(result.scalars().all())
    assert len(stuck_jobs) == 2  # Only the stuck jobs for ontology 1

    # Delete stuck jobs
    if stuck_jobs:
        delete_query = delete(BackgroundJob).where(
            BackgroundJob.id.in_([job.id for job in stuck_jobs])
        )
        await jobs_session.execute(delete_query)
        await jobs_session.commit()

    # Verify stuck jobs were deleted
    deleted_stuck1 = await repo.get_by_id(stuck_job1.id)
    assert deleted_stuck1 is None
    deleted_stuck2 = await repo.get_by_id(stuck_job2.id)
    assert deleted_stuck2 is None

    # Verify other jobs still exist
    remaining_done = await repo.get_by_id(done_job.id)
    assert remaining_done is not None
    remaining_other_ont = await repo.get_by_id(other_ontology_job.id)
    assert remaining_other_ont is not None
    remaining_non_embedding = await repo.get_by_id(non_embedding_job.id)
    assert remaining_non_embedding is not None


@pytest.mark.asyncio
async def test_delete_agent_embedding_jobs_by_ontology(jobs_session):
    """Test bulk deletion of embedding jobs filtered by ontology."""
    repo = BackgroundJobRepository(jobs_session)

    agent_id = "agent-789"

    # Create jobs for ontology 1
    ont1_job1 = await repo.create(
        author_type=AuthorType.AGENT,
        author_id=agent_id,
        job_type=JobType.NEO4J_EMBEDDING,
        description="Ontology 1 job 1",
        ontology_id=1,
    )

    ont1_job2 = await repo.create(
        author_type=AuthorType.AGENT,
        author_id=agent_id,
        job_type=JobType.PDF_BOOK_EMBEDDING,
        description="Ontology 1 job 2",
        ontology_id=1,
    )

    # Create jobs for ontology 2
    ont2_job = await repo.create(
        author_type=AuthorType.AGENT,
        author_id=agent_id,
        job_type=JobType.NEO4J_EMBEDDING,
        description="Ontology 2 job",
        ontology_id=2,
    )

    # Delete only ontology 1 jobs
    from sqlalchemy import delete, select
    from app.models.background_job import BackgroundJob

    query = select(BackgroundJob).where(
        BackgroundJob.author_type == AuthorType.AGENT,
        BackgroundJob.author_id == agent_id,
        BackgroundJob.job_type.in_(
            [JobType.NEO4J_EMBEDDING, JobType.PDF_BOOK_EMBEDDING]
        ),
        BackgroundJob.ontology_id == 1,
    )
    result = await jobs_session.execute(query)
    ont1_jobs = list(result.scalars().all())
    assert len(ont1_jobs) == 2

    # Delete ontology 1 jobs
    if ont1_jobs:
        delete_query = delete(BackgroundJob).where(
            BackgroundJob.id.in_([job.id for job in ont1_jobs])
        )
        await jobs_session.execute(delete_query)
        await jobs_session.commit()

    # Verify ontology 1 jobs were deleted
    deleted1 = await repo.get_by_id(ont1_job1.id)
    assert deleted1 is None
    deleted2 = await repo.get_by_id(ont1_job2.id)
    assert deleted2 is None

    # Verify ontology 2 job still exists
    remaining = await repo.get_by_id(ont2_job.id)
    assert remaining is not None
