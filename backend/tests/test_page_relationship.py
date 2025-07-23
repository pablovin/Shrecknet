import pytest
from .test_page import register_and_login, SYSTEM_ADMIN, WRITER

@pytest.mark.anyio
async def test_add_relationship_adds_inverse(async_client):
    sys_token = await register_and_login(async_client, SYSTEM_ADMIN)
    writer_token = await register_and_login(async_client, WRITER)

    gw = {"name": "RelWorld", "system": "sys", "description": "d", "logo": "l"}
    resp = await async_client.post("/gameworlds/", json=gw, headers={"Authorization": f"Bearer {sys_token}"})
    assert resp.status_code == 200
    gw_id = resp.json()["id"]

    concept = {"gameworld_id": gw_id, "name": "Clan", "description": "c"}
    resp = await async_client.post("/concepts/", json=concept, headers={"Authorization": f"Bearer {sys_token}"})
    assert resp.status_code == 200
    concept_id = resp.json()["id"]

    p1 = {"gameworld_id": gw_id, "concept_id": concept_id, "name": "A"}
    resp = await async_client.post("/pages/", json=p1, headers={"Authorization": f"Bearer {writer_token}"})
    assert resp.status_code == 200
    p1_id = resp.json()["id"]

    p2 = {"gameworld_id": gw_id, "concept_id": concept_id, "name": "B"}
    resp = await async_client.post("/pages/", json=p2, headers={"Authorization": f"Bearer {writer_token}"})
    assert resp.status_code == 200
    p2_id = resp.json()["id"]

    rel_payload = {"page_id": p1_id, "target_page_id": p2_id, "relationship_type": "friend", "author_type": "user", "author_id": 1}
    resp = await async_client.post(f"/pages/{p1_id}/relationships/", json=rel_payload, headers={"Authorization": f"Bearer {writer_token}"})
    assert resp.status_code == 200

    resp = await async_client.get(f"/pages/{p2_id}", headers={"Authorization": f"Bearer {writer_token}"})
    assert resp.status_code == 200
    rels = resp.json()["relationship_map"]
    assert any(r["page_id"] == p2_id and r["target_page_id"] == p1_id and r["direction"] == "incoming" for r in rels)
