from __future__ import annotations

import logging

from celery import Celery

from app.core.config_store import get_settings

celery_app = Celery("backend_2")


def configure_celery_app() -> None:
    settings = get_settings()
    celery_app.conf.broker_url = settings.celery_broker_url
    celery_app.conf.result_backend = settings.celery_result_backend
    celery_app.conf.task_default_queue = "ontology_linking"
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
