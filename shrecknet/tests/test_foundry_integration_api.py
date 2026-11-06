from types import SimpleNamespace

import pytest

from app.api.deps import get_current_admin_user
from app.main import app


@pytest.mark.asyncio
async def test_admin_binding_and_setup_key_contract(client, session_maker):
    from app.models.world import World
    async with session_maker() as session:
        session.add(World(id="a", name="A"))
        await session.commit()
    app.dependency_overrides[get_current_admin_user] = lambda: SimpleNamespace(id=1)
    response = await client.post("/config/integrations/foundry", json={"name": "Table", "allowed_world_ids": ["a"], "default_world_id": "a"})
    assert response.status_code == 201
    integration = response.json()
    headers = {"X-Shrecknet-Integration-Key": integration["key"]}
    validated = await client.post("/integrations/foundry/validate", headers=headers)
    assert validated.json()["default_world_id"] == "a"
    listed = await client.get("/config/integrations/foundry")
    assert "key" not in listed.json()[0]
    invalid = await client.put(f"/config/integrations/foundry/{integration['id']}", json={"allowed_world_ids": ["b"]})
    assert invalid.status_code == 422
    updated = await client.put(f"/config/integrations/foundry/{integration['id']}", json={"default_world_id": None})
    assert updated.json()["default_world_id"] is None
    rejected = await client.post("/config/integrations/foundry", json={"name": "Missing", "default_world_id": "missing"})
    assert rejected.status_code == 422
    await client.post(f"/config/integrations/foundry/{integration['id']}/revoke")
    assert (await client.post("/integrations/foundry/validate", headers=headers)).status_code == 401
    app.dependency_overrides.pop(get_current_admin_user)
    assert (await client.get("/config/integrations/foundry")).status_code == 401
    assert (await client.get("/integrations/foundry/worlds")).status_code == 401
