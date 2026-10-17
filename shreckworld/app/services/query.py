"""Scoped E5/FTS/exact retrieval with shared Librarian fusion and parent evidence.

Reader book/selection requests and action resolution use this same entry point.
Only active, fingerprint-compatible structured generations are searched; original
PDF reading remains independent of vector readiness.
"""
from __future__ import annotations

import json
import time

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


def query_shreckworld(session: Session, *, shreckworld_id: str, query: str, top_k: int,
                      library_item_id: str | None = None,
                      library_item_ids: list[str] | None = None) -> tuple[list[ShreckWorldCitation], dict]:
    if library_item_id is not None and library_item_ids is not None:
        raise ValueError("Specify either library_item_id or library_item_ids, not both")
    started = time.perf_counter()
    embedder = get_embedder()
    health = embedder.ensure_ready()
    fingerprint = str(health["fingerprint"])
    documents = active_documents(session, shreckworld_id, fingerprint, library_item_id, library_item_ids)
    document_ids = [document.id for document in documents]
    rows = child_rows(session, document_ids)
    by_id = {chunk.id: (chunk, item) for chunk, item in rows}
    eligible_items = {document.library_item_id for document in documents}
    scope = select(ShreckWorldLibraryItem.id).where(ShreckWorldLibraryItem.shreckworld_id == shreckworld_id)
    if library_item_id is not None:
        scope = scope.where(ShreckWorldLibraryItem.id == library_item_id)
    elif library_item_ids is not None:
        scope = scope.where(ShreckWorldLibraryItem.id.in_(library_item_ids))
    scoped_items = set(session.scalars(scope))
    ready_items = set(session.scalars(
        select(ShreckWorldLibraryItem.id).where(
            ShreckWorldLibraryItem.id.in_(scoped_items),
            ShreckWorldLibraryItem.embedding_status == "ready",
        )
    )) if scoped_items else set()
    searched_items = {item.id for _, item in rows}
    trace = {"embedded_chunks_considered": len(rows), "returned_citations": 0, "web_search": "not_implemented",
             "pipeline": "e5_qdrant_contextual_fts_exact_rrf_cross_encoder_parent_v2",
             "embedding_backend": "sentence_transformers", "embedding_model": embedder.settings.embedding_model_id,
             "embedding_fingerprint": fingerprint, "sources_requiring_reindex": len(ready_items - eligible_items),
             "sources_without_ready_index": len(scoped_items - ready_items),
             "requested_sources": len(scoped_items),
             "sources_searched": len(searched_items),
             "qdrant_fallback": False, "reranker_fallback": False}
    if not rows:
        trace["retrieval_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return [], trace
    vector = embedder.embed(query, query=True)
    try:
        vector_ids = search_chunks(world_id=shreckworld_id, document_ids=document_ids, fingerprint=fingerprint, vector=vector)
    except Exception:
        trace["qdrant_fallback"] = True
        vector_ids = [chunk.id for chunk, _ in sorted(rows, key=lambda row: (
            -cosine_similarity(vector, json.loads(row[0].embedding_json)), row[1].authority_priority, row[0].id))[:40]]
    lexical_ids = lexical_candidates(session, query, document_ids)
    terms = extract_named_terms(query)
    exact = []
    for chunk, item in rows:
        heading = chunk.heading_path[-1].casefold() if chunk.heading_path else ""
        context = (" > ".join(chunk.heading_path) + " " + chunk.text).casefold()
        if any(term.casefold() in context for term in terms):
            exact.append((sum(term.casefold() == heading for term in terms), chunk, item))
    exact.sort(key=lambda row: (-row[0], row[2].authority_priority, row[1].ordinal, row[1].id))

    def branch(ids: list[str]) -> list[dict]:
        result = []
        for chunk_id in ids:
            if chunk_id not in by_id:
                continue  # SQL scope/activation wins over external payloads.
            chunk, item = by_id[chunk_id]
            result.append({"props": {"chunk_id": chunk.id, "library_item_id": item.id,
                "parent_chunk_id": chunk.parent_chunk_id, "display_text": chunk.text,
                "book_title": item.title, "heading_path_text": " > ".join(chunk.heading_path),
                "primary_heading": chunk.heading_path[-1] if chunk.heading_path else "",
                "authority_priority": item.authority_priority}})
        return result

    branches = {"vector": branch(vector_ids), "contextual_fts": branch(lexical_ids),
                "exact": branch([chunk.id for _, chunk, _ in exact[:40]])}
    fused = reciprocal_rank_fusion(branches)
    fused.sort(key=lambda value: (-value["rrf_score"], by_id[value["chunk_id"]][1].authority_priority, value["chunk_id"]))
    ranked, trace["reranker_fallback"] = get_reranker().rerank(query, fused)
    selected, _ = select_diverse(ranked, top_k)
    citations, budget = [], 14000
    for candidate in selected:
        if budget < 200:
            break
        chunk, item = by_id[candidate["chunk_id"]]
        parent = session.get(ShreckWorldDocumentChunk, chunk.parent_chunk_id) if chunk.parent_chunk_id else None
        evidence = parent.text if parent is not None and parent.document_id == chunk.document_id and parent.library_item_id == item.id and parent.chunk_role == "parent" else chunk.text
        if len(evidence) > 12000:
            location = evidence.find(chunk.text)
            start = max(0, location - 2000) if location >= 0 else 0
            evidence = evidence[start:start + 8000] if location >= 0 else chunk.text
        evidence = evidence[:max(0, budget - 1200)]
        excerpt = chunk.text[:min(1200, budget)]
        budget -= len(excerpt) + len(evidence)
        spans = citation_provenance(session, chunk)
        number = len(citations) + 1
        citations.append(ShreckWorldCitation(
            source_id=f"source-{number}", library_item_id=item.id, title=item.title,
            page_number=chunk.page_number, chunk_id=chunk.id,
            score=round(float(candidate.get("rerank_score", candidate["rrf_score"])), 5),
            excerpt=excerpt, authority_tier=item.authority_tier, authority_priority=item.authority_priority,
            document_id=chunk.document_id, parent_chunk_id=chunk.parent_chunk_id, content_type=chunk.content_type,
            heading_path=chunk.heading_path, page_numbers=chunk.page_numbers,
            evidence_text=evidence, parent_excerpt=evidence[:2400] if parent else None,
            source_sha256=item.source_sha256,
            page_label=next((span["page_label"] for span in spans if span["page_number"] == chunk.page_number), None),
            content_url=f"/shreckworlds/{shreckworld_id}/library-items/{item.id}/content#page={chunk.page_number}",
            provenance=spans, rectangles=_rectangles(spans),
        ))
    trace.update(returned_citations=len(citations), vector_candidates=len(branches["vector"]),
                 fulltext_candidates=len(branches["contextual_fts"]), exact_candidates=len(branches["exact"]))
    trace["retrieval_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return citations, trace
