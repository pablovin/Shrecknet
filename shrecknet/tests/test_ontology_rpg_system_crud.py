from __future__ import annotations

import pytest

from app.core.security import create_access_token
from app.models import User, UserRole


async def _create_user(session_maker, role: UserRole) -> dict[str, str]:
    async with session_maker() as session:
        user = User(
            username=f"{role.value}-ontology-rpg-user",
            hashed_password="hashed",
            password="",
            full_name=f"{role.value.title()} User",
            email=f"{role.value}-ontology-rpg@example.com",
            timezone="UTC",
            role=role,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)

    token = create_access_token(str(user.id), role.value)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_ontology_rpg_system_create_update_and_filter(client, session_maker) -> None:
    headers = await _create_user(session_maker, UserRole.ADMIN)

    create_response = await client.post(
        "/ontologies/",
        headers=headers,
        json={
            "name": "World With RPG",
            "description": "Test world",
            "rpg_system": "Dungeons & Dragons 5e",
        },
    )
    assert create_response.status_code == 201
    created = create_response.json()
    ontology_id = created["id"]
    assert created["rpg_system"] == "Dungeons & Dragons 5e"

    update_response = await client.put(
        f"/ontologies/{ontology_id}",
        headers=headers,
        json={"rpg_system": "Pathfinder 2e"},
    )
    assert update_response.status_code == 200
    updated = update_response.json()
    assert updated["rpg_system"] == "Pathfinder 2e"

    get_response = await client.get(f"/ontologies/{ontology_id}", headers=headers)
    assert get_response.status_code == 200
    fetched = get_response.json()
    assert fetched["rpg_system"] == "Pathfinder 2e"

    list_response = await client.get(
        "/ontologies/",
        headers=headers,
        params={"rpg_system": "pathfinder"},
    )
    assert list_response.status_code == 200
    listed = list_response.json()
    assert any(item["id"] == ontology_id for item in listed)
