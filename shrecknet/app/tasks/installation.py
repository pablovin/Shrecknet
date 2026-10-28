"""One bounded, non-retrying setup check on the domain worker queue."""
import asyncio
from app.celery_app import celery_app
from app.services.installation_service import InstallationService

@celery_app.task(name="installation.verify_models", ignore_result=True)
def verify_models(verification_id: str) -> None:
    asyncio.run(InstallationService().verify_models(verification_id))
