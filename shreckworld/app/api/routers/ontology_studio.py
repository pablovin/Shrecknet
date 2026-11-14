"""World-scoped Ontology Studio API. Write routes require World Builder privileges."""
from __future__ import annotations
from uuid import uuid4
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import require_world_manager, oauth2_scheme, ShrecknetPrincipal
from app.db.session import get_db
from app.integrations.ontologies import resolve_ontology
from app.models.document import ShreckWorldDocument, ShreckWorldDocumentBookMap
from app.models.shreckworld import ShreckWorldLibraryItem
from app.models.ontology_studio import OntologyLearnRun, OntologyLearnSource, OntologyRevision
from app.services import ontology_studio as service
from app.core.config import get_settings

router = APIRouter(prefix="/api/v1/ontology-studio/worlds/{ontology_id}", tags=["Ontology Studio"])

class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Versioned(Strict):
    expected_revision: int = Field(ge=0)

class EntityCreate(Versioned):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    parent_id: str | None = None

class EntityPatch(Versioned):
    name: str | None = None
    description: str | None = None
    aliases: list[str] | None = None
    parent_id: str | None = None

class PropertyCreate(Versioned):
    name: str = Field(min_length=1, max_length=200)
    data_type: str
    description: str = ""
    required: bool = False
    cardinality: str = "one"
    constraints: dict = Field(default_factory=dict)

class PropertyPatch(Versioned):
    name: str | None = None
    data_type: str | None = None
    description: str | None = None
    required: bool | None = None
    cardinality: str | None = None
    constraints: dict | None = None

class RelationshipCreate(Versioned):
    name: str = Field(min_length=1, max_length=200)
    source_id: str
    target_id: str
    description: str = ""
    cardinality: str = "many"
    inverse_name: str | None = None

class RelationshipPatch(Versioned):
    name: str | None = None
    source_id: str | None = None
    target_id: str | None = None
    description: str | None = None
    cardinality: str | None = None
    inverse_name: str | None = None

class LearnRequest(Strict):
    idempotency_key: str = Field(min_length=1, max_length=128)
    source_ids: list[str] = Field(min_length=1, max_length=20)

class ReviewRequest(Versioned):
    decisions: dict[str, str]

def auth(ontology_id: int, token: str) -> None:
    resolve_ontology(ontology_id, token)

def _data(payload: BaseModel, exclude: set[str] = {"expected_revision"}) -> dict:
    return payload.model_dump(exclude=exclude, exclude_unset=True, exclude_none=False)

def _patch(payload: BaseModel) -> dict:
    return {k: v for k,v in _data(payload).items() if v is not None or k in {"parent_id", "inverse_name"}}

@router.get("/ontology")
def get_graph(ontology_id: int, token: str = Depends(oauth2_scheme),
              _: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    return service.graph(db, ontology_id)

@router.get("/revisions")
def revisions(ontology_id: int, token: str = Depends(oauth2_scheme),
              _: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    root = service.root(db, ontology_id)
    if root is None:
        return []
    return [{"revision": r.revision, "author_id": r.author_id,
             "created_at": r.created_at, "snapshot": r.snapshot} for r in db.scalars(
        select(OntologyRevision).where(OntologyRevision.root_id == root.id)
        .order_by(OntologyRevision.revision.desc()).limit(100))]

@router.post("/entities")
def create_entity(ontology_id: int, data: EntityCreate, token: str = Depends(oauth2_scheme),
                  user: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    return service.create_entity(db, ontology_id, _data(data), data.expected_revision, user.user_id)

@router.patch("/entities/{entity_id}")
def update_entity(ontology_id: int, entity_id: str, data: EntityPatch, token: str = Depends(oauth2_scheme),
                  user: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    return service.update_entity(db, ontology_id, entity_id, _patch(data), data.expected_revision, user.user_id)

@router.delete("/entities/{entity_id}")
def delete_entity(ontology_id: int, entity_id: str, expected_revision: int = Query(ge=0),
                  token: str = Depends(oauth2_scheme),
                  user: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    return service.delete_entity(db, ontology_id, entity_id, expected_revision, user.user_id)

@router.post("/entities/{entity_id}/properties")
def create_property(ontology_id: int, entity_id: str, data: PropertyCreate,
                    token: str = Depends(oauth2_scheme),
                    user: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    return service.create_property(db, ontology_id, entity_id, _data(data), data.expected_revision, user.user_id)

@router.patch("/properties/{property_id}")
def update_property(ontology_id: int, property_id: str, data: PropertyPatch,
                    token: str = Depends(oauth2_scheme),
                    user: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    return service.update_property(db, ontology_id, property_id, _patch(data), data.expected_revision, user.user_id)

@router.delete("/properties/{property_id}")
def delete_property(ontology_id: int, property_id: str, expected_revision: int = Query(ge=0),
                    token: str = Depends(oauth2_scheme),
                    user: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    return service.delete_property(db, ontology_id, property_id, expected_revision, user.user_id)

@router.post("/relationships")
def create_relationship(ontology_id: int, data: RelationshipCreate,
                        token: str = Depends(oauth2_scheme),
                        user: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    return service.create_relationship(db, ontology_id, _data(data), data.expected_revision, user.user_id)

@router.patch("/relationships/{relationship_id}")
def update_relationship(ontology_id: int, relationship_id: str, data: RelationshipPatch,
                        token: str = Depends(oauth2_scheme),
                        user: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    return service.update_relationship(db, ontology_id, relationship_id, _patch(data), data.expected_revision, user.user_id)

@router.delete("/relationships/{relationship_id}")
def delete_relationship(ontology_id: int, relationship_id: str, expected_revision: int = Query(ge=0),
                        token: str = Depends(oauth2_scheme),
                        user: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    return service.delete_relationship(db, ontology_id, relationship_id, expected_revision, user.user_id)

def eligible_sources(db: Session, ontology_id: int) -> list:
    return list(db.execute(select(ShreckWorldLibraryItem, ShreckWorldDocument)
        .join(ShreckWorldDocument, ShreckWorldDocument.library_item_id == ShreckWorldLibraryItem.id)
        .join(ShreckWorldDocumentBookMap, ShreckWorldDocumentBookMap.document_id == ShreckWorldDocument.id)
        .where(ShreckWorldLibraryItem.ontology_id == ontology_id,
               ShreckWorldLibraryItem.source_type == "explanatory",
               ShreckWorldDocument.is_active.is_(True))))

@router.get("/sources")
def sources(ontology_id: int, token: str = Depends(oauth2_scheme),
            _: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    history = {r.source_id: r for r in db.scalars(select(OntologyLearnSource).where(OntologyLearnSource.ontology_id == ontology_id))}
    current = {item.id: (item, doc) for item,doc in eligible_sources(db, ontology_id)}
    result = []
    for item in db.scalars(select(ShreckWorldLibraryItem).where(
        ShreckWorldLibraryItem.ontology_id == ontology_id, ShreckWorldLibraryItem.source_type == "explanatory")):
        doc = current.get(item.id)
        learned = history.get(item.id)
        source_status = ("not_indexed" if doc is None else "ready" if learned is None else
             "learned" if learned.content_digest == doc[1].source_sha256 else "outdated")
        result.append({"id": item.id, "title": item.title, "index_status": item.index_status,
            "learning_status": source_status, "last_learned_at": learned.reviewed_at if learned else None,
            "last_learned_run_id": learned.run_id if learned else None,
            "document_id": doc[1].id if doc else None})
    return result

@router.post("/learn", status_code=202)
def learn(ontology_id: int, request: LearnRequest, token: str = Depends(oauth2_scheme),
          user: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    if len(set(request.source_ids)) != len(request.source_ids):
        raise HTTPException(422, "Duplicate sources")
    lookup = {item.id: (item, doc) for item,doc in eligible_sources(db, ontology_id)}
    if not all(source_id in lookup for source_id in request.source_ids):
        raise HTTPException(422, "Selected Explanatory Sources must have an active Book Map")
    pins = [{"source_id": sid, "title": lookup[sid][0].title,
             "document_id": lookup[sid][1].id, "content_digest": lookup[sid][1].source_sha256}
            for sid in request.source_ids]
    found = db.scalar(select(OntologyLearnRun).where(OntologyLearnRun.owner_id == user.user_id,
                    OntologyLearnRun.idempotency_key == request.idempotency_key))
    if found:
        if found.ontology_id != ontology_id or found.input_sources != pins:
            raise HTTPException(409, "Idempotency key already used for different inputs")
        return run_response(found)
    settings = get_settings()
    model_targets = {k: getattr(settings, "model_architect_ontology_" + k).model_dump()
                     for k in ("discovery", "schema", "reconciliation")}
    owner = service.root(db, ontology_id)
    run = OntologyLearnRun(ontology_id=ontology_id, owner_id=user.user_id,
         idempotency_key=request.idempotency_key, input_sources=pins,
         base_revision=owner.revision if owner else 0, model_targets=model_targets)
    db.add(run)
    db.commit()
    from app.tasks.architect_learn_ontology import run_ontology_discovery
    try:
        run_ontology_discovery.delay(run.id)
    except Exception as exc:
        run.status = "failed"
        run.diagnostics = [{"code": "dispatch_failed"}]
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(503, "Ontology discovery could not be queued") from exc
    return run_response(run)

def run_response(run):
    return {"id": run.id, "ontology_id": run.ontology_id, "status": run.status,
            "stage": run.stage, "progress": run.progress, "input_sources": run.input_sources,
            "base_revision": run.base_revision, "proposals": run.proposals,
            "diagnostics": run.diagnostics, "created_at": run.created_at,
            "finished_at": run.finished_at}

@router.get("/runs")
def list_runs(ontology_id: int, token: str = Depends(oauth2_scheme),
              _: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    return [run_response(r) for r in db.scalars(select(OntologyLearnRun)
        .where(OntologyLearnRun.ontology_id == ontology_id)
        .order_by(OntologyLearnRun.created_at.desc()).limit(100))]

@router.get("/runs/{run_id}")
def get_run(ontology_id: int, run_id: str, token: str = Depends(oauth2_scheme),
            _: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    run = db.get(OntologyLearnRun, run_id)
    if run is None or run.ontology_id != ontology_id:
        raise HTTPException(404, "Run not found")
    return run_response(run)

@router.post("/runs/{run_id}/cancel")
def cancel(ontology_id: int, run_id: str, token: str = Depends(oauth2_scheme),
           _: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    run = db.get(OntologyLearnRun, run_id)
    if run is None or run.ontology_id != ontology_id:
        raise HTTPException(404, "Run not found")
    if run.status not in {"queued", "running"}:
        raise HTTPException(409, "Run cannot be cancelled")
    run.status = "cancel_requested"
    db.commit()
    return run_response(run)

@router.post("/runs/{run_id}/review")
def review(ontology_id: int, run_id: str, request: ReviewRequest,
           token: str = Depends(oauth2_scheme),
           user: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    run = db.get(OntologyLearnRun, run_id)
    if run is None or run.ontology_id != ontology_id:
        raise HTTPException(404, "Run not found")
    return service.apply_proposals(db, run, request.decisions, request.expected_revision, user.user_id)
