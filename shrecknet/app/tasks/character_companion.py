"""Celery entry point for durable companion outbox items."""
from app.celery_app import celery_app
from app.utils.async_helpers import run_async
from app.jobs.companion.runner import process_outbox


@celery_app.task(name="companion.process_outbox", acks_late=True, reject_on_worker_lost=True)
def process_companion_outbox(identifier):
    run_async(process_outbox(identifier))
