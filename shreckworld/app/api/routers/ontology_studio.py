"""World-scoped Ontology Studio API. Write routes require World Builder privileges."""
from __future__ import annotations
from uuid import uuid4
from datetime import datetime, timezone
from collections import Counter, defaultdict
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import require_world_manager, oauth2_scheme, ShrecknetPrincipal
from app.db.session import get_db
from app.integrations.ontologies import resolve_ontology
from app.models.document import (
    ShreckWorldDocument,
    ShreckWorldDocumentBlock,
    ShreckWorldDocumentBookMap,
)
from app.models.shreckworld import ShreckWorldLibraryItem
from app.models.ontology_studio import OntologyLearnRun, OntologyLearnSource, OntologyLearnChapter, OntologyLearnWindow, OntologyRevision
from app.services import ontology_studio as service
from app.services.ontology_compact import CONTRACT_VERSION, proposal_counts
from app.services.ontology_discovery_batches import build_discovery_batches
from app.services.book_map_contract import read_book_map
from app.services.ontology_run_reporting import cancel_run, run_response
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
    chapter_ids: dict[str, list[str]] | None = None

class ProposalEdit(Strict):
    expected_proposal_revision: int = Field(ge=0)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    entity: str | None = None
    source: str | None = None
    target: str | None = None
    data_type: str | None = None

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
    # V2 "chapter sent" history must not imply a chapter was processed using
    # the schema-only V3 contract. A chapter is sent only if all of its current
    # V3 windows completed in the same run for the current source digest.
    completed_by_run = defaultdict(set)
    for row in db.scalars(select(OntologyLearnWindow).where(
        OntologyLearnWindow.ontology_id == ontology_id,
        OntologyLearnWindow.contract_version == CONTRACT_VERSION,
        OntologyLearnWindow.status == "completed",
    )):
        key = (row.source_id, row.content_digest, row.chapter_id, row.run_id)
        completed_by_run[key].add(row.window_index)
    fully_processed = defaultdict(list)
    for (source_id, digest, chapter_id, _), indices in completed_by_run.items():
        fully_processed[(source_id, digest, chapter_id)].append(indices)
    result = []
    for item in db.scalars(select(ShreckWorldLibraryItem).where(
        ShreckWorldLibraryItem.ontology_id == ontology_id, ShreckWorldLibraryItem.source_type == "explanatory")):
        doc = current.get(item.id)
        learned = history.get(item.id)
        source_status = ("not_indexed" if doc is None else "ready" if learned is None else
             "learned" if learned.content_digest == doc[1].source_sha256 else "outdated")
        chapters = []
        if doc:
            mapped = read_book_map(db.get(ShreckWorldDocumentBookMap, doc[1].id).payload)
            block_rows = list(db.scalars(select(ShreckWorldDocumentBlock).where(
                ShreckWorldDocumentBlock.document_id == doc[1].id)))
            chapter_titles = {}
            chapter_window_counts = Counter()
            for batch in build_discovery_batches(mapped, {b.id: b for b in block_rows}):
                chapter_titles[batch["chapter_id"]] = batch["chapter_title"]
                chapter_window_counts[batch["chapter_id"]] += 1
            chapters = [{"id": chapter_id, "title": title,
                "sent": any(all(index in indices for index in range(1, chapter_window_counts[chapter_id] + 1))
                            for indices in fully_processed.get(
                                (item.id, doc[1].source_sha256, chapter_id), []))}
                for chapter_id, title in chapter_titles.items()]
        result.append({"id": item.id, "title": item.title, "index_status": item.index_status,
            "learning_status": source_status, "last_learned_at": learned.reviewed_at if learned else None,
            "last_learned_run_id": learned.run_id if learned else None,
            "document_id": doc[1].id if doc else None, "chapters": chapters})
    return result

@router.post("/learn", status_code=202)
def learn(ontology_id: int, request: LearnRequest, token: str = Depends(oauth2_scheme),
          user: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    if len(set(request.source_ids)) != len(request.source_ids):
        raise HTTPException(422, "Duplicate sources")
    if request.chapter_ids is not None and not set(request.chapter_ids) <= set(request.source_ids):
        raise HTTPException(422, "Chapter selections must belong to selected sources")
    lookup = {item.id: (item, doc) for item,doc in eligible_sources(db, ontology_id)}
    if not all(source_id in lookup for source_id in request.source_ids):
        raise HTTPException(422, "Selected Explanatory Sources must have an active Book Map")
    pins = []
    for sid in request.source_ids:
        item, doc = lookup[sid]
        mapped = read_book_map(db.scalar(select(ShreckWorldDocumentBookMap).where(
            ShreckWorldDocumentBookMap.document_id == doc.id)).payload)
        batches = build_discovery_batches(mapped, {b.id: b for b in db.scalars(
            select(ShreckWorldDocumentBlock).where(ShreckWorldDocumentBlock.document_id == doc.id))})
        available = {batch["chapter_id"] for batch in batches}
        requested = (request.chapter_ids or {}).get(sid)
        chosen = sorted(available) if requested is None else requested
        if not chosen or len(set(chosen)) != len(chosen) or not set(chosen) <= available:
            raise HTTPException(422, "Selected chapters must belong to the active Book Map")
        pins.append({"source_id": sid, "title": item.title, "document_id": doc.id,
                     "content_digest": doc.source_sha256, "chapter_ids": chosen})
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
    payload = run_response(run)
    if run.status == "succeeded" and not run.proposals:
        windows = db.scalars(select(OntologyLearnWindow).where(
            OntologyLearnWindow.run_id == run.id,
            OntologyLearnWindow.status == "completed",
        ))
        payload["proposals_recovery_available"] = any(bool(window.proposals) for window in windows)
    else:
        payload["proposals_recovery_available"] = False
    return payload


@router.post("/runs/{run_id}/recover-proposals")
def recover_proposals(ontology_id: int, run_id: str, token: str = Depends(oauth2_scheme),
                   user: ShrecknetPrincipal = Depends(require_world_manager),
                   db: Session = Depends(get_db)):
    auth(ontology_id, token)
    run = db.get(OntologyLearnRun, run_id)
    if run is None or run.ontology_id != ontology_id:
        raise HTTPException(404, "Run not found")
    return run_response(service.recover_empty_review(db, run))


@router.post("/runs/{run_id}/discard-proposals")
def discard_proposals(ontology_id: int, run_id: str, token: str = Depends(oauth2_scheme),
                   user: ShrecknetPrincipal = Depends(require_world_manager),
                   db: Session = Depends(get_db)):
    auth(ontology_id, token)
    run = db.get(OntologyLearnRun, run_id)
    if run is None or run.ontology_id != ontology_id:
        raise HTTPException(404, "Run not found")
    return run_response(service.restore_review(db, run, user.user_id))

@router.get("/runs/{run_id}/windows")
def list_run_windows(ontology_id: int, run_id: str,
                     token: str = Depends(oauth2_scheme),
                     _: ShrecknetPrincipal = Depends(require_world_manager),
                     db: Session = Depends(get_db)):
    auth(ontology_id, token)
    run = db.get(OntologyLearnRun, run_id)
    if run is None or run.ontology_id != ontology_id:
        raise HTTPException(404, "Run not found")
    rows = db.scalars(select(OntologyLearnWindow).where(
        OntologyLearnWindow.run_id == run_id,
        OntologyLearnWindow.ontology_id == ontology_id,
    ).order_by(OntologyLearnWindow.source_id,
               OntologyLearnWindow.chapter_id, OntologyLearnWindow.window_index))
    return [{"id": row.id, "source_id": row.source_id,
             "chapter_id": row.chapter_id, "chapter_title": row.chapter_title,
             "window_index": row.window_index,
             "contract_version": row.contract_version, "status": row.status,
             "cached": row.cached, "proposals_count": len(row.proposals or []),
             "schema_counts": proposal_counts(row.proposals or []),
             "input_tokens": row.input_tokens, "output_tokens": row.output_tokens,
             "error_code": row.error_code, "finished_at": row.finished_at}
            for row in rows]

@router.post("/runs/{run_id}/cancel")
def cancel(ontology_id: int, run_id: str, token: str = Depends(oauth2_scheme),
           _: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    run = db.get(OntologyLearnRun, run_id)
    if run is None or run.ontology_id != ontology_id:
        raise HTTPException(404, "Run not found")
    if not cancel_run(db, run):
        raise HTTPException(409, "Run cannot be cancelled")
    return run_response(run)

@router.patch("/runs/{run_id}/proposals/{proposal_id}")
def edit_proposal(ontology_id: int, run_id: str, proposal_id: str, payload: ProposalEdit,
                  token: str = Depends(oauth2_scheme),
                  _: ShrecknetPrincipal = Depends(require_world_manager), db: Session = Depends(get_db)):
    auth(ontology_id, token)
    run = db.get(OntologyLearnRun, run_id)
    if run is None or run.ontology_id != ontology_id:
        raise HTTPException(404, "Run not found")
    if run.status != "awaiting_review":
        raise HTTPException(409, "Run is not awaiting review")
    if run.proposal_revision != payload.expected_proposal_revision:
        raise HTTPException(409, "Proposal revision conflict")
    proposals = [dict(p) for p in run.proposals]
    target = next((p for p in proposals if p["id"] == proposal_id), None)
    if target is None:
        raise HTTPException(404, "Proposal not found")
    changes = payload.model_dump(exclude={"expected_proposal_revision"}, exclude_unset=True)
    allowed = {"name", "description"}
    if target["kind"] == "property":
        allowed |= {"entity", "data_type"}
    if target["kind"] == "relationship":
        allowed |= {"source", "target"}
    if set(changes) - allowed:
        raise HTTPException(422, "Fields cannot be edited for this proposal type")
    if any(not isinstance(v, str) or not v.strip() for k,v in changes.items() if k != "description"):
        raise HTTPException(422, "Proposal references must not be blank")
    if "data_type" in changes and changes["data_type"] not in service.TYPES:
        raise HTTPException(422, "Invalid property type")
    target.update(changes)
    target.pop("conflict", None)
    target["edited_by_human"] = True
    run.proposals = proposals
    run.proposal_revision += 1
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
