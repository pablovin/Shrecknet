from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import get_settings
from app.db.session import get_db
from app.models import ShreckWorld, ShreckWorldEmbeddingJob, ShreckWorldLibraryItem
from app.schemas.shreckworld import (
    AuthorityTier, EmbeddingJobRead, ShreckWorldCreate, ShreckWorldLibraryItemRead,
    ShreckWorldLibraryItemUpdate, ShreckWorldQueryRequest, ShreckWorldQueryResponse,
    ShreckWorldRead, ShreckWorldUpdate,
)
from app.services.query import query_shreckworld

admin_router = APIRouter(prefix="/admin/shreckworlds", tags=["ShreckWorld admin"])
query_router = APIRouter(prefix="/shreckworlds", tags=["ShreckWorld query"])
router = APIRouter()


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


@admin_router.post("", response_model=ShreckWorldRead, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
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


@admin_router.get("", response_model=list[ShreckWorldRead], dependencies=[Depends(require_admin)])
def list_shreckworlds(session: Session = Depends(get_db)) -> list[ShreckWorld]:
    return list(session.scalars(select(ShreckWorld).order_by(ShreckWorld.name)))


@admin_router.get("/{shreckworld_id}", response_model=ShreckWorldRead, dependencies=[Depends(require_admin)])
def get_shreckworld(shreckworld_id: str, session: Session = Depends(get_db)) -> ShreckWorld:
    return _world_or_404(session, shreckworld_id)


@admin_router.patch("/{shreckworld_id}", response_model=ShreckWorldRead, dependencies=[Depends(require_admin)])
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


@admin_router.delete("/{shreckworld_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
def delete_shreckworld(shreckworld_id: str, session: Session = Depends(get_db)) -> None:
    world = _world_or_404(session, shreckworld_id)
    root = get_settings().media_root / "shreckworlds" / world.id
    session.delete(world); session.commit()
    shutil.rmtree(root, ignore_errors=True)


@admin_router.post("/{shreckworld_id}/library-items", response_model=ShreckWorldLibraryItemRead, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
def upload_library_item(
    shreckworld_id: str, title: str, pdf: UploadFile = File(...), authors: str | None = None,
    description: str | None = None, authority_tier: AuthorityTier = "core_rules", authority_priority: int = 100,
    session: Session = Depends(get_db),
) -> ShreckWorldLibraryItem:
    _world_or_404(session, shreckworld_id)
    if pdf.content_type not in {"application/pdf", "application/x-pdf"}:
        raise HTTPException(status_code=415, detail="Only PDF uploads are supported")
    raw = pdf.file.read()
    if not raw or len(raw) > get_settings().max_pdf_bytes:
        raise HTTPException(status_code=413, detail="PDF is empty or exceeds the configured size limit")
    source_hash = hashlib.sha256(raw).hexdigest()
    item = ShreckWorldLibraryItem(
        shreckworld_id=shreckworld_id, title=title.strip(), authors=authors, description=description,
        authority_tier=authority_tier, authority_priority=authority_priority,
        pdf_path="pending", source_sha256=source_hash,
    )
    session.add(item); session.flush()
    target = get_settings().media_root / "shreckworlds" / shreckworld_id / item.id / "source.pdf"
    target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
    item.pdf_path = str(target.resolve())
    try:
        session.commit()
    except Exception as exc:
        session.rollback(); target.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail="A source with that title already exists in this ShreckWorld") from exc
    session.refresh(item)
    return item


@admin_router.get("/{shreckworld_id}/library-items", response_model=list[ShreckWorldLibraryItemRead], dependencies=[Depends(require_admin)])
def list_library_items(shreckworld_id: str, session: Session = Depends(get_db)) -> list[ShreckWorldLibraryItem]:
    _world_or_404(session, shreckworld_id)
    return list(session.scalars(select(ShreckWorldLibraryItem).where(ShreckWorldLibraryItem.shreckworld_id == shreckworld_id).order_by(ShreckWorldLibraryItem.authority_priority, ShreckWorldLibraryItem.title)))


@admin_router.patch("/{shreckworld_id}/library-items/{item_id}", response_model=ShreckWorldLibraryItemRead, dependencies=[Depends(require_admin)])
def update_library_item(shreckworld_id: str, item_id: str, payload: ShreckWorldLibraryItemUpdate, session: Session = Depends(get_db)) -> ShreckWorldLibraryItem:
    item = _item_or_404(session, shreckworld_id, item_id)
    for field, value in payload.model_dump(exclude_unset=True).items(): setattr(item, field, value)
    session.commit(); session.refresh(item)
    return item


@admin_router.delete("/{shreckworld_id}/library-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
def delete_library_item(shreckworld_id: str, item_id: str, session: Session = Depends(get_db)) -> None:
    item = _item_or_404(session, shreckworld_id, item_id)
    root = Path(item.pdf_path).parent
    session.delete(item); session.commit(); shutil.rmtree(root, ignore_errors=True)


@admin_router.post("/{shreckworld_id}/library-items/{item_id}/embed", response_model=EmbeddingJobRead, status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_admin)])
def start_embedding(shreckworld_id: str, item_id: str, session: Session = Depends(get_db)) -> ShreckWorldEmbeddingJob:
    item = _item_or_404(session, shreckworld_id, item_id)
    if item.embedding_status in {"queued", "embedding"}:
        raise HTTPException(status_code=409, detail="Embedding is already running")
    job = ShreckWorldEmbeddingJob(library_item_id=item.id)
    item.embedding_status = "queued"; item.embedding_error = None
    session.add(job); session.commit(); session.refresh(job)
    from app.tasks.embedding import embed_shreckworld_library_item
    embed_shreckworld_library_item.delay(item.id, job.id)
    return job


@admin_router.get("/{shreckworld_id}/library-items/{item_id}/embedding-status", response_model=EmbeddingJobRead, dependencies=[Depends(require_admin)])
def embedding_status(shreckworld_id: str, item_id: str, session: Session = Depends(get_db)) -> ShreckWorldEmbeddingJob:
    item = _item_or_404(session, shreckworld_id, item_id)
    job = session.scalar(select(ShreckWorldEmbeddingJob).where(ShreckWorldEmbeddingJob.library_item_id == item.id).order_by(ShreckWorldEmbeddingJob.created_at.desc()))
    if job is None: raise HTTPException(status_code=404, detail="No embedding job exists")
    return job


@query_router.post("/{shreckworld_id}/query", response_model=ShreckWorldQueryResponse, dependencies=[Depends(require_admin)])
def query(shreckworld_id: str, payload: ShreckWorldQueryRequest, session: Session = Depends(get_db)) -> ShreckWorldQueryResponse:
    _world_or_404(session, shreckworld_id)
    citations, trace = query_shreckworld(session, shreckworld_id=shreckworld_id, query=payload.query, top_k=payload.top_k)
    if citations:
        answer = "Relevant ShreckWorld evidence was found: " + " ".join(f"[{citation.source_id}] {citation.excerpt}" for citation in citations)
    else:
        answer = "No embedded source in this ShreckWorld contains enough evidence to answer that question."
    return ShreckWorldQueryResponse(shreckworld_id=shreckworld_id, query=payload.query, answer=answer, citations=citations, trace=trace if payload.include_trace else None)


router.include_router(admin_router)
router.include_router(query_router)
