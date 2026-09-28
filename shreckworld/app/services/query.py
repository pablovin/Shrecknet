from __future__ import annotations

import json
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ShreckWorldDocumentChunk, ShreckWorldLibraryItem
from app.schemas.shreckworld import ShreckWorldCitation
from app.services.embedding import cosine_similarity, get_embedder


def query_shreckworld(session: Session, *, shreckworld_id: str, query: str, top_k: int) -> tuple[list[ShreckWorldCitation], dict[str, int | str]]:
    rows = session.execute(
        select(ShreckWorldDocumentChunk, ShreckWorldLibraryItem)
        .join(ShreckWorldLibraryItem)
        .where(
            ShreckWorldLibraryItem.shreckworld_id == shreckworld_id,
            ShreckWorldLibraryItem.embedding_status == "ready",
        )
    ).all()
    query_vector = get_embedder().embed(query, query=True)
    query_words = {word.lower() for word in query.split() if len(word) > 2}
    ranked = []
    for chunk, item in rows:
        vector_score = cosine_similarity(query_vector, json.loads(chunk.embedding_json))
        lexical = sum(word in chunk.text.lower() for word in query_words) / max(1, len(query_words))
        # Authority is a deterministic tie-breaker, never a substitute for relevance.
        score = vector_score + (0.15 * lexical) + (1 / (10000 + item.authority_priority))
        ranked.append((score, chunk, item))
    ranked.sort(key=lambda row: (-row[0], row[2].authority_priority, row[1].id))
    citations = [
        ShreckWorldCitation(
            source_id=f"source-{number}", library_item_id=item.id, title=item.title,
            page_number=chunk.page_number, chunk_id=chunk.id, score=round(score, 5),
            excerpt=chunk.text[:1200], authority_tier=item.authority_tier,
            authority_priority=item.authority_priority,
        )
        for number, (score, chunk, item) in enumerate(ranked[:top_k], 1)
    ]
    return citations, {"embedded_chunks_considered": len(rows), "returned_citations": len(citations), "web_search": "not_implemented"}
