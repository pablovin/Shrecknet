from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from shrecknet_client.models import ArchitectAnalysisRequest, NovelistRunCreate
from shrecknet_client.resources import ArchitectAPI, NovelistAPI


@pytest.mark.asyncio
async def test_novelist_world_routes_accept_runs_without_agents():
    response = {
        "id": "run",
        "agent_id": None,
        "ontology_id": 7,
        "status": "pending",
        "stage": "ingest",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    client = SimpleNamespace(raw_request=AsyncMock(return_value=response))
    api = NovelistAPI(client, None)
    run = await api.start_world_run(7, NovelistRunCreate(unstructured_text="A story"))
    assert run.agent_id is None and run.ontology_id == 7
    assert client.raw_request.call_args.args == ("POST", "/jobs/novelist/worlds/7/runs")
    client.raw_request.return_value = [response]
    assert len(await api.list_world_runs(7)) == 1
    client.raw_request.return_value = {"deleted": 1}
    assert await api.delete_world_run(7, "run") == {"deleted": 1}
    assert client.raw_request.call_args.args == (
        "DELETE",
        "/jobs/novelist/worlds/7/runs/run",
    )


@pytest.mark.asyncio
async def test_architect_world_routes_preserve_world_and_source():
    response = {
        "id": "run",
        "agent_id": None,
        "ontology_id": 7,
        "ontology_instance_id": "source",
        "status": "pending",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    client = SimpleNamespace(raw_request=AsyncMock(return_value=response))
    api = ArchitectAPI(client, None, None, None)
    run = await api.analyze_world(
        7, ArchitectAnalysisRequest(ontology_instance_id="source", ontology_id=7)
    )
    assert run.agent_id is None and run.ontology_id == 7
    assert client.raw_request.call_args.args == (
        "POST",
        "/jobs/architect/worlds/7/analyze",
    )
    client.raw_request.return_value = [response]
    assert len(await api.list_world_runs(7)) == 1
    client.raw_request.return_value = {"deleted": 1}
    assert await api.delete_world_runs(7) == {"deleted": 1}
    assert client.raw_request.call_args.args == (
        "DELETE",
        "/jobs/architect/worlds/7/runs",
    )
