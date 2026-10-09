import asyncio

import pytest

from shrecknet_client.errors import JobFailedError
from shrecknet_client.models import BackgroundJobRecord
from shrecknet_client.resources import CharacterAgentsAPI, JobHandle, JobsAPI, _normalize_job


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


@pytest.mark.asyncio
async def test_list_embodiment_drafts_returns_resume_summaries() -> None:
    class C:
        async def raw_request(self, method, path, params=None, json=None):
            assert method == "GET"
            assert path == "/character-agents/embodiment-drafts"
            assert params == {"ontology_id": 12, "limit": 20}
            return [{
                "id": "draft-1", "ontology_id": 12, "source_entity_id": "entity-mara",
                "target_character_agent_id": None, "status": "ready", "background_job_id": 42,
                "error_message": None, "created_at": "2026-10-08T10:00:00Z",
                "updated_at": "2026-10-08T10:01:00Z",
            }]

    summaries = await CharacterAgentsAPI(C()).list_embodiments(12)

    assert summaries[0].id == "draft-1"
    assert summaries[0].background_job_id == 42


@pytest.mark.asyncio
async def test_delete_embodiment_calls_admin_draft_endpoint() -> None:
    class C:
        async def raw_request(self, method, path, params=None, json=None):
            assert method == "DELETE"
            assert path == "/character-agents/embodiment-drafts/draft-1"
            return None

    await CharacterAgentsAPI(C()).delete_embodiment("draft-1")
