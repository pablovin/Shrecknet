import pytest

from shrecknet_client.errors import JobTimeoutError
from shrecknet_client.models import BackgroundJobRecord, NovelistRunCreate
from shrecknet_client.resources import JobsAPI, NovelistAPI


class _DummyClient:
    def __init__(self):
        self.calls = []

    async def raw_request(self, method, path, params=None, json=None):
        self.calls.append((method, path, params, json))
        if method == "POST" and path == "/jobs/novelist/agent-1/runs":
            return {
                "id": "run-1",
                "agent_id": "agent-1",
                "status": "queued",
                "stage": "queued",
                "background_job_id": 7,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        if method == "GET" and path == "/jobs/novelist/runs/run-1":
            return {
                "id": "run-1",
                "agent_id": "agent-1",
                "status": "processing",
                "stage": "ingest",
                "background_job_id": 7,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        if method == "GET" and path == "/jobs/novelist/runs/run-timeout":
            return {
                "id": "run-timeout",
                "agent_id": "agent-1",
                "status": "queued",
                "stage": "queued",
                "background_job_id": None,
                "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        if method == "GET" and path == "/jobs/novelist/agent-1/runs":
            return []
        if method == "DELETE" and path == "/jobs/novelist/agent-1/runs/run-1":
            return {"deleted": 1}
        raise AssertionError((method, path))


@pytest.mark.asyncio
async def test_novelist_start_list_delete() -> None:
    client = _DummyClient()
    jobs = JobsAPI(client)
    api = NovelistAPI(client, jobs)

    run = await api.start_run("agent-1", NovelistRunCreate(unstructured_text="hello"))
    assert run.id == "run-1"

    items = await api.list_runs("agent-1")
    assert items == []

    deleted = await api.delete_run("agent-1", "run-1")
    assert deleted["deleted"] == 1


@pytest.mark.asyncio
async def test_novelist_wait_for_run_uses_jobs_wait() -> None:
    client = _DummyClient()

    class _Jobs(JobsAPI):
        async def wait(self, job_id: int, **kwargs):
            return BackgroundJobRecord(
                id=job_id,
                job_type="novelist",
                status="done",
                progress=1.0,
                raw={"id": job_id, "job_type": "novelist", "status": "done", "progress": 1.0},
            )

    api = NovelistAPI(client, _Jobs(client))
    job = await api.wait_for_run("run-1", timeout_s=1)
    assert job.status == "done"


@pytest.mark.asyncio
async def test_novelist_wait_for_run_timeout_before_job_id() -> None:
    client = _DummyClient()
    jobs = JobsAPI(client)
    api = NovelistAPI(client, jobs)

    with pytest.raises(JobTimeoutError):
        await api.wait_for_run("run-timeout", timeout_s=0.01, poll_interval_s=0.01)
