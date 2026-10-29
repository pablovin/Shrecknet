"""First deployment routes; the public route contains no settings or secrets."""
import httpx
from fastapi import APIRouter, Depends, HTTPException
from app.api.deps import get_media_service, get_ontology_service, get_user_service, require_roles, oauth2_scheme, get_db_session, get_ontology_instance_service
from app.api.routers.browser_auth import AuthRoute
from app.core.installation_claim import claim_required, local_browser_claim_enabled
from app.models.user import UserRole
from app.schemas.installation import InstallationPublicStatus, InstallationState, InstallationWorldRequest, InstallationModels, InstallationProgress, InstallationVerificationRequest, InstallationReadiness
from app.services.installation_service import InstallationService, InstallationConflict
from app.services.setup_service import SetupService


def enabled():
    if not claim_required():
        raise HTTPException(404, detail={"code": "installation_disabled"})

router = APIRouter(prefix="/installation", tags=["installation"], route_class=AuthRoute)
protected = APIRouter(dependencies=[Depends(enabled), Depends(require_roles(UserRole.ADMIN))], route_class=AuthRoute)

@router.get("/status", response_model=InstallationPublicStatus)
async def public_status(users=Depends(get_user_service)):
    active = claim_required()
    return InstallationPublicStatus(enabled=active, owner_required=active and not await users.has_any_users(), claim_mode="local_browser" if local_browser_claim_enabled() else "host_code")

@router.get("/readiness", response_model=InstallationReadiness, dependencies=[Depends(enabled)])
async def readiness():
    return await InstallationService.readiness()

@protected.get("/state", response_model=InstallationState)
async def progress():
    return InstallationService().status()

@protected.put("/progress", response_model=InstallationState)
async def save_progress(payload: InstallationProgress):
    service = InstallationService()
    with service.repository.transaction() as (_, state):
        state.tour_completed = payload.tour_completed
        return state

@protected.post("/world", response_model=InstallationState)
async def world(payload: InstallationWorldRequest, ontologies=Depends(get_ontology_service), media=Depends(get_media_service), instances=Depends(get_ontology_instance_service)):
    try:
        return await InstallationService().create_world(payload.template, SetupService(ontology_service=ontologies, media_service=media), instances, payload.include_example)
    except InstallationConflict as exc:
        raise HTTPException(409, detail={"code": str(exc)}) from exc

@protected.put("/models", response_model=InstallationState)
async def models(payload: InstallationModels, token: str = Depends(oauth2_scheme), db=Depends(get_db_session)):
    try:
        from app.services.agent_service import AgentService
        return await InstallationService().configure_models(payload, token, AgentService(db))
    except InstallationConflict as exc:
        raise HTTPException(409, detail={"code": str(exc)}) from exc
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(422, detail={"code": "model_not_configured"}) from exc

@protected.post("/verify", response_model=InstallationState, status_code=202)
async def verify(payload: InstallationVerificationRequest):
    service = InstallationService()
    try:
        state, should_enqueue = service.begin_verification()
    except InstallationConflict as exc:
        raise HTTPException(409, detail={"code": str(exc)}) from exc
    if should_enqueue:
        from app.tasks.installation import verify_models
        try:
            verify_models.apply_async(args=[state.verification_id], queue="ontology_linking", task_id=state.verification_id)
        except Exception as exc:
            with service.repository.transaction() as (_, current):
                if current.verification_id == state.verification_id:
                    current.verification_status = "failed"
                    current.verification_code = "queue_unavailable"
            raise HTTPException(503, detail={"code": "queue_unavailable"}) from exc
    return state

@protected.post("/complete", response_model=InstallationState)
async def complete():
    try:
        return InstallationService().finish()
    except InstallationConflict as exc:
        raise HTTPException(409, detail={"code": str(exc)}) from exc

router.include_router(protected)
