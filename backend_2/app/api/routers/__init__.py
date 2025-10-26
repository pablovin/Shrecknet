from fastapi import APIRouter

from app.api.routers import audit_logs, auth, media, ontologies, users


def get_api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(auth.router)
    router.include_router(users.router)
    router.include_router(media.router)
    router.include_router(audit_logs.router)
    router.include_router(ontologies.router)
    return router
