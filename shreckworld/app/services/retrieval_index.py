"""Rebuildable Qdrant projection of canonical ShreckWorld SQLite chunks."""
from __future__ import annotations

import json

from app.core.config import get_settings
from app.models import ShreckWorldDocumentChunk, ShreckWorldLibraryItem


def upsert_item_chunks(item: ShreckWorldLibraryItem, chunks: list[ShreckWorldDocumentChunk]) -> None:
    """Bulk-project one indexed item. SQLite remains authoritative on failure."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url)
    if not client.collection_exists(settings.qdrant_collection):
        client.create_collection(settings.qdrant_collection, vectors_config=VectorParams(size=settings.embedding_dimension, distance=Distance.COSINE))
    client.upsert(
        collection_name=settings.qdrant_collection,
        points=[PointStruct(id=chunk.id, vector=json.loads(chunk.embedding_json), payload={
            "shreckworld_id": item.shreckworld_id, "library_item_id": item.id,
            "page_number": chunk.page_number, "ordinal": chunk.ordinal,
            "authority_tier": item.authority_tier, "authority_priority": item.authority_priority,
        }) for chunk in chunks],
        wait=True,
    )


def delete_item_chunks(item_id: str) -> None:
    from qdrant_client import QdrantClient
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    settings = get_settings()
    client = QdrantClient(url=settings.qdrant_url)
    if client.collection_exists(settings.qdrant_collection):
        client.delete(settings.qdrant_collection, points_selector=Filter(must=[FieldCondition(key="library_item_id", match=MatchValue(value=item_id))]), wait=True)
