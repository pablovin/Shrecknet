"""World-local Narrative and Explanatory hierarchy collections API."""
from __future__ import annotations

from typing import Literal
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.routers.sources_workspace import require_source_admin
from app.db.session import get_db
from app.integrations.ontologies import authorized_ontology
from app.repositories.source_scope import OntologyScope
from app.schemas.source_collections import CollectionCreate, CollectionRead, CollectionUpdate
from app.services import source_collections as collections

router = APIRouter(prefix="/ontologies/{ontology_id}/source-collections", tags=["Source collections"])
manager = [Depends(require_source_admin)]


@router.get("", response_model=list[CollectionRead])
def list_category(
    category: Literal["narrative", "explanatory"] = Query(...),
    scope: OntologyScope = Depends(authorized_ontology), db: Session = Depends(get_db),
):
    # Explicit category is mandatory; no cross-category "all sources" endpoint.
    return collections.list_collections(db, scope, category)


@router.post("", response_model=CollectionRead, status_code=201, dependencies=manager)
def assign_hierarchy(
    payload: CollectionCreate,
    scope: OntologyScope = Depends(authorized_ontology), db: Session = Depends(get_db),
):
    return collections.create_collection(db, scope, payload)


@router.get("/{collection_id}", response_model=CollectionRead)
def get_collection(
    collection_id: str,
    scope: OntologyScope = Depends(authorized_ontology), db: Session = Depends(get_db),
):
    return collections.read_collection(db, collections.scoped_collection(db, scope, collection_id))


@router.put("/{collection_id}", response_model=CollectionRead, dependencies=manager)
def save_collection(
    collection_id: str, payload: CollectionUpdate,
    scope: OntologyScope = Depends(authorized_ontology), db: Session = Depends(get_db),
):
    return collections.save_collection(db, scope, collection_id, payload)


@router.delete("/{collection_id}", status_code=204, dependencies=manager)
def archive_collection(
    collection_id: str, expected_revision: int = Query(ge=1),
    scope: OntologyScope = Depends(authorized_ontology), db: Session = Depends(get_db),
):
    # Archive the assignment and preserve all entries and attached documents.
    collections.remove_collection(db, scope, collection_id, expected_revision)


@router.post("/{collection_id}/entries/{entry_id}/image", response_model=CollectionRead, dependencies=manager)
async def upload_entry_artwork(
    collection_id: str, entry_id: str, image: UploadFile = File(...),
    expected_revision: int = Query(ge=1),
    scope: OntologyScope = Depends(authorized_ontology), db: Session = Depends(get_db),
):
    raw = await image.read(5 * 1024 * 1024 + 1)
    return collections.upload_entry_image(
        db, scope, collection_id, entry_id, expected_revision, raw, image.content_type,
    )


@router.get("/{collection_id}/entries/{entry_id}/image")
def read_entry_artwork(
    collection_id: str, entry_id: str,
    scope: OntologyScope = Depends(authorized_ontology), db: Session = Depends(get_db),
):
    path = collections.entry_image(db, scope, collection_id, entry_id)
    return FileResponse(
        path, media_type={".png":"image/png", ".jpg":"image/jpeg", ".webp":"image/webp"}[path.suffix.lower()],
        headers={"Cache-Control":"private, no-store", "X-Content-Type-Options":"nosniff"},
    )
