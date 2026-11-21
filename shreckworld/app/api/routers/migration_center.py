"""Read-only migration intelligence and explicit source-ownership application."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import ShrecknetPrincipal, oauth2_scheme, require_world_manager
from app.db.session import get_db
from app.services import migration_center as service

router = APIRouter(prefix="/admin/migration-center", tags=["Migration Center"])


class SourcePlan(BaseModel):
    collection_id: str = Field(min_length=1, max_length=36)
    target_ontology_id: int = Field(gt=0)


class SourceApply(SourcePlan):
    preview_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation: str
    backup_confirmed: bool = False


@router.get("/overview")
def migration_overview(
    token: str = Depends(oauth2_scheme),
    _: ShrecknetPrincipal = Depends(require_world_manager),
    db: Session = Depends(get_db),
):
    return service.overview(db, token)


@router.post("/source-preview")
def source_preview(
    payload: SourcePlan,
    token: str = Depends(oauth2_scheme),
    _: ShrecknetPrincipal = Depends(require_world_manager),
    db: Session = Depends(get_db),
):
    return service.preview(db, token, payload.collection_id, payload.target_ontology_id)


@router.post("/source-apply")
def source_apply(
    payload: SourceApply,
    token: str = Depends(oauth2_scheme),
    actor: ShrecknetPrincipal = Depends(require_world_manager),
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    if payload.confirmation != "MIGRATE SOURCES" or not payload.backup_confirmed:
        raise HTTPException(422, "Confirm MIGRATE SOURCES and acknowledge a consistent external backup")
    return service.apply_source_ownership(
        db, token, collection_id=payload.collection_id,
        target=payload.target_ontology_id,
        fingerprint=payload.preview_fingerprint, actor_id=actor.user_id,
    )
