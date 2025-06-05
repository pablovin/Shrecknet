import os
import asyncio
from celery import Celery

celery_broker = os.getenv("CELERY_BROKER_URL")
celery_backend = os.getenv("CELERY_RESULT_BACKEND")

use_celery = bool(celery_broker)

celery_app = Celery(
    "shrecknet",
    broker=celery_broker or "redis://localhost:6379/0",
    backend=celery_backend or "redis://localhost:6379/0",
)

celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.broker_connection_retry_on_startup = True

from app.crud.crud_page_links_update import (
    auto_crosslink_page_content,
    auto_crosslink_batch,
    remove_crosslinks_to_page,
    remove_page_refs_from_characteristics,
)

@celery_app.task
def task_auto_crosslink_page_content(page_id: int):
    asyncio.run(auto_crosslink_page_content(page_id))

@celery_app.task
def task_auto_crosslink_batch(page_id: int):
    asyncio.run(auto_crosslink_batch(page_id))

@celery_app.task
def task_remove_crosslinks_to_page(page_id: int):
    asyncio.run(remove_crosslinks_to_page(page_id))

@celery_app.task
def task_remove_page_refs_from_characteristics(page_id: int):
    asyncio.run(remove_page_refs_from_characteristics(page_id))


async def queue_auto_crosslink_page_content(page_id: int, *, session=None):
    """Queue or run the cross-link update for a single page."""
    if use_celery:
        task_auto_crosslink_page_content.delay(page_id)
    else:
        # Skip in non-Celery environments
        return


async def queue_auto_crosslink_batch(page_id: int, *, session=None):
    """Queue or run batch cross-linking for a new page."""
    if use_celery:
        task_auto_crosslink_batch.delay(page_id)
    else:
        return


async def queue_remove_crosslinks_to_page(page_id: int, *, session=None):
    """Queue or run link removal for a deleted page."""
    if use_celery:
        task_remove_crosslinks_to_page.delay(page_id)
    else:
        return


async def queue_remove_page_refs_from_characteristics(page_id: int, *, session=None):
    """Queue or run page reference cleanup."""
    if use_celery:
        task_remove_page_refs_from_characteristics.delay(page_id)
    else:
        return
