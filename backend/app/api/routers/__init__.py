from fastapi import APIRouter

from app.api.routers import (
    admin_notes,
    agents,
    novelist,
    architect,
    audit_logs,
    auth,
    background_jobs,
    backups,
    configurations,
    elder,
    elder_chats,
    games,
    graphrag,
    imports,
    library,
    librarian,
    legacy_export,
    media,
    notes,
    notifications,
    ontologies,
    ontology_instances,
    page_visits,
    llm_status,
    setup,
    users,
)


def get_api_router() -> APIRouter:
    router = APIRouter()
    router.include_router(auth.router)
    router.include_router(users.router)
    router.include_router(media.router)
    router.include_router(configurations.router)
    router.include_router(games.router)
    router.include_router(graphrag.router)
    router.include_router(imports.router)
    router.include_router(library.router)
    router.include_router(notes.router)
    router.include_router(admin_notes.router)
    router.include_router(notifications.router)
    router.include_router(ontology_instances.router)
    router.include_router(audit_logs.router)
    router.include_router(ontologies.router)
    router.include_router(page_visits.router)
    router.include_router(agents.router)
    router.include_router(novelist.router)
    router.include_router(elder.router)
    router.include_router(elder_chats.router)
    router.include_router(librarian.router)
    router.include_router(architect.router)
    router.include_router(legacy_export.router)
    router.include_router(background_jobs.router)
    router.include_router(backups.router)
    router.include_router(llm_status.router)
    router.include_router(setup.router)
    return router
