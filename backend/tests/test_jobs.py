import json
from pathlib import Path
import pytest

@pytest.mark.anyio
async def test_list_and_delete_jobs(async_client, create_user, login_and_get_token, tmp_path, monkeypatch):
    from app.config import settings

    settings.vectordb_job_dir = str(tmp_path / "vector")
    settings.writer_job_dir = str(tmp_path / "writer")
    settings.specialist_job_dir = str(tmp_path / "specialist")
    settings.novelist_job_dir = str(tmp_path / "novelist")
    settings.library_job_dir = str(tmp_path / "library")
    settings.world_embedding_job_dir = str(tmp_path / "embeddings")

    for p in [settings.vectordb_job_dir, settings.writer_job_dir, settings.specialist_job_dir, settings.novelist_job_dir, settings.library_job_dir, settings.world_embedding_job_dir]:
        Path(p).mkdir(parents=True, exist_ok=True)

    # create sample job files
    vec_job = Path(settings.vectordb_job_dir) / "v1.json"
    vec_job.write_text(json.dumps({"status": "done", "start_time": "2024"}))

    writer_dir = Path(settings.writer_job_dir) / "w1"
    writer_dir.mkdir(parents=True)
    writer_job = writer_dir / "job.json"
    writer_job.write_text(json.dumps({"status": "running", "start_time": "2024"}))

    novel_job = Path(settings.novelist_job_dir) / "n1.json"
    novel_job.write_text(json.dumps({"status": "error", "start_time": "2024"}))
    embed_job = Path(settings.world_embedding_job_dir) / "e1.json"
    embed_job.write_text(json.dumps({"status": "queued", "start_time": "2024"}))

    await create_user("admin@test.com", "pass", "system admin")
    token = await login_and_get_token("admin@test.com", "pass", "system admin")

    resp = await async_client.get("/jobs", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert any(j["job_id"] == "v1" and j["kind"] == "vectordb" for j in data)
    assert any(j["job_id"] == "w1" for j in data)
    assert any(j["job_id"] == "e1" and j["kind"] == "world_embedding" for j in data)

    # attempt delete (should remove done and error but not running)
    delete_payload = {
        "jobs": [
            {"kind": "vectordb", "job_id": "v1"},
            {"kind": "novelist", "job_id": "n1"},
            {"kind": "writer", "job_id": "w1"},
            {"kind": "world_embedding", "job_id": "e1"},
        ]
    }
    del_resp = await async_client.delete(
        "/jobs",
        json=delete_payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert del_resp.status_code == 200
    res = del_resp.json()
    assert {"kind": "vectordb", "job_id": "v1"} in res["deleted"]
    assert {"kind": "novelist", "job_id": "n1"} in res["deleted"]
    assert {"kind": "writer", "job_id": "w1"} not in res["deleted"]
    assert {"kind": "world_embedding", "job_id": "e1"} not in res["deleted"]

    # ensure files deleted appropriately
    assert not vec_job.exists()
    assert not novel_job.exists()
    assert writer_job.exists()
    assert embed_job.exists()
