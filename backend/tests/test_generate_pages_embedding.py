import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from app.config import settings
from app.models.model_world_embedding import WorldEmbedding
from app.crud import crud_world_embedding

WORLD_BUILDER = {
    "nickname": "builder2",
    "email": "builder2@example.com",
    "password": "pass",
    "role": "world builder",
    "image_url": "no image",
}
ADMIN = {
    "nickname": "admin2",
    "email": "admin2@example.com",
    "password": "pass",
    "role": "system admin",
    "image_url": "no image",
}
WRITER = {
    "nickname": "writer2",
    "email": "writer2@example.com",
    "password": "pass",
    "role": "writer",
    "image_url": "no image",
}


@pytest.mark.anyio
async def test_generate_pages_triggers_embedding(
    async_client, create_user, login_and_get_token, session, tmp_path
):
    await create_user(**WORLD_BUILDER)
    await create_user(**ADMIN)
    await create_user(**WRITER)

    wb_token = await login_and_get_token(
        WORLD_BUILDER["email"], WORLD_BUILDER["password"], WORLD_BUILDER["role"]
    )
    admin_token = await login_and_get_token(
        ADMIN["email"], ADMIN["password"], ADMIN["role"]
    )
    writer_token = await login_and_get_token(
        WRITER["email"], WRITER["password"], WRITER["role"]
    )

    gw_payload = {
        "name": "EmbedWorld2",
        "system": "sys",
        "description": "d",
        "logo": "logo",
    }
    resp = await async_client.post(
        "/gameworlds/", json=gw_payload, headers={"Authorization": f"Bearer {wb_token}"}
    )
    assert resp.status_code == 200
    gw_id = resp.json()["id"]

    concept_payload = {"gameworld_id": gw_id, "name": "Clan", "description": "c"}
    resp = await async_client.post(
        "/concepts/",
        json=concept_payload,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    concept_id = resp.json()["id"]

    page_payload = {
        "gameworld_id": gw_id,
        "concept_id": concept_id,
        "name": "Base",
        "content": "txt",
    }
    resp = await async_client.post(
        "/pages/",
        json=page_payload,
        headers={"Authorization": f"Bearer {writer_token}"},
    )
    assert resp.status_code == 200
    base_page_id = resp.json()["id"]

    agent_payload = {"name": "Writer", "world_id": gw_id}
    resp = await async_client.post(
        "/agents/", json=agent_payload, headers={"Authorization": f"Bearer {wb_token}"}
    )
    assert resp.status_code == 200
    agent_id = resp.json()["id"]

    emb = WorldEmbedding(world_id=gw_id, name="base")
    await crud_world_embedding.create_embedding(session, emb)

    settings.world_embedding_job_dir = str(tmp_path)
    Path(settings.world_embedding_job_dir).mkdir(parents=True, exist_ok=True)

    with (
        patch("app.crud.crud_vectordb.add_page", new_callable=AsyncMock) as add_page,
        patch("app.crud.crud_vectordb.delete_page") as delete_page,
        patch(
            "app.crud.crud_world_embedding.update_embedding",
            new_callable=AsyncMock,
        ) as update_emb,
    ):
        payload = {
            "pages": [
                {
                    "name": "NewPage",
                    "concept_id": concept_id,
                    "source_page_ids": [base_page_id],
                }
            ]
        }
        resp = await async_client.post(
            f"/agents/{agent_id}/pages/{base_page_id}/generate",
            json=payload,
            headers={"Authorization": f"Bearer {writer_token}"},
        )
        assert resp.status_code == 200
        assert add_page.await_count > 0
        assert delete_page.called
        assert update_emb.await_count > 0
        args, _ = update_emb.await_args
        assert args[2]["page_count"] == 2
    job_files = list(Path(settings.world_embedding_job_dir).glob("*.json"))
    assert len(job_files) == 0
