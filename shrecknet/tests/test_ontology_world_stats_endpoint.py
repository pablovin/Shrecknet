from __future__ import annotations

import json

import pytest

from app.api.routers.ontologies import _world_stats_cache
from app.core.security import create_access_token
from app.models import AuthorType, Ontology, OntologyEntity, OntologyInstance, User, UserRole


async def _create_user(session_maker, role: UserRole) -> dict[str, str]:
    async with session_maker() as session:
        user = User(
            username=f"{role.value}-world-stats-user",
            hashed_password="hashed",
            password="",
            full_name=f"{role.value.title()} User",
            email=f"{role.value}-world-stats@example.com",
            timezone="UTC",
            role=role,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    token = create_access_token(str(user.id), role.value)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_world_stats_aggregates_counts_per_ontology(client, session_maker) -> None:
    headers = await _create_user(session_maker, UserRole.ADMIN)

    async with session_maker() as session:
        ont1 = Ontology(name="World A")
        ont2 = Ontology(name="World B")
        session.add_all([ont1, ont2])
        await session.flush()

        session.add_all(
            [
                OntologyEntity(
                    ontology_id=ont1.id,
                    name="Character",
                    description=None,
                    image_url=None,
                    keywords=[],
                    display_on_world=True,
                    auto_generatable=False,
                    author_type=AuthorType.HUMAN,
                    user_id="1",
                    agent_id=None,
                ),
                OntologyEntity(
                    ontology_id=ont1.id,
                    name="Place",
                    description=None,
                    image_url=None,
                    keywords=[],
                    display_on_world=True,
                    auto_generatable=False,
                    author_type=AuthorType.HUMAN,
                    user_id="1",
                    agent_id=None,
                ),
                OntologyEntity(
                    ontology_id=ont2.id,
                    name="Faction",
                    description=None,
                    image_url=None,
                    keywords=[],
                    display_on_world=True,
                    auto_generatable=False,
                    author_type=AuthorType.HUMAN,
                    user_id="1",
                    agent_id=None,
                ),
            ]
        )

        payload_1 = {
            "scenes": [
                {"milestones": [{"id": "m1"}, {"id": "m2"}]},
                {"milestones": [{"id": "m3"}]},
            ]
        }
        payload_2 = {
            "scenes": [
                {"milestones": []},
                {"name": "Scene without milestones"},
            ]
        }
        session.add_all(
            [
                OntologyInstance(
                    instance_id="inst-1",
                    ontology_id=ont1.id,
                    name="Page 1",
                    payload_json=json.dumps(payload_1),
                ),
                OntologyInstance(
                    instance_id="inst-2",
                    ontology_id=ont1.id,
                    name="Page 2",
                    payload_json=json.dumps(payload_2),
                ),
            ]
        )

        await session.commit()

    _world_stats_cache.clear()
    response = await client.get("/ontologies/world-stats", headers=headers)
    assert response.status_code == 200, response.text

    body = response.json()
    assert len(body["results"]) == 2

    first = body["results"][0]
    second = body["results"][1]

    assert first["entity_type_count"] == 2
    assert first["page_count"] == 2
    assert first["scene_count"] == 4
    assert first["milestone_count"] == 3
    assert isinstance(first["updated_at"], str)

    assert second["entity_type_count"] == 1
    assert second["page_count"] == 0
    assert second["scene_count"] == 0
    assert second["milestone_count"] == 0


@pytest.mark.asyncio
async def test_world_stats_supports_csv_filter_and_disable_content_counts(
    client, session_maker
) -> None:
    headers = await _create_user(session_maker, UserRole.ADMIN)
    selected_ontology_id: int | None = None

    async with session_maker() as session:
        ont1 = Ontology(name="World C")
        ont2 = Ontology(name="World D")
        session.add_all([ont1, ont2])
        await session.flush()
        selected_ontology_id = ont2.id

        session.add_all(
            [
                OntologyEntity(
                    ontology_id=ont1.id,
                    name="Entity A",
                    description=None,
                    image_url=None,
                    keywords=[],
                    display_on_world=True,
                    auto_generatable=False,
                    author_type=AuthorType.HUMAN,
                    user_id="1",
                    agent_id=None,
                ),
                OntologyEntity(
                    ontology_id=ont2.id,
                    name="Entity B",
                    description=None,
                    image_url=None,
                    keywords=[],
                    display_on_world=True,
                    auto_generatable=False,
                    author_type=AuthorType.HUMAN,
                    user_id="1",
                    agent_id=None,
                ),
            ]
        )
        session.add(
            OntologyInstance(
                instance_id="inst-3",
                ontology_id=ont2.id,
                name="Page 3",
                payload_json=json.dumps({"scenes": [{"milestones": [{"id": "m4"}]}]}),
            )
        )
        await session.commit()

    _world_stats_cache.clear()
    assert selected_ontology_id is not None
    response = await client.get(
        "/ontologies/world-stats",
        headers=headers,
        params={
            "ontology_ids": str(selected_ontology_id),
            "include_content_counts": "false",
        },
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert len(body["results"]) == 1
    item = body["results"][0]
    assert item["ontology_id"] == selected_ontology_id
    assert item["entity_type_count"] == 1
    assert item["page_count"] == 0
    assert item["scene_count"] == 0
    assert item["milestone_count"] == 0
