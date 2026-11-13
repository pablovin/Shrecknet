"""Ontology-scoped Sources workspace endpoints. Existing PDF APIs remain compatible."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, ShrecknetPrincipal
from app.db.session import get_db
from app.integrations.ontologies import authorized_ontology
from app.repositories.source_scope import OntologyScope
from app.schemas.shreckworld import ShreckWorldLibraryItemRead
from app.schemas.sources import (
    HierarchyInput, HierarchyRead, OrganizationInput, OrganizationRead,
    SourceCreate, SourceSettings,
)
from app.services import source_workspace as workspace

router = APIRouter(prefix="/ontologies/{ontology_id}", tags=["Sources workspace"])
def require_source_admin(principal: ShrecknetPrincipal = Depends(get_current_user)) -> None:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="Source administration requires administrator access")


manager = [Depends(require_source_admin)]


@router.get("/source-hierarchies", response_model=list[HierarchyRead])
def list_templates(
    scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db),
):
    return workspace.list_hierarchies(session)


@router.get("/source-hierarchies/{template_id}/versions/{version}", response_model=HierarchyRead)
def template_version(
    template_id: str, version: int, scope: OntologyScope = Depends(authorized_ontology),
    session: Session = Depends(get_db),
):
    return workspace.get_hierarchy(session, template_id, version)


@router.post("/source-hierarchies", response_model=HierarchyRead, status_code=201, dependencies=manager)
def create_template(
    payload: HierarchyInput, scope: OntologyScope = Depends(authorized_ontology),
    session: Session = Depends(get_db),
):
    return workspace.save_hierarchy(session, payload)


@router.put("/source-hierarchies/{template_id}", response_model=HierarchyRead, dependencies=manager)
def revise_template(
    template_id: str, payload: HierarchyInput,
    scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db),
):
    return workspace.save_hierarchy(session, payload, template_id)


@router.post("/sources", response_model=ShreckWorldLibraryItemRead, status_code=201, dependencies=manager)
def create_authored(
    payload: SourceCreate, scope: OntologyScope = Depends(authorized_ontology),
    session: Session = Depends(get_db),
):
    return workspace.create_authored_source(session, scope, payload)


@router.put("/sources/{source_id}/settings", response_model=ShreckWorldLibraryItemRead, dependencies=manager)
def update_settings(
    source_id: str, payload: SourceSettings,
    scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db),
):
    return workspace.update_source_settings(session, scope, source_id, payload)


@router.get("/sources/{source_id}/organization", response_model=OrganizationRead)
def read_organization(
    source_id: str, scope: OntologyScope = Depends(authorized_ontology),
    session: Session = Depends(get_db),
):
    item = workspace.scoped_source(session, scope, source_id)
    return workspace.get_organization(session, item)


@router.put("/sources/{source_id}/organization", response_model=OrganizationRead, dependencies=manager)
def update_organization(
    source_id: str, payload: OrganizationInput, scope: OntologyScope = Depends(authorized_ontology),
    session: Session = Depends(get_db),
):
    item = workspace.scoped_source(session, scope, source_id)
    return workspace.save_organization(session, item, payload)
