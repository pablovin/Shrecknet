"""Check worker startup in a fresh process, without API-side task imports."""
import subprocess
import sys
from pathlib import Path


def test_worker_registers_indexing_task_without_importing_api():
    result = subprocess.run(
        [sys.executable, "-c", '''
from app.celery_app import celery_app
celery_app.loader.import_default_modules()
celery_app.finalize()
name = "shreckworld.embed.library_item"
assert name in celery_app.tasks, list(celery_app.tasks)
assert celery_app.tasks[name].run.__module__ == "app.tasks.embedding"
route = celery_app.amqp.router.route({}, name, args=("item", "job"))
assert route["queue"].name == "shreckworld_embedding", route
'''],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
