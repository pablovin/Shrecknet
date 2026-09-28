from app.celery_app import celery_app
from app.db.session import session_factory
from app.services.ingestion import embed_library_item


@celery_app.task(name="shreckworld.embed.library_item")
def embed_shreckworld_library_item(library_item_id: str, job_id: str) -> None:
    session = session_factory()()
    try:
        embed_library_item(session, library_item_id=library_item_id, job_id=job_id)
    finally:
        session.close()
