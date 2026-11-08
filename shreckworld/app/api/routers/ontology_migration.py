"""Manager-only preview/apply for moving existing sources to ontologies."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.api.deps import oauth2_scheme, require_world_manager
from app.db.session import get_db
from app.services.ontology_migration import migration_plan, apply_migration
from app.schemas.ontology_sources import OntologyMigrationRequest, OntologyMigrationReport

router = APIRouter(prefix='/admin/ontology-migration', tags=['Ontology migration'], dependencies=[Depends(require_world_manager)])


@router.post('', response_model=OntologyMigrationReport)
def migrate(payload: OntologyMigrationRequest, token: str = Depends(oauth2_scheme), session: Session = Depends(get_db)):
    try:
        plan = migration_plan(session, token, payload.mappings)
        changed = apply_migration(session, plan) if payload.apply else 0
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return OntologyMigrationReport(collections=plan, changed_items=changed)
