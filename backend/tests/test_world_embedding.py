import pytest
from unittest.mock import patch
import json
from pathlib import Path

WORLD_BUILDER = {
    "nickname": "builder",
    "email": "builder_embed@example.com",
    "password": "pass",
    "role": "world builder",
    "image_url": "no image",
}
ADMIN = {
    "nickname": "admin",
    "email": "admin_embed@example.com",
    "password": "pass",
    "role": "system admin",
    "image_url": "no image",
}
WRITER = {
    "nickname": "writer",
    "email": "writer_embed@example.com",
    "password": "pass",
    "role": "writer",
    "image_url": "no image",
}

@pytest.mark.anyio
async def test_create_world_embedding(async_client, create_user, login_and_get_token, tmp_path):
    await create_user(**WORLD_BUILDER)
    await create_user(**ADMIN)
    await create_user(**WRITER)

    wb_token = await login_and_get_token(WORLD_BUILDER["email"], WORLD_BUILDER["password"], WORLD_BUILDER["role"])
    admin_token = await login_and_get_token(ADMIN["email"], ADMIN["password"], ADMIN["role"])
    writer_token = await login_and_get_token(WRITER["email"], WRITER["password"], WRITER["role"])

    gw_payload = {"name": "EmbedWorld", "system": "sys", "description": "d", "logo": "logo"}
    resp = await async_client.post("/gameworlds/", json=gw_payload, headers={"Authorization": f"Bearer {wb_token}"})
    assert resp.status_code == 200
    gw_id = resp.json()["id"]

    concept_payload = {"gameworld_id": gw_id, "name": "Clan", "description": "c"}
    resp = await async_client.post("/concepts/", json=concept_payload, headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 200
    concept_id = resp.json()["id"]

    for i in range(2):
        page = {"gameworld_id": gw_id, "concept_id": concept_id, "name": f"P{i}", "content": "txt"}
        resp = await async_client.post("/pages/", json=page, headers={"Authorization": f"Bearer {writer_token}"})
        assert resp.status_code == 200

    from app.config import settings
    settings.world_embedding_job_dir = str(tmp_path)
    Path(settings.world_embedding_job_dir).mkdir(parents=True, exist_ok=True)

    with patch("app.task_queue.task_rebuild_world_embedding.delay") as delay:
        payload = {"world_id": gw_id, "name": "base", "collection": f"world_{gw_id}_base"}
        resp = await async_client.post("/world_embeddings/", json=payload, headers={"Authorization": f"Bearer {admin_token}"})
        assert delay.called
    assert resp.status_code == 200
    data = resp.json()
    assert data["page_count"] is None
    job_files = list(Path(settings.world_embedding_job_dir).glob("*.json"))
    assert len(job_files) == 1
    with open(job_files[0]) as f:
        job = json.load(f)
    assert job["status"] == "queued"
