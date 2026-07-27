from __future__ import annotations

import logging
import threading
import time

from celery import Celery
from celery.signals import before_task_publish, task_prerun, worker_ready
from kombu import Queue

from app.core.config_store import get_settings, reload_settings

celery_app = Celery("backend_2")
_stale_reaper_thread_started = False
_stale_reaper_lock = threading.Lock()


def configure_celery_app() -> None:
    settings = get_settings()
    celery_app.conf.broker_url = settings.celery_broker_url
    celery_app.conf.result_backend = settings.celery_result_backend
    celery_app.conf.task_default_queue = "ontology_linking"
    celery_app.conf.task_queues = (
        Queue("ontology_linking"),
        Queue("architect"),
        Queue("character_agent"),
    )
    celery_app.conf.task_routes = {
        "architect.analyze_instance": {"queue": "architect"},
        "architect.generate_entities": {"queue": "architect"},
        "character_agent.generate_embodiment": {"queue": "character_agent"},
        "character_agent.query": {"queue": "character_agent"},
    }
    celery_app.conf.task_always_eager = settings.celery_task_always_eager
    celery_app.conf.task_eager_propagates = True
    celery_app.conf.timezone = "UTC"


configure_celery_app()

celery_app.autodiscover_tasks(["app.tasks"])

logger = logging.getLogger(__name__)


@celery_app.on_after_configure.connect
def _log_celery_configuration(sender, **_kwargs):  # pragma: no cover - startup log
    logger.info(
        "Configured Celery app '%s' (broker=%s backend=%s eager=%s)",
        sender.main,
        sender.conf.broker_url,
        sender.conf.result_backend,
        sender.conf.task_always_eager,
    )


@task_prerun.connect
def _reload_settings_for_task(*_args, **_kwargs) -> None:  # pragma: no cover - startup log
    # Ensure workers pick up config changes made via the API/UI.
    reload_settings()
    configure_celery_app()


@before_task_publish.connect
def _stamp_enqueue_time(headers=None, **_kwargs) -> None:  # pragma: no cover - startup hook
    if isinstance(headers, dict) and "x-enqueued-at" not in headers:
        headers["x-enqueued-at"] = int(time.time())


def _run_stale_reaper_once() -> None:
    settings = get_settings()
    if not settings.celery_stale_reaper_enabled:
        return
    try:
        from app.celery_queue_reaper import reap_stale_celery_messages

        stats = reap_stale_celery_messages()
        logger.debug("Celery stale reaper stats: %s", stats)
    except Exception:  # pragma: no cover - defensive startup logic
        logger.exception("Celery stale queue reaper failed")


def _stale_reaper_loop() -> None:
    while True:
        settings = get_settings()
        sleep_for = max(30, int(settings.celery_stale_reaper_interval_seconds))
        time.sleep(sleep_for)
        reload_settings()
        configure_celery_app()
        _run_stale_reaper_once()


def _warmup_embedding_model_if_enabled() -> None:
    settings = get_settings()
    if not settings.elder_embedding_warmup_on_worker_start:
        return

    def _warmup() -> None:
        try:
            from app.graphrag.embedding_service import get_embedding_model

            started = time.monotonic()
            get_embedding_model()
            elapsed_ms = round((time.monotonic() - started) * 1000, 2)
            logger.info("Embedding model warmup complete duration_ms=%s", elapsed_ms)
        except Exception:
            logger.exception("Embedding model warmup failed")

    thread = threading.Thread(
        target=_warmup,
        name="embedding-model-warmup",
        daemon=True,
    )
    thread.start()


def _clear_librarian_ingestion_locks_on_worker_start() -> None:
    """A job reset also invalidates locks left by interrupted PDF ingestion."""
    try:
        from app.graph.neo4j import get_driver
        from app.utils.async_helpers import run_async

        async def _clear() -> int:
            settings = get_settings()
            driver = get_driver()
            async with driver.session(database=settings.neo4j_database) as session:
                result = await session.run(
                    "MATCH (lock:PdfIngestionLock) DELETE lock RETURN count(lock) AS deleted"
                )
                record = await result.single()
                return int(record["deleted"] if record else 0)

        deleted = run_async(_clear())
        if deleted:
            logger.warning("Cleared %s stale Librarian ingestion locks on worker startup", deleted)
    except Exception:  # pragma: no cover - startup must not prevent worker boot
        logger.exception("Unable to clear Librarian ingestion locks on worker startup")


@worker_ready.connect
def _start_stale_reaper(sender=None, **_kwargs) -> None:  # pragma: no cover - startup hook
    global _stale_reaper_thread_started
    try:
        from app.celery_queue_reaper import reset_jobs_and_queues_on_startup

        reset_jobs_and_queues_on_startup()
    except Exception:  # pragma: no cover - defensive startup logic
        logger.exception("Startup job/queue reset failed")
    _clear_librarian_ingestion_locks_on_worker_start()
    _warmup_embedding_model_if_enabled()
    _run_stale_reaper_once()
    with _stale_reaper_lock:
        if _stale_reaper_thread_started:
            return
        thread = threading.Thread(
            target=_stale_reaper_loop,
            name="celery-stale-reaper",
            daemon=True,
        )
        thread.start()
        _stale_reaper_thread_started = True
        logger.info(
            "Started Celery stale reaper thread (interval=%ss)",
            max(30, int(get_settings().celery_stale_reaper_interval_seconds)),
        )
