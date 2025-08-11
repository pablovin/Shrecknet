import os
from typing import List, Dict, Optional

try:
    from chromadb.errors import ChromaError
except Exception:

    class ChromaError(Exception):
        pass


import chromadb
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain.docstore.document import Document
from langchain_huggingface import HuggingFaceEmbeddings
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timezone

from app.models.model_page import (
    Page,
    PageCharacteristicValue,
    PageKeyEvent,
    PageRelationship,
)
from app.models.model_concept import Concept
from app.models.model_characteristic import Characteristic
from app.models.model_gameworld import GameWorld
from app.models.model_agent import Agent
from app.config import settings
from more_itertools import chunked
import asyncio

from bs4 import BeautifulSoup


def strip_html(text):
    soup = BeautifulSoup(text or "", "html.parser")
    return soup.get_text(separator=" ", strip=True)


def split_html_by_headers(html, header_tags=("h1", "h2", "h3")):
    soup = BeautifulSoup(html, "html.parser")
    headers = []
    for tag in header_tags:
        headers += soup.find_all(tag)
    headers = sorted(
        headers,
        key=lambda x: x.sourceline if hasattr(x, "sourceline") and x.sourceline else 0,
    )

    chunks = []
    for i, h in enumerate(headers):
        section_texts = [h.get_text(separator=" ", strip=True)]
        for sib in h.next_siblings:
            if getattr(sib, "name", None) in header_tags:
                break
            if getattr(sib, "get_text", None):
                txt = sib.get_text(separator=" ", strip=True)
                if txt:
                    section_texts.append(txt)
            elif isinstance(sib, str):
                stripped = sib.strip()
                if stripped:
                    section_texts.append(stripped)
        chunk = "\n".join(section_texts).strip()
        if chunk:
            chunks.append(chunk)
    return chunks


class _DummyEmbeddings:
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [[float(hash(t) % 1000)] for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return [float(hash(text) % 1000)]


if os.getenv("USE_DUMMY_EMBEDDINGS", "false").lower() == "true":
    _embedding_fn = _DummyEmbeddings()
else:
    _embedding_fn = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-mpnet-base-v2",
        model_kwargs={"device": "cpu"},
    )

_text_splitter = RecursiveCharacterTextSplitter(chunk_size=512, chunk_overlap=64)

_db_path = os.getenv("VECTOR_DB_PATH", settings.vector_db_path)
chromadbURL = os.getenv("VECTOR_DB_URL", settings.vector_db_url)
chromadbPort = int(os.getenv("VECTOR_DB_PORT", settings.vector_db_port))
os.makedirs(_db_path, exist_ok=True)


def _delete_collection(name: str, _client) -> None:
    try:
        if hasattr(_client, "delete_collection"):
            try:
                _client.delete_collection(name)
            except TypeError:
                _client.delete_collection(collection_name=name)
        else:
            _client.get_collection(name).delete()
    except Exception:
        pass


def get_chroma_client():
    return chromadb.HttpClient(host=chromadbURL, port=chromadbPort)


def _get_embedding_collection(embedding_id: int) -> Chroma:
    name = f"embedding_{embedding_id}"
    client = get_chroma_client()
    return Chroma(
        client=client,
        collection_name=name,
        embedding_function=_embedding_fn,
    )


def _normalize_view_name(view: str) -> str:
    return view.lower().replace(" ", "_")


def _build_document_chunks(text: str, metadata: dict):
    docs = _text_splitter.create_documents([text], metadatas=[metadata])
    for i, doc in enumerate(docs):
        doc.metadata["chunk_index"] = i
    return docs


def _safe_add_documents(
    collection: Chroma, docs: List[Document], chroma_max_size=500
) -> None:
    client = collection._collection._client
    try:
        max_size = (
            client.get_max_batch_size()
            if hasattr(client, "get_max_batch_size")
            else getattr(client, "max_batch_size", 0)
        )
    except Exception:
        max_size = 0

    if not isinstance(max_size, int) or max_size <= 0 or max_size > chroma_max_size:
        max_size = chroma_max_size

    def _add_batch(batch: List[Document]) -> None:
        if not batch:
            return
        try:
            collection.add_documents(batch)
        except Exception as exc:
            msg = str(exc).lower()
            if (
                isinstance(exc, ChromaError)
                or "payload" in msg
                or "length" in msg
                or "413" in msg
            ) and len(batch) > 1:
                mid = len(batch) // 2
                _add_batch(batch[:mid])
                _add_batch(batch[mid:])
            else:
                raise

    for i in range(0, len(docs), max_size):
        _add_batch(docs[i : i + max_size])


async def add_page(session: AsyncSession, page_id: int, embedding_id: int):
    # Fetch the page
    result = await session.execute(select(Page).where(Page.id == page_id))
    page = result.scalar_one_or_none()
    if not page:
        return False

    # Fetch related data
    concept = await session.get(Concept, page.concept_id)
    world = await session.get(GameWorld, page.gameworld_id)

    metadata_base = {
        "page_id": page.id,
        "title": page.name,
        "concept_id": page.concept_id,
        "concept_name": concept.name if concept else None,
        "concept_description": concept.description if concept else None,
        "gameworld_id": page.gameworld_id,
        "gameworld_name": world.name if world else None,
        "system": world.system if world else None,
    }

    chunks = []

    # ---- Narrative: page.content ----
    if page.content and page.content.strip():
        metadata = dict(metadata_base)
        metadata["view"] = "narrative"
        metadata["section"] = "content"
        chunks.extend(_build_document_chunks(strip_html(page.content), metadata))

    # ---- Narrative: page.autogenerated_content ----
    if page.autogenerated_content and page.autogenerated_content.strip():
        metadata = dict(metadata_base)
        metadata["view"] = "narrative"
        metadata["section"] = "autogenerated_content"
        chunks.extend(
            _build_document_chunks(strip_html(page.autogenerated_content), metadata)
        )

    # ---- Narrative: all characteristics/values ----
    values = await session.execute(
        select(PageCharacteristicValue, Characteristic)
        .join(
            Characteristic,
            PageCharacteristicValue.characteristic_id == Characteristic.id,
        )
        .where(PageCharacteristicValue.page_id == page.id)
    )
    char_texts = []
    for val, char in values.all():
        if val.value:
            val_str = (
                ", ".join(val.value) if isinstance(val.value, list) else str(val.value)
            )
            char_texts.append(f"{char.name}: {val_str}")
    if char_texts:
        metadata = dict(metadata_base)
        metadata["view"] = "narrative"
        metadata["section"] = "characteristics"
        text = "\n".join(char_texts)
        chunks.extend(_build_document_chunks(text, metadata))

    # ---- Event: all events as one chunk ----
    event_result = await session.execute(
        select(PageKeyEvent).where(PageKeyEvent.page_id == page.id)
    )
    event_parts = [
        f"EVENT [{e.event_type}] on {e.event_date}: {e.summary or ''}"
        for e in event_result.scalars()
    ]
    if event_parts:
        metadata = dict(metadata_base)
        metadata["view"] = "event"
        metadata["section"] = "events"
        text = "\n".join(event_parts)
        chunks.extend(_build_document_chunks(text, metadata))

    # ---- Relationship: all relationships as one chunk ----
    rel_result = await session.execute(
        select(PageRelationship).where(PageRelationship.page_id == page.id)
    )
    rel_parts = [
        f"RELATIONSHIP {'->' if r.direction == 'outgoing' else '<-'} {r.target_page_id} ({r.relationship_type}): {r.description or ''}"
        for r in rel_result.scalars()
    ]
    if rel_parts:
        metadata = dict(metadata_base)
        metadata["view"] = "relationship"
        metadata["section"] = "relationships"
        text = "\n".join(rel_parts)
        chunks.extend(_build_document_chunks(text, metadata))

    # ---- Save all to Chroma ----
    collection = _get_embedding_collection(embedding_id)
    if not chunks:
        return False

    _safe_add_documents(collection, chunks)
    return True


def get_page_chunks(
    embedding_id: int,
    page_id: int,
    *,
    view: Optional[str] = None,
) -> List[str]:
    """Return ordered text chunks for a single page from the embedding.

    Parameters
    ----------
    embedding_id : int
        The embedding collection identifier.
    page_id : int
        The page ID to fetch chunks for.
    view : str, optional
        If provided, only chunks matching this view (``narrative``, ``event``,
        ``relationship``) will be returned.
    """

    collection = _get_embedding_collection(embedding_id)
    where = {"page_id": page_id}
    if view:
        where["view"] = _normalize_view_name(view)
    try:
        data = collection.get(where=where, include=["documents", "metadatas"])
    except Exception:
        return []

    docs = data.get("documents") or []
    metas = data.get("metadatas") or []
    pairs = zip(metas, docs)
    ordered = sorted(pairs, key=lambda p: p[0].get("chunk_index", 0))
    return [doc for _, doc in ordered]


def delete_page(embedding_id: int, page_id: int) -> None:
    """Remove all vectors for a given page from an embedding."""

    collection = _get_embedding_collection(embedding_id)
    try:
        collection.delete(where={"page_id": page_id})
    except Exception:
        # If the collection or vectors do not exist, ignore the error.
        pass


async def rebuild_embedding(
    session: AsyncSession, world_id: int, embedding_id: int
) -> int:
    name = f"embedding_{embedding_id}"
    collection_obj = _get_embedding_collection(embedding_id)
    _delete_collection(name, collection_obj)

    result = await session.execute(select(Page.id).where(Page.gameworld_id == world_id))
    page_ids = [row[0] for row in result.all()]

    for batch in chunked(page_ids, 20):
        await asyncio.gather(*(add_page(session, pid, embedding_id) for pid in batch))

    return len(page_ids)


def query_world(
    embedding_id: int,
    query: str,
    n_results: int = 5,
    views: Optional[List[str]] = None,
    filters: Optional[Dict] = None,
    max_chunks_per_page: Optional[int] = 15,  # Set to 0 or None for unlimited
) -> List[Dict]:
    """
    Query the world vectorstore and return up to n_results pages, each with up to max_chunks_per_page most relevant chunks.
    Each result contains:
      - document: joined string of the best chunks for the page
      - highlights: list of individual chunks with metadata
      - ...page-level metadata
    """
    collection_obj = _get_embedding_collection(embedding_id)

    # --- Build filter for Chroma/your vectorstore ---
    conditions = []
    if views:
        conditions.append({"view": {"$in": views}})
    if filters:
        for key, value in filters.items():
            if isinstance(value, list):
                conditions.append({key: {"$in": value}})
            elif isinstance(value, dict):
                conditions.append({key: value})
            else:
                conditions.append({key: {"$eq": value}})
    chroma_filter = (
        None
        if not conditions
        else conditions[0] if len(conditions) == 1 else {"$and": conditions}
    )

    # --- Query vectorstore ---
    # With a 128k LLM window and pages max 8k tokens, we can return up to 8 chunks per page by default
    # If you want to further optimize, you can dynamically adjust max_chunks_per_page based on actual chunk lengths

    retrieved = collection_obj.max_marginal_relevance_search(
        query,
        k=n_results * 12,  # Large enough for top-N pages with several chunks each
        filter=chroma_filter if chroma_filter else None,
    )

    retrieved2 = collection_obj.max_marginal_relevance_search(query, k=20)

    # # print(collection_obj.count())
    # for d in collection_obj.get(include=['metadatas', 'documents'], limit=5):
    #     print(f"Collection: {d}")

    # print (f"CRUD_VECTOR Embedding: {embedding_id}")
    # print (f"CRUD_VECTOR  chroma_filter: {chroma_filter}")
    # print (f"CRUD_VECTOR  querry: {query}")
    # print (f"CRUD_VECTOR  retrieved: {retrieved}")
    # print (f"CRUD_VECTOR  retrieved: {retrieved2}")
    # --- Group retrieved chunks by page_id, keep only the top-N (most relevant) per page ---
    pages: Dict[int, Dict] = {}
    chunk_ids_seen = set()  # Avoid duplicates

    for order, doc in enumerate(retrieved):
        meta = doc.metadata or {}
        page_id = meta.get("page_id")
        chunk_index = meta.get("chunk_index", 0)
        unique_chunk_id = (page_id, chunk_index)
        if page_id is None or unique_chunk_id in chunk_ids_seen:
            continue  # Skip if no page_id or already added this chunk for this page
        chunk_ids_seen.add(unique_chunk_id)

        entry = pages.setdefault(
            page_id,
            {
                "chunks": [],
                "metadata": {
                    k: v for k, v in meta.items() if k not in ("chunk_index",)
                },
            },
        )
        entry["chunks"].append(
            {
                "retrieval_order": order,
                "chunk_index": chunk_index,
                "content": doc.page_content,
                "metadata": meta,
                "score": getattr(doc, "score", None),  # If available
            }
        )

    # --- Prepare results: up to n_results pages, each with up to max_chunks_per_page highlight chunks ---
    results: List[Dict] = []

    for page in pages.values():
        # Sort by retrieval_order (relevance) and keep only the best N (unless max_chunks_per_page=0/None)
        sorted_by_relevance = sorted(page["chunks"], key=lambda x: x["retrieval_order"])
        if max_chunks_per_page and max_chunks_per_page > 0:
            sorted_by_relevance = sorted_by_relevance[:max_chunks_per_page]
        # Sort those by chunk_index (in-page order) for document aggregation
        sorted_by_chunk = sorted(sorted_by_relevance, key=lambda x: x["chunk_index"])

        document = " ".join(c["content"] for c in sorted_by_chunk)
        highlights = [
            {
                "chunk_index": c["chunk_index"],
                "content": c["content"],
                "metadata": c["metadata"],
                "score": c.get("score"),
            }
            for c in sorted_by_relevance
        ]
        result = {"document": document, "highlights": highlights, **page["metadata"]}
        results.append(result)

    # Optionally sort results by most relevant chunk (lowest retrieval_order across all chunks)
    results = sorted(
        results,
        key=lambda r: (
            r["highlights"][0]["score"]
            if r["highlights"] and r["highlights"][0].get("score") is not None
            else 0
        ),
        reverse=True,
    )

    return results[:n_results]


def query_embedding(*args, **kwargs):
    """Deprecated alias for ``query_world``."""
    return query_world(*args, **kwargs)


# def query_world(world_id: int, query: str, n_results: int = 5) -> List[Dict]:
#     collection = _get_collection(world_id)
#     retrieved = collection.max_marginal_relevance_search(query, k=n_results * 4)

#     pages: Dict[int, Dict] = {}
#     for doc in retrieved:
#         meta = doc.metadata or {}
#         page_id = meta.get("page_id")
#         if page_id is None:
#             continue
#         entry = pages.setdefault(
#             page_id,
#             {"document_parts": [], "metadata": {k: v for k, v in meta.items() if k != "chunk_index"}},
#         )
#         entry["document_parts"].append((meta.get("chunk_index", 0), doc.page_content))

#     results: List[Dict] = []
#     for page in pages.values():
#         parts = sorted(page["document_parts"], key=lambda x: x[0])
#         full_doc = " ".join(p[1] for p in parts)
#         results.append({"document": full_doc, **page["metadata"]})

#     return results[:n_results]
