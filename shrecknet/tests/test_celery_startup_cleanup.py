from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from app.celery_queue_reaper import reset_jobs_and_queues_on_startup


def test_reset_jobs_and_queues_on_startup_marks_jobs_failed_and_purges_queues(
    tmp_path, monkeypatch
) -> None:
    jobs_db = tmp_path / "jobs.db"
    conn = sqlite3.connect(jobs_db.as_posix())
    conn.execute(
        """
        CREATE TABLE background_jobs (
            id INTEGER PRIMARY KEY,
            status TEXT NOT NULL,
            error_message TEXT,
            completed_at TEXT
        )
        """
    )
    conn.executemany(
        "INSERT INTO background_jobs (id, status, error_message, completed_at) VALUES (?, ?, ?, ?)",
        [
            (1, "queued", None, None),
            (2, "running", "", None),
            (3, "done", None, None),
        ],
    )
    conn.commit()
    conn.close()

    settings = SimpleNamespace(
        jobs_database_url=f"sqlite:///{jobs_db.as_posix()}",
        celery_broker_url="redis://unused:6379/0",
    )
    monkeypatch.setattr("app.celery_queue_reaper.get_settings", lambda: settings)

    class FakeRedisClient:
        def __init__(self) -> None:
            self.queues = {"ontology_linking": ["a", "b"], "architect": ["c"]}

        def llen(self, queue: str) -> int:
            return len(self.queues.get(queue, []))

        def delete(self, queue: str) -> int:
            existed = queue in self.queues
            self.queues.pop(queue, None)
            return 1 if existed else 0

    fake_client = FakeRedisClient()
    monkeypatch.setattr(
        "app.celery_queue_reaper.redis.Redis.from_url",
        lambda *_args, **_kwargs: fake_client,
    )

    stats = reset_jobs_and_queues_on_startup()

    assert stats["failed_jobs"] == 2
    assert stats["queued_messages_inspected"] == 3
    assert stats["queues_purged"] == 2

    verify_conn = sqlite3.connect(jobs_db.as_posix())
    rows = verify_conn.execute(
        "SELECT id, status, error_message, completed_at FROM background_jobs ORDER BY id"
    ).fetchall()
    verify_conn.close()

    assert rows[0][1] == "failed"
    assert rows[0][2]
    assert rows[0][3]
    assert rows[1][1] == "failed"
    assert rows[1][2]
    assert rows[1][3]
    assert rows[2][1] == "done"
