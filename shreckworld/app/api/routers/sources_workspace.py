"""Ontology-scoped Sources workspace endpoints. Existing PDF APIs remain compatible."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse
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
from app.services.source_documents import DocumentRead, DocumentSave, read_document, save_imported_markdown

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


@router.delete("/source-hierarchies/{template_id}", response_model=HierarchyRead, dependencies=manager)
def delete_template(
    template_id: str, expected_version: int = Query(ge=1), remove_items: bool = Query(default=False),
    scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db),
):
    # Collection entries can be removed from this ontology; source documents remain independent.
    return workspace.delete_hierarchy(session, scope, template_id, expected_version, remove_items)


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


@router.get("/source-hierarchies/{template_id}/versions/{version}/image")
def hierarchy_image(
    template_id: str, version: int,
    scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db),
):
    path = workspace.hierarchy_image_path(session, template_id, version)
    media_type = {
        ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
    }[path.suffix.lower()]
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=300"})


@router.post("/source-hierarchies/{template_id}/image", response_model=HierarchyRead, dependencies=manager)
async def upload_hierarchy_image(
    template_id: str, image: UploadFile = File(...),
    expected_version: int = Query(ge=1),
    scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db),
):
    # Bounded read guards against multi-gigabyte uploads before writing to media.
    raw = await image.read(5 * 1024 * 1024 + 1)
    return workspace.save_hierarchy_image(
        session, template_id, expected_version, raw, image.content_type,
    )


@router.get("/sources/{source_id}/document", response_model=DocumentRead)
def source_document_preview(
    source_id: str, scope: OntologyScope = Depends(authorized_ontology),
    session: Session = Depends(get_db),
):
    return read_document(workspace.scoped_source(session, scope, source_id))


@router.put("/sources/{source_id}/document", response_model=DocumentRead, dependencies=manager)
def save_uploaded_markdown(
    source_id: str, payload: DocumentSave,
    scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db),
):
    return save_imported_markdown(
        session, workspace.scoped_source(session, scope, source_id), payload,
    )


@router.get("/sources/{source_id}/assets/{asset_id}")
def source_section_asset(
    source_id: str, asset_id: str,
    scope: OntologyScope = Depends(authorized_ontology),
    session: Session = Depends(get_db),
):
    """Authorized, source-scoped image and Markdown access."""
    from pathlib import Path
    from app.services.source_assets import read_asset
    workspace.scoped_source(session, scope, source_id)
    asset = read_asset(session, source_id, asset_id)
    extension = Path(asset.storage_path).suffix
    return FileResponse(
        asset.storage_path, media_type=asset.mime_type,
        filename=f"{asset.id}{extension}",
        content_disposition_type="inline" if asset.kind == "image" else "attachment",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
