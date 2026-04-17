from __future__ import annotations

import pytest

from app.models import AuthorType, BackgroundJob, JobStatus, JobType


@pytest.mark.asyncio
async def test_get_job_allows_unauthenticated_polling(client, session_maker) -> None:
    async with session_maker() as session:
        job = BackgroundJob(
            celery_task_id="legacy-import-job",
            author_type=AuthorType.USER,
            author_id="1",
            kind=JobType.LEGACY_IMPORT.value,
            job_type=JobType.LEGACY_IMPORT,
            status=JobStatus.RUNNING,
            description="Importing legacy backup",
            details='{"phase":"shrecknet_import"}',
            progress=0.25,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)

    response = await client.get(f"/jobs/{job.id}")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["id"] == job.id
    assert payload["status"] == JobStatus.RUNNING.value


@pytest.mark.asyncio
async def test_list_jobs_still_requires_authentication(client) -> None:
    response = await client.get("/jobs/")

    assert response.status_code == 401
