import json
from pathlib import Path
from types import SimpleNamespace
from contextlib import asynccontextmanager

import pytest

from app import task_queue


def test_task_generate_pages_job_writes_updated(tmp_path, monkeypatch):
    monkeypatch.setattr(task_queue.settings, "writer_job_dir", str(tmp_path))

    @asynccontextmanager
    async def dummy_session():
        yield None

    async def dummy_get_agent(_session, _agent_id):
        return SimpleNamespace(id=1, world_id=1)

    async def dummy_get_page(_session, _page_id):
        return SimpleNamespace(id=1, gameworld_id=1)

    async def dummy_generate_pages(_s, _a, _p, _pages, _merge):
        return {"pages": [{"id": 1}], "updated": [{"id": 2}]}

    monkeypatch.setattr(task_queue, "async_session_maker", dummy_session)
    monkeypatch.setattr(task_queue, "get_agent", dummy_get_agent)
    monkeypatch.setattr(task_queue, "get_page", dummy_get_page)
    monkeypatch.setattr(
        task_queue.crud_agent_writer, "generate_pages", dummy_generate_pages
    )

    task_queue.task_generate_pages_job(1, 1, [], "job1")

    job_dir = Path(tmp_path) / "job1"
    with open(job_dir / "job.json") as f:
        job_data = json.load(f)
    with open(job_dir / "generated.json") as f:
        gen_data = json.load(f)

    assert job_data["pages"] == [{"id": 1}]
    assert job_data["updated"] == [{"id": 2}]
    assert gen_data["pages"] == [{"id": 1}]
    assert gen_data["updated"] == [{"id": 2}]
