"""Startup maintenance task for existing generation Book Map backfill."""
from app.celery_app import celery_app
from app.services.book_map import backfill_missing_book_maps


@celery_app.task(name="shreckworld.book_map.backfill")
def backfill_book_maps_on_startup() -> dict[str, int]:
    return backfill_missing_book_maps(batch_size=100)
