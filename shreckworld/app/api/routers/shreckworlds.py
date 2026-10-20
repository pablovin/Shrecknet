from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
import shutil
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.private_files import private_file_response
from app.api.deps import ShrecknetPrincipal, get_current_user, require_world_manager
from app.core.config import get_settings
from app.db.session import get_db
from app.jobs.shreckworld_agent.agent import QueryScopeError, run_shreckworld_agent, validate_query_scope
from app.models import ShreckWorld, ShreckWorldAgentJob, ShreckWorldEmbeddingJob, ShreckWorldLibraryItem
from app.schemas.shreckworld import (
    AnswerPresentation, AuthorityTier, EmbeddingJobRead, GroundedClaim, GroundedConflict, ShreckWorldAgentJobRead,
    ShreckWorldCreate, ShreckWorldLibraryItemRead,
    ShreckWorldLibraryItemUpdate, ShreckWorldQueryRequest, ShreckWorldQueryResponse,
    ShreckWorldRead, ShreckWorldUpdate, ActionResolutionRequest, ActionResolutionResponse,
    QuerySourceCoverage,
    ShreckWorldModelSettings,
)
from app.services.query import query_shreckworld
from app.services.rule_resolution import resolve_action
from app.services.embedding import EmbeddingInputError, EmbeddingUnavailable, get_embedder
from app.services.document_metadata import resolve_upload_metadata
from app.services.grounded_answer import (
    EvidenceBudgetExceeded,
    GroundedAnswerInvalid,
    GroundedAnswerUnavailable,
    evidence_item,
    synthesize_grounded_answer,
)
from app.services.source_lifecycle import delete_source, delete_world
from app.services.model_settings import (
    get_model_shreckworld_synthesizer,
    save_model_shreckworld_synthesizer,
)

logger = logging.getLogger(__name__)

admin_router = APIRouter(prefix="/admin/shreckworlds", tags=["ShreckWorld admin"])
settings_router = APIRouter(prefix="/admin/settings", tags=["ShreckWorld settings"])
query_router = APIRouter(prefix="/shreckworlds", tags=["ShreckWorld query"])
router = APIRouter()


@contextmanager
def _semantic_errors():
    """Use one HTTP error contract for query and agent rule-review transport."""
    try:
        yield
    except EmbeddingUnavailable as exc:
        raise HTTPException(status_code=503, detail="Semantic retrieval is unavailable. Reading remains available.") from exc
    except EmbeddingInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

def _world_or_404(session: Session, shreckworld_id: str) -> ShreckWorld:
    world = session.get(ShreckWorld, shreckworld_id)
    if world is None:
        raise HTTPException(status_code=404, detail="ShreckWorld not found")
    return world


def _item_or_404(session: Session, shreckworld_id: str, item_id: str) -> ShreckWorldLibraryItem:
    item = session.get(ShreckWorldLibraryItem, item_id)
    if item is None or item.shreckworld_id != shreckworld_id:
        raise HTTPException(status_code=404, detail="ShreckWorld library item not found")
    return item


def _source_warnings(trace: dict) -> list[dict[str, str | int]]:
    warnings: list[dict[str, str | int]] = []
    not_indexed = int(trace.get("sources_without_ready_index", 0))
    requires_reindex = int(trace.get("sources_requiring_reindex", 0))
    if not_indexed:
        warnings.append({
            "code": "source_not_indexed",
            "count": not_indexed,
            "message": f"{not_indexed} requested source(s) have not been indexed; results use available indexed sources.",
        })
    if requires_reindex:
        warnings.append({
            "code": "source_requires_reindex",
            "count": requires_reindex,
            "message": f"{requires_reindex} requested source(s) need reindexing; results use compatible active sources.",
        })
    return warnings


@settings_router.get(
    "/model-targets/shreckworld_synthesizer",
    response_model=ShreckWorldModelSettings,
    dependencies=[Depends(require_world_manager)],
)
def get_model_settings(session: Session = Depends(get_db)) -> ShreckWorldModelSettings:
    return ShreckWorldModelSettings(
        model_shreckworld_synthesizer=get_model_shreckworld_synthesizer(session)
    )


@settings_router.put(
    "/model-targets/shreckworld_synthesizer",
    response_model=ShreckWorldModelSettings,
    dependencies=[Depends(require_world_manager)],
)
def update_model_settings(
    payload: ShreckWorldModelSettings,
    session: Session = Depends(get_db),
) -> ShreckWorldModelSettings:
    target = save_model_shreckworld_synthesizer(
        session, payload.model_shreckworld_synthesizer
    )
    return ShreckWorldModelSettings(model_shreckworld_synthesizer=target)


@admin_router.post("", response_model=ShreckWorldRead, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_world_manager)])
def create_shreckworld(payload: ShreckWorldCreate, session: Session = Depends(get_db)) -> ShreckWorld:
    world = ShreckWorld(**payload.model_dump())
    session.add(world)
    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="A ShreckWorld with that name already exists") from exc
    session.refresh(world)
    return world


@query_router.get("", response_model=list[ShreckWorldRead], dependencies=[Depends(get_current_user)])
@admin_router.get("", response_model=list[ShreckWorldRead], dependencies=[Depends(require_world_manager)])
def list_shreckworlds(session: Session = Depends(get_db)) -> list[ShreckWorld]:
    return list(session.scalars(select(ShreckWorld).order_by(ShreckWorld.name)))


@query_router.get("/{shreckworld_id}", response_model=ShreckWorldRead, dependencies=[Depends(get_current_user)])
@admin_router.get("/{shreckworld_id}", response_model=ShreckWorldRead, dependencies=[Depends(require_world_manager)])
def get_shreckworld(shreckworld_id: str, session: Session = Depends(get_db)) -> ShreckWorld:
    return _world_or_404(session, shreckworld_id)


@admin_router.patch("/{shreckworld_id}", response_model=ShreckWorldRead, dependencies=[Depends(require_world_manager)])
def update_shreckworld(shreckworld_id: str, payload: ShreckWorldUpdate, session: Session = Depends(get_db)) -> ShreckWorld:
    world = _world_or_404(session, shreckworld_id)
    updates = payload.model_dump(exclude_unset=True)
    enabled = updates.get("web_search_enabled", world.web_search_enabled)
    policy = updates.get("web_search_policy", world.web_search_policy)
    if not enabled and policy != "disabled":
        raise HTTPException(status_code=422, detail="web_search_policy must be disabled while web search is disabled")
    for field, value in updates.items():
        setattr(world, field, value)
    session.commit(); session.refresh(world)
    return world


@admin_router.delete("/{shreckworld_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None, dependencies=[Depends(require_world_manager)])
def delete_shreckworld(shreckworld_id: str, session: Session = Depends(get_db)) -> None:
    world = _world_or_404(session, shreckworld_id)
    delete_world(session, world)


@admin_router.post("/{shreckworld_id}/library-items", response_model=ShreckWorldLibraryItemRead, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_world_manager)])
def upload_library_item(
    shreckworld_id: str, title: str | None = None, pdf: UploadFile = File(...), authors: str | None = None,
    description: str | None = None, authority_tier: AuthorityTier = "core_rules", authority_priority: int = 100,
    auto_extract_metadata: bool = Form(False, description="Fill empty title, authors and description from document properties and render a PDF cover."),
    form_title: str | None = Form(None, alias="title", validation_alias="title"),
    form_authors: str | None = Form(None, alias="authors", validation_alias="authors"),
    form_description: str | None = Form(None, alias="description", validation_alias="description"),
    session: Session = Depends(get_db),
) -> ShreckWorldLibraryItem:
    _world_or_404(session, shreckworld_id)
    extension = Path(pdf.filename or "").suffix.lower()
    allowed_types = {".pdf", ".docx", ".md", ".txt"}
    if extension not in allowed_types:
        raise HTTPException(status_code=415, detail="Supported source types are PDF, DOCX, Markdown, and text")
    raw = pdf.file.read()
    if not raw or len(raw) > get_settings().max_document_bytes:
        raise HTTPException(status_code=413, detail="Source is empty or exceeds the configured size limit")
    try:
        metadata = resolve_upload_metadata(
            raw, filename=pdf.filename or "", title=form_title if form_title is not None else title,
            authors=form_authors if form_authors is not None else authors,
            description=form_description if form_description is not None else description,
            auto_extract_metadata=auto_extract_metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    source_hash = hashlib.sha256(raw).hexdigest()
    item = ShreckWorldLibraryItem(
        shreckworld_id=shreckworld_id, title=metadata.title, authors=metadata.authors, description=metadata.description,
        authority_tier=authority_tier, authority_priority=authority_priority,
        pdf_path="pending", storage_path="pending", original_filename=pdf.filename,
        mime_type=pdf.content_type or mimetypes.guess_type(pdf.filename or "")[0] or "application/octet-stream",
        file_size=len(raw), source_sha256=source_hash, index_status="unindexed",
    )
    session.add(item)
    try:
        session.flush()
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="A source with that title already exists in this ShreckWorld") from exc
    target = get_settings().media_root / "shreckworlds" / shreckworld_id / item.id / f"source{extension}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        item.pdf_path = str(target.resolve())  # legacy worker compatibility
        item.storage_path = item.pdf_path
        if metadata.cover:
            cover = target.parent / "cover.png"
            cover.write_bytes(metadata.cover)
            item.cover_path = str(cover.resolve())
        session.commit()
    except OSError as exc:
        session.rollback()
        shutil.rmtree(target.parent, ignore_errors=True)
        raise HTTPException(status_code=500, detail="Unable to store source files") from exc
    except Exception as exc:
        session.rollback(); shutil.rmtree(target.parent, ignore_errors=True)
        raise HTTPException(status_code=409, detail="A source with that title already exists in this ShreckWorld") from exc
    session.refresh(item)
    return item


@query_router.get("/{shreckworld_id}/library-items", response_model=list[ShreckWorldLibraryItemRead], dependencies=[Depends(get_current_user)])
@admin_router.get("/{shreckworld_id}/library-items", response_model=list[ShreckWorldLibraryItemRead], dependencies=[Depends(require_world_manager)])
def list_library_items(shreckworld_id: str, session: Session = Depends(get_db)) -> list[ShreckWorldLibraryItem]:
    _world_or_404(session, shreckworld_id)
    return list(session.scalars(select(ShreckWorldLibraryItem).where(ShreckWorldLibraryItem.shreckworld_id == shreckworld_id).order_by(ShreckWorldLibraryItem.authority_priority, ShreckWorldLibraryItem.title)))


@admin_router.patch("/{shreckworld_id}/library-items/{item_id}", response_model=ShreckWorldLibraryItemRead, dependencies=[Depends(require_world_manager)])
def update_library_item(shreckworld_id: str, item_id: str, payload: ShreckWorldLibraryItemUpdate, session: Session = Depends(get_db)) -> ShreckWorldLibraryItem:
    item = _item_or_404(session, shreckworld_id, item_id)
    for field, value in payload.model_dump(exclude_unset=True).items(): setattr(item, field, value)
    session.commit(); session.refresh(item)
    return item


@admin_router.delete("/{shreckworld_id}/library-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None, dependencies=[Depends(require_world_manager)])
def delete_library_item(shreckworld_id: str, item_id: str, session: Session = Depends(get_db)) -> None:
    item = _item_or_404(session, shreckworld_id, item_id)
    delete_source(session, item)


@query_router.get("/{shreckworld_id}/library-items/{item_id}", response_model=ShreckWorldLibraryItemRead, dependencies=[Depends(get_current_user)])
def get_library_item(shreckworld_id: str, item_id: str, session: Session = Depends(get_db)) -> ShreckWorldLibraryItem:
    return _item_or_404(session, shreckworld_id, item_id)


@query_router.head("/{shreckworld_id}/library-items/{item_id}/content", dependencies=[Depends(get_current_user)])
@query_router.get("/{shreckworld_id}/library-items/{item_id}/content", dependencies=[Depends(get_current_user)])
def download_library_item(shreckworld_id: str, item_id: str, request: Request, session: Session = Depends(get_db)) -> Response:
    """Private download route for the shared Shrecknet frontend."""
    item = _item_or_404(session, shreckworld_id, item_id)
    path = Path(item.storage_path or item.pdf_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Source file not found")
    return private_file_response(path, request=request, media_type=item.mime_type or "application/octet-stream",
                                 filename=item.original_filename or path.name, revision=item.source_sha256)


@query_router.get("/{shreckworld_id}/library-items/{item_id}/cover", dependencies=[Depends(get_current_user)])
def download_library_cover(shreckworld_id: str, item_id: str, session: Session = Depends(get_db)) -> FileResponse:
    item = _item_or_404(session, shreckworld_id, item_id)
    if not item.cover_path or not Path(item.cover_path).is_file():
        raise HTTPException(status_code=404, detail="Source cover not found")
    return FileResponse(item.cover_path, media_type="image/png", filename="cover.png")


@admin_router.post("/{shreckworld_id}/library-items/{item_id}/embed", response_model=EmbeddingJobRead, status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_world_manager)])
def start_embedding(shreckworld_id: str, item_id: str, session: Session = Depends(get_db)) -> ShreckWorldEmbeddingJob:
    item = _item_or_404(session, shreckworld_id, item_id)
    if item.embedding_status in {"queued", "embedding"}:
        raise HTTPException(status_code=409, detail="Embedding is already running")
    try:
        get_embedder().ensure_ready()
    except EmbeddingUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    job = ShreckWorldEmbeddingJob(library_item_id=item.id, created_at=datetime.now(timezone.utc))
    item.embedding_status = "queued"; item.embedding_error = None
    item.index_status, item.index_error = "queued", None
    session.add(job); session.commit(); session.refresh(job)
    from app.tasks.embedding import embed_shreckworld_library_item
    embed_shreckworld_library_item.delay(item.id, job.id)
    return job


@admin_router.post("/{shreckworld_id}/library-items/{item_id}/index", response_model=EmbeddingJobRead, status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_world_manager)])
def start_indexing(shreckworld_id: str, item_id: str, session: Session = Depends(get_db)) -> ShreckWorldEmbeddingJob:
    """Preferred public name for the legacy-compatible embedding task."""
    return start_embedding(shreckworld_id, item_id, session)


@admin_router.get("/{shreckworld_id}/library-items/{item_id}/index-status", response_model=EmbeddingJobRead, dependencies=[Depends(require_world_manager)])
def index_status(shreckworld_id: str, item_id: str, session: Session = Depends(get_db)) -> ShreckWorldEmbeddingJob:
    return embedding_status(shreckworld_id, item_id, session)


@admin_router.get("/{shreckworld_id}/library-items/{item_id}/embedding-status", response_model=EmbeddingJobRead, dependencies=[Depends(require_world_manager)])
def embedding_status(shreckworld_id: str, item_id: str, session: Session = Depends(get_db)) -> ShreckWorldEmbeddingJob:
    item = _item_or_404(session, shreckworld_id, item_id)
    job = session.scalar(select(ShreckWorldEmbeddingJob).where(ShreckWorldEmbeddingJob.library_item_id == item.id).order_by(ShreckWorldEmbeddingJob.created_at.desc(), ShreckWorldEmbeddingJob.id.desc()))
    if job is None: raise HTTPException(status_code=404, detail="No embedding job exists")
    return job


@admin_router.get("/{shreckworld_id}/library-items/{item_id}/index-jobs/{job_id}", response_model=EmbeddingJobRead, dependencies=[Depends(require_world_manager)])
def get_indexing_job(shreckworld_id: str, item_id: str, job_id: str, session: Session = Depends(get_db)) -> ShreckWorldEmbeddingJob:
    _item_or_404(session, shreckworld_id, item_id)
    job = session.get(ShreckWorldEmbeddingJob, job_id)
    if job is None or job.library_item_id != item_id:
        raise HTTPException(status_code=404, detail="Indexing job not found")
    return job


@query_router.post("/{shreckworld_id}/query", response_model=ShreckWorldQueryResponse, dependencies=[Depends(get_current_user)])
def query(shreckworld_id: str, payload: ShreckWorldQueryRequest, session: Session = Depends(get_db)) -> ShreckWorldQueryResponse:
    started = time.perf_counter()
    if payload.retrieval_strategy == "planned":
        try:
            validate_query_scope(session, shreckworld_id=shreckworld_id, payload=payload)
        except QueryScopeError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        with _semantic_errors():
            try:
                return run_shreckworld_agent(session, shreckworld_id=shreckworld_id, request=payload)
            except QueryScopeError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    _world_or_404(session, shreckworld_id)
    if payload.library_item_id:
        _item_or_404(session, shreckworld_id, payload.library_item_id)
    if payload.library_item_ids is not None:
        for item_id in payload.library_item_ids:
            _item_or_404(session, shreckworld_id, item_id)
    if payload.selection:
        selected = _item_or_404(session, shreckworld_id, payload.selection.library_item_id)
        if selected.source_sha256 != payload.selection.source_sha256:
            raise HTTPException(status_code=409, detail="The selected document has changed. Reopen it before asking.")
        if payload.library_item_id and payload.selection.library_item_id != payload.library_item_id:
            raise HTTPException(status_code=422, detail="Selection must belong to the requested book")
        if payload.library_item_ids is not None and payload.selection.library_item_id not in payload.library_item_ids:
            raise HTTPException(status_code=422, detail="Selection must belong to one of the requested books")
    # Selection supplies untrusted retrieval context, never model instructions.
    retrieval_query = payload.query + (" " + payload.selection.text if payload.selection else "")
    with _semantic_errors():
        citations, trace = query_shreckworld(session, shreckworld_id=shreckworld_id, query=retrieval_query,
                                           top_k=payload.top_k, library_item_id=payload.library_item_id,
                                           library_item_ids=payload.library_item_ids)
    answer_status: Literal["complete", "partial", "unsupported"] | None = None
    claims: list[GroundedClaim] = []
    missing_information: list[str] = []
    conflicts: list[GroundedConflict] = []
    omitted_citations = 0
    answer_content: AnswerPresentation | None = None
    evidence = [evidence_item(citation) for citation in citations]
    source_warnings = _source_warnings(trace)
    if citations:
        answer = "Relevant ShreckWorld evidence was found: " + " ".join(f"[{citation.source_id}] {citation.excerpt}" for citation in citations)
    elif trace.get("sources_requiring_reindex"):
        answer = "Sources require reindexing with the verified semantic model before they can be searched."
    else:
        answer = "No embedded source in this ShreckWorld contains enough evidence to answer that question."
    if payload.mode == "answer":
        if not citations:
            answer_status = "unsupported"
            missing_information = ["No compatible indexed passage was retrieved for this query."]
            if trace["sources_without_ready_index"]:
                missing_information.append(
                    f"{trace['sources_without_ready_index']} requested source(s) have no ready index."
                )
            if trace["sources_requiring_reindex"]:
                missing_information.append(
                    f"{trace['sources_requiring_reindex']} requested source(s) require reindexing."
                )
            answer_content = AnswerPresentation(
                direct_answer=answer,
                follow_up_hint="Try another indexed source or rephrase the question.",
            )
        else:
            synthesis_started = time.perf_counter()
            try:
                (
                    answer, answer_status, claims, missing_information, conflicts,
                    omitted_citations, answer_content, evidence,
                ) = synthesize_grounded_answer(
                    query=payload.query,
                    citations=citations,
                    model_target=get_model_shreckworld_synthesizer(session),
                    session=session,
                    response_detail=payload.response_detail or "standard",
                )
            except EvidenceBudgetExceeded as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except GroundedAnswerUnavailable as exc:
                raise HTTPException(status_code=503, detail="Grounded answer synthesis is unavailable") from exc
            except GroundedAnswerInvalid as exc:
                logger.warning(
                    "ShreckWorld grounded answer rejected (reason=%s)", exc.reason,
                )
                raise HTTPException(
                    status_code=502,
                    detail="Grounded answer synthesis returned invalid structured output or evidence references",
                ) from exc
            trace["synthesis_ms"] = round((time.perf_counter() - synthesis_started) * 1000, 2)
            unavailable_sources = int(trace["sources_without_ready_index"]) + int(trace["sources_requiring_reindex"])
            requires_full_scope = bool(re.search(
                r"\b(?:compare|comparison|versus|difference|both|all|every|exhaustive)\b",
                payload.query.casefold(),
            ))
            if (unavailable_sources and requires_full_scope) or omitted_citations:
                if answer_status == "complete":
                    answer_status = "partial"
                gaps = []
                if unavailable_sources and requires_full_scope:
                    gaps.append(
                        f"{unavailable_sources} requested source(s) were not searched because their indexes are unavailable or incompatible"
                    )
                if omitted_citations:
                    gaps.append(
                        f"{omitted_citations} retrieved passage(s) were omitted to fit the synthesis evidence budget"
                    )
                gap_text = "; ".join(gaps)
                missing_information.append(gap_text + ".")
                missing_information.append(
                    "The result may be incomplete because " + gap_text + "."
                )
    trace["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return ShreckWorldQueryResponse(
        shreckworld_id=shreckworld_id, query=payload.query, answer=answer, citations=citations,
        trace=trace if payload.include_trace else None, mode=payload.mode, answer_status=answer_status,
        claims=claims, missing_information=missing_information, conflicts=conflicts,
        answer_content=answer_content, evidence=evidence, source_warnings=source_warnings,
        source_coverage=QuerySourceCoverage(
            requested_source_count=int(trace["requested_sources"]),
            searched_source_count=int(trace["sources_searched"]),
            sources_without_ready_index=int(trace["sources_without_ready_index"]),
            sources_requiring_reindex=int(trace["sources_requiring_reindex"]),
        ),
    )


def _agent_job_read(job: ShreckWorldAgentJob) -> ShreckWorldAgentJobRead:
    return ShreckWorldAgentJobRead(
        id=job.id, shreckworld_id=job.shreckworld_id, status=job.status, stage=job.stage,
        created_at=job.created_at, started_at=job.started_at, updated_at=job.updated_at,
        completed_at=job.completed_at,
        result=ShreckWorldQueryResponse.model_validate_json(job.result_json) if job.result_json else None,
        error=job.error,
    )


@query_router.post(
    "/{shreckworld_id}/agent-jobs", response_model=ShreckWorldAgentJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_agent_job(
    shreckworld_id: str,
    payload: ShreckWorldQueryRequest,
    principal: ShrecknetPrincipal = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> ShreckWorldAgentJobRead:
    request = payload.model_copy(update={"retrieval_strategy": "planned"})
    try:
        validate_query_scope(session, shreckworld_id=shreckworld_id, payload=request)
    except QueryScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    job = ShreckWorldAgentJob(
        shreckworld_id=shreckworld_id, owner_id=principal.user_id,
        request_json=request.model_dump_json(), created_at=datetime.now(timezone.utc),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    try:
        from app.tasks.agent import run_shreckworld_agent_job
        run_shreckworld_agent_job.delay(job.id)
    except Exception as exc:
        job.status, job.stage = "failed", "failed"
        job.error = "The agent job could not be queued. Please retry."
        job.completed_at = job.updated_at = datetime.now(timezone.utc)
        session.commit()
        raise HTTPException(status_code=503, detail=job.error) from exc
    session.refresh(job)
    return _agent_job_read(job)


@query_router.get("/{shreckworld_id}/agent-jobs/{job_id}", response_model=ShreckWorldAgentJobRead)
def get_agent_job(
    shreckworld_id: str, job_id: str,
    principal: ShrecknetPrincipal = Depends(get_current_user),
    session: Session = Depends(get_db),
) -> ShreckWorldAgentJobRead:
    job = session.get(ShreckWorldAgentJob, job_id)
    if job is None or job.shreckworld_id != shreckworld_id:
        raise HTTPException(status_code=404, detail="ShreckWorld agent job not found")
    if job.owner_id != principal.user_id and principal.role != "admin":
        raise HTTPException(status_code=404, detail="ShreckWorld agent job not found")
    return _agent_job_read(job)


@query_router.post("/{shreckworld_id}/resolve-action", response_model=ActionResolutionResponse, dependencies=[Depends(get_current_user)])
def resolve_world_action(shreckworld_id: str, payload: ActionResolutionRequest, session: Session = Depends(get_db)) -> ActionResolutionResponse:
    _world_or_404(session, shreckworld_id)
    if payload.library_item_id:
        _item_or_404(session, shreckworld_id, payload.library_item_id)
    with _semantic_errors():
        return resolve_action(session, shreckworld_id=shreckworld_id, request=payload)


router.include_router(admin_router)
router.include_router(settings_router)
router.include_router(query_router)
