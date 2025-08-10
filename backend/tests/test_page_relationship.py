import pytest
from .test_page import register_and_login, SYSTEM_ADMIN, WRITER
from app.models.model_page import PageRelationship


@pytest.mark.anyio
async def test_add_relationship_adds_inverse(async_client):
    sys_token = await register_and_login(async_client, SYSTEM_ADMIN)
    writer_token = await register_and_login(async_client, WRITER)

    gw = {"name": "RelWorld", "system": "sys", "description": "d", "logo": "l"}
    resp = await async_client.post(
        "/gameworlds/", json=gw, headers={"Authorization": f"Bearer {sys_token}"}
    )
    assert resp.status_code == 200
    gw_id = resp.json()["id"]

    concept = {"gameworld_id": gw_id, "name": "Clan", "description": "c"}
    resp = await async_client.post(
        "/concepts/", json=concept, headers={"Authorization": f"Bearer {sys_token}"}
    )
    assert resp.status_code == 200
    concept_id = resp.json()["id"]

    p1 = {"gameworld_id": gw_id, "concept_id": concept_id, "name": "A"}
    resp = await async_client.post(
        "/pages/", json=p1, headers={"Authorization": f"Bearer {writer_token}"}
    )
    assert resp.status_code == 200
    p1_id = resp.json()["id"]

    p2 = {"gameworld_id": gw_id, "concept_id": concept_id, "name": "B"}
    resp = await async_client.post(
        "/pages/", json=p2, headers={"Authorization": f"Bearer {writer_token}"}
    )
    assert resp.status_code == 200
    p2_id = resp.json()["id"]

    rel_payload = {
        "page_id": p1_id,
        "target_page_id": p2_id,
        "relationship_type": "friend",
        "author_type": "user",
        "author_id": 1,
    }
    resp = await async_client.post(
        f"/pages/{p1_id}/relationships/",
        json=rel_payload,
        headers={"Authorization": f"Bearer {writer_token}"},
    )
    assert resp.status_code == 200

    resp = await async_client.get(
        f"/pages/{p2_id}", headers={"Authorization": f"Bearer {writer_token}"}
    )
    assert resp.status_code == 200
    rels = resp.json()["relationship_map"]
    assert any(
        r["page_id"] == p2_id
        and r["target_page_id"] == p1_id
        and r["direction"] == "incoming"
        for r in rels
    )


@pytest.mark.anyio
async def test_add_relationship_no_duplicate_inverse(async_client):
    sys_token = await register_and_login(async_client, SYSTEM_ADMIN)
    writer_token = await register_and_login(async_client, WRITER)

    gw = {"name": "RelWorld2", "system": "sys", "description": "d", "logo": "l"}
    resp = await async_client.post(
        "/gameworlds/", json=gw, headers={"Authorization": f"Bearer {sys_token}"}
    )
    assert resp.status_code == 200
    gw_id = resp.json()["id"]

    concept = {"gameworld_id": gw_id, "name": "Clan", "description": "c"}
    resp = await async_client.post(
        "/concepts/", json=concept, headers={"Authorization": f"Bearer {sys_token}"}
    )
    assert resp.status_code == 200
    concept_id = resp.json()["id"]

    p1 = {"gameworld_id": gw_id, "concept_id": concept_id, "name": "A"}
    resp = await async_client.post(
        "/pages/", json=p1, headers={"Authorization": f"Bearer {writer_token}"}
    )
    assert resp.status_code == 200
    p1_id = resp.json()["id"]

    p2 = {"gameworld_id": gw_id, "concept_id": concept_id, "name": "B"}
    resp = await async_client.post(
        "/pages/", json=p2, headers={"Authorization": f"Bearer {writer_token}"}
    )
    assert resp.status_code == 200
    p2_id = resp.json()["id"]

    rel_payload = {
        "page_id": p1_id,
        "target_page_id": p2_id,
        "relationship_type": "friend",
        "author_type": "user",
        "author_id": 1,
    }
    resp = await async_client.post(
        f"/pages/{p1_id}/relationships/",
        json=rel_payload,
        headers={"Authorization": f"Bearer {writer_token}"},
    )
    assert resp.status_code == 200

    resp = await async_client.get(
        f"/pages/{p2_id}", headers={"Authorization": f"Bearer {writer_token}"}
    )
    rels = resp.json()["relationship_map"]
    count = sum(
        1
        for r in rels
        if r["page_id"] == p2_id
        and r["target_page_id"] == p1_id
        and r["direction"] == "incoming"
    )
    assert count == 1


@pytest.mark.anyio
async def test_add_relationship_handles_existing_inverse_duplicates(
    async_client, session
):
    sys_token = await register_and_login(async_client, SYSTEM_ADMIN)
    writer_token = await register_and_login(async_client, WRITER)

    gw = {"name": "RelWorld3", "system": "sys", "description": "d", "logo": "l"}
    resp = await async_client.post(
        "/gameworlds/",
        json=gw,
        headers={"Authorization": f"Bearer {sys_token}"},
    )
    assert resp.status_code == 200
    gw_id = resp.json()["id"]

    concept = {"gameworld_id": gw_id, "name": "Clan", "description": "c"}
    resp = await async_client.post(
        "/concepts/",
        json=concept,
        headers={"Authorization": f"Bearer {sys_token}"},
    )
    assert resp.status_code == 200
    concept_id = resp.json()["id"]

    p1 = {"gameworld_id": gw_id, "concept_id": concept_id, "name": "A"}
    resp = await async_client.post(
        "/pages/",
        json=p1,
        headers={"Authorization": f"Bearer {writer_token}"},
    )
    assert resp.status_code == 200
    p1_id = resp.json()["id"]

    p2 = {"gameworld_id": gw_id, "concept_id": concept_id, "name": "B"}
    resp = await async_client.post(
        "/pages/",
        json=p2,
        headers={"Authorization": f"Bearer {writer_token}"},
    )
    assert resp.status_code == 200
    p2_id = resp.json()["id"]

    dup1 = PageRelationship(
        page_id=p2_id,
        target_page_id=p1_id,
        relationship_type="friend",
        direction="incoming",
        author_type="user",
        author_id=1,
    )
    dup2 = PageRelationship(
        page_id=p2_id,
        target_page_id=p1_id,
        relationship_type="friend",
        direction="incoming",
        author_type="user",
        author_id=1,
    )
    session.add_all([dup1, dup2])
    await session.commit()

    rel_payload = {
        "page_id": p1_id,
        "target_page_id": p2_id,
        "relationship_type": "friend",
        "author_type": "user",
        "author_id": 1,
    }
    resp = await async_client.post(
        f"/pages/{p1_id}/relationships/",
        json=rel_payload,
        headers={"Authorization": f"Bearer {writer_token}"},
    )
    assert resp.status_code == 200

    resp = await async_client.get(
        f"/pages/{p1_id}",
        headers={"Authorization": f"Bearer {writer_token}"},
    )
    rels = resp.json()["relationship_map"]
    count = sum(
        1
        for r in rels
        if r["page_id"] == p1_id
        and r["target_page_id"] == p2_id
        and r["direction"] == "outgoing"
    )
    assert count == 1


@pytest.mark.anyio
async def test_add_relationship_skips_if_target_has_any(async_client, session):
    sys_token = await register_and_login(async_client, SYSTEM_ADMIN)
    writer_token = await register_and_login(async_client, WRITER)

    gw = {"name": "RelWorld4", "system": "sys", "description": "d", "logo": "l"}
    resp = await async_client.post(
        "/gameworlds/",
        json=gw,
        headers={"Authorization": f"Bearer {sys_token}"},
    )
    assert resp.status_code == 200
    gw_id = resp.json()["id"]

    concept = {"gameworld_id": gw_id, "name": "Clan", "description": "c"}
    resp = await async_client.post(
        "/concepts/",
        json=concept,
        headers={"Authorization": f"Bearer {sys_token}"},
    )
    assert resp.status_code == 200
    concept_id = resp.json()["id"]

    p1 = {"gameworld_id": gw_id, "concept_id": concept_id, "name": "A"}
    resp = await async_client.post(
        "/pages/",
        json=p1,
        headers={"Authorization": f"Bearer {writer_token}"},
    )
    assert resp.status_code == 200
    p1_id = resp.json()["id"]

    p2 = {"gameworld_id": gw_id, "concept_id": concept_id, "name": "B"}
    resp = await async_client.post(
        "/pages/",
        json=p2,
        headers={"Authorization": f"Bearer {writer_token}"},
    )
    assert resp.status_code == 200
    p2_id = resp.json()["id"]

    existing = PageRelationship(
        page_id=p2_id,
        target_page_id=p1_id,
        relationship_type="friend",
        direction="outgoing",
        author_type="user",
        author_id=1,
    )
    session.add(existing)
    await session.commit()

    rel_payload = {
        "page_id": p1_id,
        "target_page_id": p2_id,
        "relationship_type": "friend",
        "author_type": "user",
        "author_id": 1,
    }
    resp = await async_client.post(
        f"/pages/{p1_id}/relationships/",
        json=rel_payload,
        headers={"Authorization": f"Bearer {writer_token}"},
    )
    assert resp.status_code == 200

    resp = await async_client.get(
        f"/pages/{p2_id}",
        headers={"Authorization": f"Bearer {writer_token}"},
    )
    rels = resp.json()["relationship_map"]
    outgoing = sum(
        1
        for r in rels
        if r["page_id"] == p2_id
        and r["target_page_id"] == p1_id
        and r["direction"] == "outgoing"
    )
    incoming = sum(
        1
        for r in rels
        if r["page_id"] == p2_id
        and r["target_page_id"] == p1_id
        and r["direction"] == "incoming"
    )
    assert outgoing == 1
    assert incoming == 0
