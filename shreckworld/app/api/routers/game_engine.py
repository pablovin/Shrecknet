from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import ShrecknetPrincipal, oauth2_scheme, require_world_manager
from app.db.session import get_db
from app.integrations.ontologies import resolve_ontology, shrecknet_read
from app.models.game_engine import GameBinding, GameGenerationRun, GameRuleset
from app.models.document import ShreckWorldDocument, ShreckWorldDocumentBookMap
from app.models.shreckworld import ShreckWorldLibraryItem
from app.schemas.game_engine import (
    BindingCreate, BindingRead, DefinitionCreate, DefinitionRead, ParticipantCreate, ParticipantRead,
    PublishRequest, RejectRequest, ReviewRead, RulesetCreate, RulesetRead, RunCreate, RunPage, RunRead, TableCreate, TablePage,
    TableRead, TableUpdate, VersionRead, EntityDiscoveryCreate, EntityDiscoveryReview,
)
from app.services import game_engine as service

router = APIRouter(prefix="/api/v1/game-engine", tags=["ShreckWorld game engine"])


def _manager(principal: ShrecknetPrincipal = Depends(require_world_manager)) -> ShrecknetPrincipal:
    return principal


def _reviewer(principal: ShrecknetPrincipal = Depends(_manager)) -> ShrecknetPrincipal:
    if principal.role != "admin":
        raise HTTPException(403, "Administrator review privileges required")
    return principal


def _authorize_ontology(ontology_id: int, token: str) -> None:
    resolve_ontology(ontology_id, token)


@router.post("/rulesets", response_model=RulesetRead, status_code=status.HTTP_201_CREATED)
def create_ruleset(payload: RulesetCreate, principal: ShrecknetPrincipal = Depends(_manager),
                   session: Session = Depends(get_db)):
    return service.create_ruleset(session, payload, principal.user_id)


@router.get("/rulesets", response_model=list[RulesetRead])
def list_rulesets(_: ShrecknetPrincipal = Depends(_manager), session: Session = Depends(get_db)):
    return service.list_rulesets(session)


@router.post("/bindings", response_model=BindingRead, status_code=status.HTTP_201_CREATED)
def create_binding(payload: BindingCreate, token: str = Depends(oauth2_scheme),
                   principal: ShrecknetPrincipal = Depends(_manager), session: Session = Depends(get_db)):
    _authorize_ontology(payload.ontology_id, token)
    return service.create_binding(session, payload, principal.user_id)


@router.get("/bindings", response_model=list[BindingRead])
def list_bindings(ontology_id: int = Query(gt=0), token: str = Depends(oauth2_scheme),
                  _: ShrecknetPrincipal = Depends(_manager), session: Session = Depends(get_db)):
    _authorize_ontology(ontology_id, token)
    return service.list_bindings(session, ontology_id)


@router.post("/rulesets/{ruleset_id}/definitions", response_model=DefinitionRead, status_code=status.HTTP_201_CREATED)
def add_definition(ruleset_id: str, payload: DefinitionCreate, ontology_id: int = Query(gt=0),
                   token: str = Depends(oauth2_scheme), _: ShrecknetPrincipal = Depends(_manager),
                   session: Session = Depends(get_db)):
    _authorize_ontology(ontology_id, token)
    binding = session.scalar(select(GameBinding).where(GameBinding.ruleset_id == ruleset_id,
                                                       GameBinding.ontology_id == ontology_id))
    if not binding:
        raise HTTPException(404, "Ontology binding not found")
    references = [reference.model_dump(mode="json") for reference in payload.provenance]
    if references:
        service.validate_source_references(session, ontology_id, references)
    return service.add_definition(session, ruleset_id, payload)


@router.post("/rulesets/{ruleset_id}/publish", response_model=VersionRead, status_code=status.HTTP_201_CREATED)
def publish_ruleset(ruleset_id: str, payload: PublishRequest,
                    principal: ShrecknetPrincipal = Depends(_reviewer), session: Session = Depends(get_db)):
    return service.publish_ruleset(session, ruleset_id, payload.expected_revision,
                                   principal.user_id, payload.supersedes_version_id)


@router.post("/rulesets/{ruleset_id}/approve", response_model=RulesetRead)
def approve_ruleset(ruleset_id: str, expected_revision: int = Query(ge=1),
                    principal: ShrecknetPrincipal = Depends(_reviewer), session: Session = Depends(get_db)):
    return service.approve_ruleset(session, ruleset_id, expected_revision, principal.user_id)


@router.post("/rulesets/{ruleset_id}/reject", response_model=RulesetRead)
def reject_ruleset(ruleset_id: str, payload: RejectRequest, expected_revision: int = Query(ge=1),
                   principal: ShrecknetPrincipal = Depends(_reviewer), session: Session = Depends(get_db)):
    return service.reject_ruleset(session, ruleset_id, expected_revision, principal.user_id, payload.reason)


@router.get("/rulesets/{ruleset_id}/reviews", response_model=list[ReviewRead])
def list_reviews(ruleset_id: str, _: ShrecknetPrincipal = Depends(_manager), session: Session = Depends(get_db)):
    return service.list_reviews(session, ruleset_id)


@router.get("/rulesets/{ruleset_id}/versions", response_model=list[VersionRead])
def list_versions(ruleset_id: str, _: ShrecknetPrincipal = Depends(_manager), session: Session = Depends(get_db)):
    return service.list_versions(session, ruleset_id)


@router.post("/rulesets/{ruleset_id}/runs", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
def create_run(ruleset_id: str, payload: RunCreate, token: str = Depends(oauth2_scheme),
               principal: ShrecknetPrincipal = Depends(_manager), session: Session = Depends(get_db)):
    ruleset = session.get(GameRuleset, ruleset_id)
    if not ruleset:
        raise HTTPException(404, "Ruleset not found")
    binding = None
    if payload.binding_id:
        binding = session.get(GameBinding, payload.binding_id)
        if not binding or binding.ruleset_id != ruleset_id:
            raise HTTPException(404, "Binding not found")
        _authorize_ontology(binding.ontology_id, token)
    source_refs = [reference.model_dump(mode="json") for reference in payload.source_refs]
    if source_refs and binding is None:
        raise HTTPException(422, "Source references require an ontology binding")
    existing = session.scalar(select(GameGenerationRun).where(
        GameGenerationRun.owner_id == principal.user_id,
        GameGenerationRun.idempotency_key == payload.idempotency_key))
    if existing:
        if existing.ruleset_id != ruleset_id or existing.binding_id != payload.binding_id or existing.input_sources != source_refs:
            raise HTTPException(409, "Idempotency key was already used with a different request")
        return existing
    if source_refs and binding is not None:
        service.validate_source_references(session, binding.ontology_id, source_refs)
    run = GameGenerationRun(ruleset_id=ruleset_id, binding_id=binding.id if binding else None,
                            ontology_id=binding.ontology_id if binding else None,
                            owner_id=principal.user_id, idempotency_key=payload.idempotency_key,
                            input_sources=source_refs, status="queued", attempt=1)
    session.add(run)
    session.commit()
    session.refresh(run)
    from app.tasks.game_engine import run_generation_placeholder
    try:
        run_generation_placeholder.delay(run.id)
    except Exception as exc:
        run.status, run.error_code, run.finished_at = "failed", "dispatch_failed", service.now()
        session.commit()
        raise HTTPException(503, "Generation run could not be queued") from exc
    return run


@router.post("/rulesets/{ruleset_id}/entity-discovery", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
def create_entity_discovery(ruleset_id: str, payload: EntityDiscoveryCreate, token: str = Depends(oauth2_scheme),
                            principal: ShrecknetPrincipal = Depends(_manager), session: Session = Depends(get_db)):
    ruleset = session.get(GameRuleset, ruleset_id)
    if not ruleset:
        raise HTTPException(404, "Ruleset not found")
    binding = session.get(GameBinding, payload.binding_id)
    if not binding or binding.ruleset_id != ruleset_id:
        raise HTTPException(404, "Binding not found")
    _authorize_ontology(binding.ontology_id, token)
    if len(set(payload.source_ids)) != len(payload.source_ids):
        raise HTTPException(422, "Duplicate source IDs are not allowed")
    inputs = []
    for source_id in payload.source_ids:
        row = session.execute(select(ShreckWorldDocument, ShreckWorldLibraryItem).join(
            ShreckWorldLibraryItem, ShreckWorldLibraryItem.id == ShreckWorldDocument.library_item_id
        ).join(ShreckWorldDocumentBookMap, ShreckWorldDocumentBookMap.document_id == ShreckWorldDocument.id).where(
            ShreckWorldDocument.library_item_id == source_id, ShreckWorldDocument.is_active.is_(True),
            ShreckWorldLibraryItem.ontology_id == binding.ontology_id,
        )).first()
        if row is None:
            raise HTTPException(422, "Every selected source must be indexed and have a Book Map")
        document, item = row
        inputs.append({"source_id": item.id, "title": item.title, "edition": "",
                       "document_id": document.id, "content_digest": document.source_sha256})
    existing = session.scalar(select(GameGenerationRun).where(GameGenerationRun.owner_id == principal.user_id,
        GameGenerationRun.idempotency_key == payload.idempotency_key))
    if existing:
        if existing.ruleset_id != ruleset_id or existing.binding_id != binding.id or existing.input_sources != inputs:
            raise HTTPException(409, "Idempotency key was already used with a different request")
        return existing
    run = GameGenerationRun(ruleset_id=ruleset_id, binding_id=binding.id, ontology_id=binding.ontology_id,
        owner_id=principal.user_id, idempotency_key=payload.idempotency_key, input_sources=inputs,
        status="queued", attempt=1, result={"job_type": "entity_discovery", "progress": 0})
    session.add(run); session.commit(); session.refresh(run)
    from app.tasks.game_engine import run_entity_discovery
    try:
        run_entity_discovery.delay(run.id)
    except Exception as exc:
        run.status, run.error_code, run.finished_at = "failed", "dispatch_failed", service.now()
        session.commit()
        raise HTTPException(503, "Entity discovery could not be queued") from exc
    return run


@router.post("/runs/{run_id}/entity-review", response_model=RunRead)
def review_entity_discovery(run_id: str, payload: EntityDiscoveryReview,
                            principal: ShrecknetPrincipal = Depends(_reviewer), session: Session = Depends(get_db)):
    run = service.run_for_owner(session, run_id, session.scalar(select(GameGenerationRun.owner_id).where(GameGenerationRun.id == run_id)))
    if not run.result or run.result.get("job_type") != "entity_discovery" or run.status != "awaiting_review":
        raise HTTPException(409, "Entity discovery result is not awaiting review")
    candidates = run.result.get("candidates", [])
    selected = set(payload.candidate_ids) if payload.candidate_ids else {candidate["id"] for candidate in candidates}
    known = {candidate["id"] for candidate in candidates}
    if not selected or not selected <= known:
        raise HTTPException(422, "Candidate selection is invalid")
    for candidate in candidates:
        if candidate["id"] in selected:
            candidate["review_status"] = "approved" if payload.decision == "approve" else "rejected"
            candidate["review_reason"] = payload.reason
    run.result = {**run.result, "candidates": candidates, "reviewer_id": principal.user_id,
                  "review_decision": payload.decision, "reviewed_at": service.now().isoformat()}
    run.status = "succeeded"
    run.updated_at = run.finished_at = service.now()
    session.commit(); session.refresh(run)
    return run


@router.get("/runs", response_model=RunPage)
def list_runs(limit: int = Query(25, ge=1, le=100), cursor: str | None = None,
              ontology_id: int | None = Query(None, gt=0), token: str | None = Depends(oauth2_scheme),
              principal: ShrecknetPrincipal = Depends(_manager), session: Session = Depends(get_db)):
    if ontology_id is not None:
        _authorize_ontology(ontology_id, token or "")
    query = select(GameGenerationRun).where(GameGenerationRun.owner_id == principal.user_id)
    if ontology_id is not None:
        query = query.where(GameGenerationRun.ontology_id == ontology_id)
    decoded = service._decode_cursor(cursor)
    if decoded:
        created_at, row_id = decoded
        query = query.where((GameGenerationRun.created_at > created_at) |
                            ((GameGenerationRun.created_at == created_at) & (GameGenerationRun.id > row_id)))
    rows = list(session.scalars(query.order_by(GameGenerationRun.created_at, GameGenerationRun.id).limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = service._cursor(rows[-1].created_at, rows[-1].id) if more and rows else None
    return RunPage(items=rows, next_cursor=next_cursor)


@router.get("/runs/{run_id}", response_model=RunRead)
def get_run(run_id: str, principal: ShrecknetPrincipal = Depends(_manager), session: Session = Depends(get_db)):
    return service.run_for_owner(session, run_id, principal.user_id)


@router.post("/runs/{run_id}/retries", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED)
def retry_run(run_id: str, idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
              principal: ShrecknetPrincipal = Depends(_manager), session: Session = Depends(get_db)):
    original = service.run_for_owner(session, run_id, principal.user_id)
    existing = session.scalar(select(GameGenerationRun).where(GameGenerationRun.owner_id == principal.user_id,
                                                                GameGenerationRun.idempotency_key == idempotency_key))
    if existing:
        if existing.retry_of_id != original.id:
            raise HTTPException(409, "Idempotency key was already used")
        return existing
    if original.status not in {"failed", "cancelled"}:
        raise HTTPException(409, "Run is not retryable")
    retry = GameGenerationRun(ruleset_id=original.ruleset_id, binding_id=original.binding_id,
                              ontology_id=original.ontology_id, owner_id=principal.user_id,
                              idempotency_key=idempotency_key, input_sources=original.input_sources,
                              status="queued", attempt=original.attempt + 1, retry_of_id=original.id,
                              result=({"job_type": "entity_discovery", "progress": 0}
                                      if original.result and original.result.get("job_type") == "entity_discovery" else None))
    session.add(retry)
    session.commit()
    session.refresh(retry)
    from app.tasks.game_engine import run_entity_discovery, run_generation_placeholder
    try:
        (run_entity_discovery if retry.result else run_generation_placeholder).delay(retry.id)
    except Exception as exc:
        retry.status, retry.error_code, retry.finished_at = "failed", "dispatch_failed", service.now()
        session.commit()
        raise HTTPException(503, "Generation run could not be queued") from exc
    return retry


@router.post("/runs/{run_id}/cancel", response_model=RunRead)
def cancel_run(run_id: str, principal: ShrecknetPrincipal = Depends(_manager), session: Session = Depends(get_db)):
    run = service.run_for_owner(session, run_id, principal.user_id)
    if run.status in {"queued", "running"}:
        run.status = "cancel_requested" if run.status == "running" else "cancelled"
        run.updated_at = service.now()
        if run.status == "cancelled":
            run.finished_at = run.updated_at
        session.commit()
        session.refresh(run)
    return run


@router.post("/tables", response_model=TableRead, status_code=status.HTTP_201_CREATED)
def create_table(payload: TableCreate, token: str = Depends(oauth2_scheme),
                 principal: ShrecknetPrincipal = Depends(_manager), session: Session = Depends(get_db)):
    _authorize_ontology(payload.ontology_id, token)
    return service.create_table(session, payload, principal.user_id)


@router.get("/tables", response_model=TablePage)
def list_tables(ontology_id: int = Query(gt=0), limit: int = Query(25, ge=1, le=100), cursor: str | None = None,
                token: str = Depends(oauth2_scheme), _: ShrecknetPrincipal = Depends(_manager),
                session: Session = Depends(get_db)):
    _authorize_ontology(ontology_id, token)
    items, next_cursor = service.list_tables(session, ontology_id, limit, cursor)
    return TablePage(items=items, next_cursor=next_cursor)


@router.get("/tables/{table_id}", response_model=TableRead)
def get_table(table_id: str, ontology_id: int = Query(gt=0), token: str = Depends(oauth2_scheme),
              _: ShrecknetPrincipal = Depends(_manager), session: Session = Depends(get_db)):
    _authorize_ontology(ontology_id, token)
    return service.get_table(session, table_id, ontology_id)


@router.patch("/tables/{table_id}", response_model=TableRead)
def update_table(table_id: str, payload: TableUpdate, ontology_id: int = Query(gt=0),
                 if_match: str = Header(alias="If-Match"), token: str = Depends(oauth2_scheme),
                 _: ShrecknetPrincipal = Depends(_manager), session: Session = Depends(get_db)):
    _authorize_ontology(ontology_id, token)
    try:
        revision = int(if_match.strip('"'))
    except ValueError as exc:
        raise HTTPException(422, "If-Match must contain the table revision") from exc
    return service.update_table(session, table_id, ontology_id, revision, payload)


@router.delete("/tables/{table_id}", response_model=TableRead)
def archive_table(table_id: str, ontology_id: int = Query(gt=0), token: str = Depends(oauth2_scheme),
                  _: ShrecknetPrincipal = Depends(_manager), session: Session = Depends(get_db)):
    _authorize_ontology(ontology_id, token)
    return service.archive_table(session, table_id, ontology_id)


@router.put("/tables/{table_id}/participants", response_model=ParticipantRead, status_code=status.HTTP_201_CREATED)
def add_participant(table_id: str, payload: ParticipantCreate, ontology_id: int = Query(gt=0),
                    token: str = Depends(oauth2_scheme), _: ShrecknetPrincipal = Depends(_manager),
                    session: Session = Depends(get_db)):
    _authorize_ontology(ontology_id, token)
    try:
        user = shrecknet_read(f"/contracts/users/{payload.user_id}", token)
    except HTTPException:
        raise
    if payload.character_agent_id:
        agent = shrecknet_read(f"/character-agents/{payload.character_agent_id}", token)
        if not isinstance(agent, dict) or agent.get("ontology_id") != ontology_id:
            raise HTTPException(409, "Character Agent does not belong to this ontology")
        try:
            agent_owner = int(agent.get("created_by_user_id"))
            participant_id = int(payload.user_id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(409, "Character Agent ownership could not be verified") from exc
        if agent_owner != participant_id:
            raise HTTPException(409, "Character Agent does not belong to this participant")
    if not isinstance(user, dict) or str(user.get("id")) != payload.user_id:
        raise HTTPException(404, "Participant not found")
    return service.add_participant(session, table_id, ontology_id, user_id=payload.user_id,
                                   role=payload.role, character_agent_id=payload.character_agent_id)


@router.delete("/tables/{table_id}/participants/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_participant(table_id: str, user_id: str, ontology_id: int = Query(gt=0),
                       token: str = Depends(oauth2_scheme), _: ShrecknetPrincipal = Depends(_manager),
                       session: Session = Depends(get_db)):
    _authorize_ontology(ontology_id, token)
    service.remove_participant(session, table_id, ontology_id, user_id)
