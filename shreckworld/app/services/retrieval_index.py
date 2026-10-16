"""Versioned Qdrant projection; SQL decides which document generations are active."""
from __future__ import annotations

import json
import math
from functools import lru_cache

from app.core.config import get_settings
from app.models import ShreckWorldDocument, ShreckWorldDocumentChunk, ShreckWorldLibraryItem


@lru_cache(maxsize=1)
def get_client():
    from qdrant_client import QdrantClient
    return QdrantClient(url=get_settings().qdrant_url, timeout=30)


def upsert_item_chunks(item: ShreckWorldLibraryItem, chunks: list[ShreckWorldDocumentChunk], *, document: ShreckWorldDocument) -> None:
    from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PayloadSchemaType, PointStruct, VectorParams
    settings = get_settings()
    client = get_client()
    if not client.collection_exists(settings.qdrant_collection):
        client.create_collection(settings.qdrant_collection, vectors_config=VectorParams(size=document.embedding_dimension, distance=Distance.COSINE))
        for field in ("shreckworld_id", "document_id", "embedding_fingerprint"):
            client.create_payload_index(settings.qdrant_collection, field, field_schema=PayloadSchemaType.KEYWORD, wait=True)
    params = client.get_collection(settings.qdrant_collection).config.params.vectors
    if getattr(params, "size", None) != document.embedding_dimension or getattr(params, "distance", None) != Distance.COSINE:
        raise ValueError("Qdrant collection configuration does not match the semantic encoder")
    points = []
    for chunk in chunks:
        vector = json.loads(chunk.embedding_json)
        if chunk.chunk_role != "child" or len(vector) != document.embedding_dimension or not all(math.isfinite(value) for value in vector):
            raise ValueError("Only validated semantic child vectors can be projected")
        points.append(PointStruct(id=chunk.id, vector=vector, payload={
            "shreckworld_id": item.shreckworld_id, "library_item_id": item.id,
            "document_id": document.id, "embedding_fingerprint": document.embedding_fingerprint,
            "page_number": chunk.page_number, "ordinal": chunk.ordinal,
            "authority_tier": item.authority_tier, "authority_priority": item.authority_priority,
        }))
    # Bounded requests avoid one huge JSON body for an illustrated rulebook.
    for offset in range(0, len(points), 256):
        client.upsert(collection_name=settings.qdrant_collection, points=points[offset:offset + 256], wait=True)
    count = client.count(settings.qdrant_collection, count_filter=Filter(must=[
        FieldCondition(key="document_id", match=MatchValue(value=document.id)),
        FieldCondition(key="embedding_fingerprint", match=MatchValue(value=document.embedding_fingerprint)),
    ]), exact=True).count
    if count != len(points):
        raise ValueError("Qdrant staged vector count does not match the semantic child count")


def search_chunks(*, world_id: str, document_ids: list[str], fingerprint: str, vector: list[float], limit: int = 40) -> list[str]:
    from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue
    if not document_ids:
        return []
    rows = get_client().search(
        collection_name=get_settings().qdrant_collection, query_vector=vector, limit=limit,
        query_filter=Filter(must=[
            FieldCondition(key="shreckworld_id", match=MatchValue(value=world_id)),
            FieldCondition(key="document_id", match=MatchAny(any=document_ids)),
            FieldCondition(key="embedding_fingerprint", match=MatchValue(value=fingerprint)),
        ]), with_payload=False,
    )
    return [str(row.id) for row in rows]


def _delete(field: str, value: str) -> None:
    from qdrant_client.models import FieldCondition, Filter, MatchValue
    client, collection = get_client(), get_settings().qdrant_collection
    if client.collection_exists(collection):
        client.delete(collection_name=collection, points_selector=Filter(must=[FieldCondition(key=field, match=MatchValue(value=value))]), wait=True)


def delete_document_chunks(document_id: str) -> None:
    _delete("document_id", document_id)


def delete_item_chunks(item_id: str) -> None:
    _delete("library_item_id", item_id)
