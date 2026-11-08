"""Ontology-owned source transport; uses the same source lifecycle as legacy reads."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, Path
from sqlalchemy.orm import Session

from app.api.deps import require_world_manager, oauth2_scheme, get_current_user, ShrecknetPrincipal
from app.api.routers import shreckworlds as sources
from app.db.session import get_db
from app.integrations.ontologies import authorized_ontology
from app.schemas.shreckworld import (
    AuthorityTier, EmbeddingJobRead, ShreckWorldLibraryItemRead,
    ShreckWorldLibraryItemUpdate, ShreckWorldQueryRequest, ShreckWorldQueryResponse,
    ActionResolutionRequest, ActionResolutionResponse, ShreckWorldAgentJobRead,
)
from app.schemas.document_media import MediaRequest, MediaList
from app.repositories.source_scope import OntologyScope

router = APIRouter(prefix="/ontologies/{ontology_id}", tags=["Ontology sources"])
manager = [Depends(require_world_manager)]


@router.get("/items", response_model=list[ShreckWorldLibraryItemRead])
def list_items(scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db)):
    return sources.list_library_items(scope, session)


@router.post("/items", response_model=ShreckWorldLibraryItemRead, status_code=201, dependencies=manager)
def upload_item(
    title: str | None = None, pdf: UploadFile = File(...), authors: str | None = None,
    description: str | None = None, authority_tier: AuthorityTier = "core_rules", authority_priority: int = 100,
    auto_extract_metadata: bool = Form(False), form_title: str | None = Form(None, alias="title"),
    form_authors: str | None = Form(None, alias="authors"), form_description: str | None = Form(None, alias="description"),
    scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db),
):
    return sources.upload_library_item(scope, title, pdf, authors, description, authority_tier,
        authority_priority, auto_extract_metadata, form_title, form_authors, form_description, session)


@router.get("/items/{item_id}", response_model=ShreckWorldLibraryItemRead)
def get_item(item_id: str, scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db)):
    return sources.get_library_item(scope, item_id, session)


@router.patch("/items/{item_id}", response_model=ShreckWorldLibraryItemRead, dependencies=manager)
def update_item(item_id: str, payload: ShreckWorldLibraryItemUpdate,
                scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db)):
    return sources.update_library_item(scope, item_id, payload, session)


@router.delete("/items/{item_id}", status_code=204, dependencies=manager)
def delete_item(item_id: str, scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db)):
    sources.delete_library_item(scope, item_id, session)


@router.head("/items/{item_id}/content")
@router.get("/items/{item_id}/content")
def content(item_id: str, request: Request, scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db)):
    return sources.download_library_item(scope, item_id, request, session)


@router.get("/items/{item_id}/cover")
def cover(item_id: str, scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db)):
    return sources.download_library_cover(scope, item_id, session)


@router.post("/items/{item_id}/index", response_model=EmbeddingJobRead, status_code=202, dependencies=manager)
def index(item_id: str, scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db)):
    return sources.start_indexing(scope, item_id, session)


@router.get("/items/{item_id}/index-status", response_model=EmbeddingJobRead, dependencies=manager)
def index_status(item_id: str, scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db)):
    return sources.index_status(scope, item_id, session)


@router.get("/items/{item_id}/index-jobs/{job_id}", response_model=EmbeddingJobRead, dependencies=manager)
def index_job(item_id: str, job_id: str, scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db)):
    return sources.get_indexing_job(scope, item_id, job_id, session)


@router.post("/query", response_model=ShreckWorldQueryResponse)
def query(payload: ShreckWorldQueryRequest, scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db)):
    return sources.query(scope, payload, session)


@router.post("/resolve-action", response_model=ActionResolutionResponse)
def resolve_action(payload: ActionResolutionRequest, scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db)):
    return sources.resolve_world_action(scope, payload, session)


@router.post("/companion-evidence", response_model=ShreckWorldQueryResponse)
async def companion_evidence(payload: ShreckWorldQueryRequest, ontology_id: int = Path(gt=0),
                             token: str = Depends(oauth2_scheme), session: Session = Depends(get_db)):
    # This endpoint accepts only an owner-validated, retrieval-only turn grant.
    # The grant already pins the ontology; it cannot call upload/read routes.
    return await sources.companion_evidence(OntologyScope(ontology_id, f"Ontology {ontology_id}"), payload, token, session)


@router.post("/companion-validate")
async def companion_validate(payload: dict, ontology_id: int = Path(gt=0),
                             token: str = Depends(oauth2_scheme), session: Session = Depends(get_db)):
    return await sources.validate_companion_sources(OntologyScope(ontology_id, f"Ontology {ontology_id}"), payload, token, session)


@router.post("/agent-jobs", response_model=ShreckWorldAgentJobRead, status_code=202)
def create_agent_job(payload: ShreckWorldQueryRequest, scope: OntologyScope = Depends(authorized_ontology),
                     principal: ShrecknetPrincipal = Depends(get_current_user), session: Session = Depends(get_db)):
    return sources.create_agent_job(scope, payload, principal, session)


@router.get("/agent-jobs/{job_id}", response_model=ShreckWorldAgentJobRead)
def agent_job(job_id: str, scope: OntologyScope = Depends(authorized_ontology),
              principal: ShrecknetPrincipal = Depends(get_current_user), session: Session = Depends(get_db)):
    return sources.get_agent_job(scope, job_id, principal, session)


@router.get('/items/{item_id}/documents/{document_id}/media/{media_id}')
def document_media(item_id: str, document_id: str, media_id: str, revision: str,
                   scope: OntologyScope = Depends(authorized_ontology), session: Session = Depends(get_db)):
    return sources.document_media(scope, item_id, document_id, media_id, revision, session)


@router.post('/companion-media', response_model=MediaList)
async def companion_media(payload: MediaRequest, ontology_id: int = Path(gt=0), token: str = Depends(oauth2_scheme), session: Session = Depends(get_db)):
    return await sources.companion_media(OntologyScope(ontology_id, f'Ontology {ontology_id}'), payload, token, session)
