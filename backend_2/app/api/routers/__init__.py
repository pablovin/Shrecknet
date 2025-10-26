from fastapi import APIRouter

from app.api.routers import ontologies


def get_api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(ontologies.router)
    return router
