from __future__ import annotations

from types import SimpleNamespace

import pytest


async def _create_ontology(client, headers) -> int:
    response = await client.post(
        "/ontologies/",
        json={"name": "Novelist API Test", "description": "test"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _create_agent(client, headers, *, ontology_id: int, job: str) -> str:
    response = await client.post(
        "/agents/",
        json={
            "name": f"agent-{job}-{ontology_id}",
            "description": "test",
            "writing_style": "test",
            "job": job,
            "active": True,
            "ontology_ids": [ontology_id],
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


@pytest.mark.asyncio
async def test_novelist_timeline_generate_accepts_valid_payload(
    client,
    admin_token,
    monkeypatch,
):
    headers = {"Authorization": f"Bearer {admin_token}"}
    ontology_id = await _create_ontology(client, headers)
    agent_id = await _create_agent(client, headers, ontology_id=ontology_id, job="novelist")

    monkeypatch.setattr("app.api.routers.novelist.is_openai_configured", lambda _s: True)

    async def _entity_exists(_entity_instance_id: str) -> bool:
        return True

    monkeypatch.setattr("app.api.routers.novelist._entity_exists", _entity_exists)

    def _fake_delay(**_kwargs):
        return SimpleNamespace(id="task-123")

    monkeypatch.setattr(
        "app.tasks.novelist_timeline_generation.generate_timeline_for_entity.delay",
        _fake_delay,
    )

    response = await client.post(
        f"/jobs/novelist/{agent_id}/timeline/generate",
        json={"entity_instance_id": "entity-1", "max_events": 3, "force": False},
        headers=headers,
    )

    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["task_id"] == "task-123"
    assert payload["entity_instance_id"] == "entity-1"


@pytest.mark.asyncio
async def test_novelist_timeline_generate_rejects_non_novelist_agent(
    client,
    admin_token,
    monkeypatch,
):
    headers = {"Authorization": f"Bearer {admin_token}"}
    ontology_id = await _create_ontology(client, headers)
    agent_id = await _create_agent(client, headers, ontology_id=ontology_id, job="elder")

    monkeypatch.setattr("app.api.routers.novelist.is_openai_configured", lambda _s: True)

    response = await client.post(
        f"/jobs/novelist/{agent_id}/timeline/generate",
        json={"entity_instance_id": "entity-1"},
        headers=headers,
    )

    assert response.status_code == 400
    assert "not 'novelist'" in response.json()["detail"]


@pytest.mark.asyncio
async def test_novelist_timeline_generate_rejects_missing_entity(
    client,
    admin_token,
    monkeypatch,
):
    headers = {"Authorization": f"Bearer {admin_token}"}
    ontology_id = await _create_ontology(client, headers)
    agent_id = await _create_agent(client, headers, ontology_id=ontology_id, job="novelist")

    monkeypatch.setattr("app.api.routers.novelist.is_openai_configured", lambda _s: True)

    async def _entity_exists(_entity_instance_id: str) -> bool:
        return False

    monkeypatch.setattr("app.api.routers.novelist._entity_exists", _entity_exists)

    response = await client.post(
        f"/jobs/novelist/{agent_id}/timeline/generate",
        json={"entity_instance_id": "missing"},
        headers=headers,
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Entity not found"


@pytest.mark.asyncio
async def test_novelist_timeline_generate_rejects_when_openai_not_configured(
    client,
    admin_token,
    monkeypatch,
):
    headers = {"Authorization": f"Bearer {admin_token}"}
    ontology_id = await _create_ontology(client, headers)
    agent_id = await _create_agent(client, headers, ontology_id=ontology_id, job="novelist")

    monkeypatch.setattr("app.api.routers.novelist.is_openai_configured", lambda _s: False)

    response = await client.post(
        f"/jobs/novelist/{agent_id}/timeline/generate",
        json={"entity_instance_id": "entity-1"},
        headers=headers,
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "OpenAI API key not configured"
