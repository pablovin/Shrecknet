import asyncio

import pytest

from shrecknet_client.errors import JobFailedError
from shrecknet_client.models import BackgroundJobRecord
from shrecknet_client.resources import JobHandle, JobsAPI, _normalize_job


class DummyClient:
    async def raw_request(self, method, path, params=None, json=None):
        raise RuntimeError("not used")


def test_normalize_frontend_job() -> None:
    rec = _normalize_job({"kind": "neo4j_embedding", "job_id": "10", "status": "queued", "details": "{}"})
    assert rec.id == 10
    assert rec.job_type == "neo4j_embedding"


@pytest.mark.asyncio
async def test_job_handle_raises_if_failed() -> None:
    api = JobsAPI(DummyClient())
    job = BackgroundJobRecord(id=5, job_type="x", status="failed", error_message="boom", raw={})
    handle = JobHandle(api, job)
    with pytest.raises(JobFailedError):
        handle.raise_if_failed()


@pytest.mark.asyncio
async def test_wait_terminal_immediate() -> None:
    class C:
        async def raw_request(self, method, path, params=None, json=None):
            return {"id": 1, "job_type": "neo4j_embedding", "status": "done", "progress": 1.0}

    api = JobsAPI(C())
    result = await api.wait(1, timeout_s=1)
    assert result.status == "done"
