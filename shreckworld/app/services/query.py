"""Scoped E5/FTS/exact retrieval with shared Librarian fusion and parent evidence.

Reader requests, action resolution, and planned ShreckWorld queries use this
module's retrieval scope and candidate search. Only active, fingerprint-compatible
generations are searched; SQL activation and scope remain authoritative.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from shreckdoc.retrieval import extract_named_terms, reciprocal_rank_fusion, select_diverse

from app.models import ShreckWorldDocumentChunk, ShreckWorldLibraryItem
from app.repositories.documents import active_documents, child_rows, citation_provenance, lexical_candidates
from app.schemas.shreckworld import ShreckWorldCitation
from app.services.embedding import cosine_similarity, get_embedder
from app.services.retrieval_index import search_chunks
from app.services.citation_regions import citation_rectangles as _rectangles
from app.integrations.reranker import get_reranker


@dataclass
class RetrievalScope:
    session: Session
    shreckworld_id: str
    fingerprint: str
    document_ids: list[str]
    rows: list
    by_id: dict[str, tuple[ShreckWorldDocumentChunk, ShreckWorldLibraryItem]]
    trace: dict[str, int | str | bool | float]


def resolve_retrieval_scope(session: Session, *, shreckworld_id: str,
                            library_item_id: str | None = None,
                            library_item_ids: list[str] | None = None) -> RetrievalScope:
    """Resolve active generations and eligible sources once per query."""
    if library_item_id is not None and library_item_ids is not None:
        raise ValueError("Specify either library_item_id or library_item_ids, not both")
    embedder = get_embedder()
    fingerprint = str(embedder.ensure_ready()["fingerprint"])
    documents = active_documents(session, shreckworld_id, fingerprint, library_item_id, library_item_ids)
    document_ids = [document.id for document in documents]
    rows = child_rows(session, document_ids)
    by_id = {chunk.id: (chunk, item) for chunk, item in rows}
    eligible_items = {document.library_item_id for document in documents}
    statement = select(ShreckWorldLibraryItem.id).where(ShreckWorldLibraryItem.shreckworld_id == shreckworld_id)
    if library_item_id is not None:
        statement = statement.where(ShreckWorldLibraryItem.id == library_item_id)
    elif library_item_ids is not None:
        statement = statement.where(ShreckWorldLibraryItem.id.in_(library_item_ids))
    scoped_items = set(session.scalars(statement))
    ready_items = set(session.scalars(
        select(ShreckWorldLibraryItem.id).where(
            ShreckWorldLibraryItem.id.in_(scoped_items),
            ShreckWorldLibraryItem.embedding_status == "ready",
        )
    )) if scoped_items else set()
    searched_items = {item.id for _, item in rows}
    trace: dict[str, int | str | bool | float] = {
        "embedded_chunks_considered": len(rows), "returned_citations": 0, "web_search": "not_implemented",
        "pipeline": "e5_qdrant_contextual_fts_exact_rrf_cross_encoder_parent_v2",
        "embedding_backend": "sentence_transformers", "embedding_model": embedder.settings.embedding_model_id,
        "embedding_fingerprint": fingerprint, "sources_requiring_reindex": len(ready_items - eligible_items),
        "sources_without_ready_index": len(scoped_items - ready_items), "requested_sources": len(scoped_items),
        "sources_searched": len(searched_items), "qdrant_fallback": False, "reranker_fallback": False,
    }
    return RetrievalScope(session, shreckworld_id, fingerprint, document_ids, rows, by_id, trace)


def search_candidates(scope: RetrievalScope, query: str, *, limit: int = 40) -> tuple[list[dict[str, Any]], dict[str, int | bool]]:
    """Search one need without citation hydration or evidence-budget truncation."""
    if not scope.rows:
        return [], {
            "qdrant_fallback": False, "reranker_fallback": False,
            "vector_candidates": 0, "fulltext_candidates": 0, "exact_candidates": 0,
        }
    vector = get_embedder().embed(query, query=True)
    try:
        vector_ids = search_chunks(
            world_id=scope.shreckworld_id, document_ids=scope.document_ids,
            fingerprint=scope.fingerprint, vector=vector,
        )
        qdrant_fallback = False
    except Exception:
        vector_ids = [chunk.id for chunk, _ in sorted(scope.rows, key=lambda row: (
            -cosine_similarity(vector, json.loads(row[0].embedding_json)), row[1].authority_priority, row[0].id
        ))[:limit]]
        qdrant_fallback = True
    lexical_ids = lexical_candidates(scope.session, query, scope.document_ids, limit=limit)
    terms = extract_named_terms(query)
    exact = []
    for chunk, item in scope.rows:
        heading = chunk.heading_path[-1].casefold() if chunk.heading_path else ""
        context = (" > ".join(chunk.heading_path) + " " + chunk.text).casefold()
        if any(term.casefold() in context for term in terms):
            exact.append((sum(term.casefold() == heading for term in terms), chunk, item))
    exact.sort(key=lambda row: (-row[0], row[2].authority_priority, row[1].ordinal, row[1].id))

    def branch(ids: list[str]) -> list[dict[str, Any]]:
        result = []
        for chunk_id in ids:
            if chunk_id not in scope.by_id:
                continue
            chunk, item = scope.by_id[chunk_id]
            result.append({"props": {"chunk_id": chunk.id, "library_item_id": item.id,
                "parent_chunk_id": chunk.parent_chunk_id, "display_text": chunk.text,
                "book_title": item.title, "heading_path_text": " > ".join(chunk.heading_path),
                "primary_heading": chunk.heading_path[-1] if chunk.heading_path else "",
                "authority_priority": item.authority_priority}})
        return result

    branches = {"vector": branch(vector_ids), "contextual_fts": branch(lexical_ids),
                "exact": branch([chunk.id for _, chunk, _ in exact[:limit]])}
    fused = reciprocal_rank_fusion(branches)
    fused.sort(key=lambda value: (
        -value["rrf_score"], scope.by_id[value["chunk_id"]][1].authority_priority, value["chunk_id"]
    ))
    ranked, reranker_fallback = get_reranker().rerank(query, fused)
    return ranked, {
        "qdrant_fallback": qdrant_fallback, "reranker_fallback": reranker_fallback,
        "vector_candidates": len(branches["vector"]),
        "fulltext_candidates": len(branches["contextual_fts"]),
        "exact_candidates": len(branches["exact"]),
    }


def hydrate_candidate(scope: RetrievalScope, candidate: dict[str, Any], *, number: int,
                      complete_evidence: bool = False) -> ShreckWorldCitation:
    chunk, item = scope.by_id[candidate["chunk_id"]]
    session = scope.session
    parent = session.get(ShreckWorldDocumentChunk, chunk.parent_chunk_id) if chunk.parent_chunk_id else None
    parent_is_valid = (
        parent is not None and parent.document_id == chunk.document_id
        and parent.library_item_id == item.id and parent.chunk_role == "parent"
    )
    evidence = parent.text if parent_is_valid else chunk.text
    if not complete_evidence:
        if len(evidence) > 12000:
            location = evidence.find(chunk.text)
            start = max(0, location - 2000) if location >= 0 else 0
            evidence = evidence[start:start + 8000] if location >= 0 else chunk.text
        evidence = evidence[:12000]
    spans = citation_provenance(session, chunk)
    return ShreckWorldCitation(
        source_id=f"source-{number}", library_item_id=item.id, title=item.title,
        page_number=chunk.page_number, chunk_id=chunk.id,
        score=round(float(candidate.get("rerank_score", candidate["rrf_score"])), 5),
        excerpt=chunk.text[:1200], authority_tier=item.authority_tier, authority_priority=item.authority_priority,
        document_id=chunk.document_id, parent_chunk_id=chunk.parent_chunk_id, content_type=chunk.content_type,
        heading_path=chunk.heading_path, page_numbers=chunk.page_numbers,
        evidence_text=evidence, parent_excerpt=evidence[:2400] if parent_is_valid else None,
        source_sha256=item.source_sha256,
        page_label=next((span["page_label"] for span in spans if span["page_number"] == chunk.page_number), None),
        content_url=f"/shreckworlds/{scope.shreckworld_id}/library-items/{item.id}/content#page={chunk.page_number}",
        provenance=spans, rectangles=_rectangles(spans),
    )


def query_shreckworld(session: Session, *, shreckworld_id: str, query: str, top_k: int,
                      library_item_id: str | None = None,
                      library_item_ids: list[str] | None = None) -> tuple[list[ShreckWorldCitation], dict]:
    started = time.perf_counter()
    scope = resolve_retrieval_scope(
        session, shreckworld_id=shreckworld_id,
        library_item_id=library_item_id, library_item_ids=library_item_ids,
    )
    trace = scope.trace
    if not scope.rows:
        trace["retrieval_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return [], trace
    ranked, fallback = search_candidates(scope, query)
    trace["qdrant_fallback"] = fallback["qdrant_fallback"]
    trace["reranker_fallback"] = fallback["reranker_fallback"]
    trace.update({key: fallback[key] for key in (
        "vector_candidates", "fulltext_candidates", "exact_candidates",
    )})
    selected, _ = select_diverse(ranked, top_k)
    citations, budget = [], 14000
    for candidate in selected:
        if budget < 200:
            break
        citation = hydrate_candidate(scope, candidate, number=len(citations) + 1)
        evidence = citation.evidence_text or ""
        excerpt = citation.excerpt[:min(1200, budget)]
        citation.excerpt = excerpt
        citation.evidence_text = evidence[:max(0, budget - 1200)]
        if citation.parent_excerpt is not None:
            citation.parent_excerpt = citation.evidence_text[:2400]
        budget -= len(excerpt) + len(citation.evidence_text or "")
        citations.append(citation)
    trace.update(returned_citations=len(citations))
    trace["retrieval_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return citations, trace
