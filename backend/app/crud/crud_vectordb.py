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

from app.models.model_page import Page, PageCharacteristicValue, PageKeyEvent, PageRelationship
from app.models.model_concept import Concept
from app.models.model_characteristic import Characteristic
from app.models.model_gameworld import GameWorld
from app.models.model_agent import Agent
from app.config import settings

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

def _get_collection(world_id: int, collection: str | None = None):
    name = collection or f"world_{world_id}"
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

def _safe_add_documents(collection: Chroma, docs: List[Document]) -> None:
    client = collection._collection._client
    try:
        max_size = client.get_max_batch_size() if hasattr(client, "get_max_batch_size") else getattr(client, "max_batch_size", 0)
    except Exception:
        max_size = 0

    if not isinstance(max_size, int) or max_size <= 0 or max_size > 100:
        max_size = 100

    def _add_batch(batch: List[Document]) -> None:
        if not batch:
            return
        try:
            collection.add_documents(batch)
        except Exception as exc:
            msg = str(exc).lower()
            if (isinstance(exc, ChromaError) or "payload" in msg or "length" in msg or "413" in msg) and len(batch) > 1:
                mid = len(batch) // 2
                _add_batch(batch[:mid])
                _add_batch(batch[mid:])
            else:
                raise

    for i in range(0, len(docs), max_size):
        _add_batch(docs[i: i + max_size])

async def add_page(session: AsyncSession, page_id: int, collection: str | None = None):
    result = await session.execute(select(Page).where(Page.id == page_id))
    page = result.scalar_one_or_none()
    if not page:
        return False

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

    # Narrative View
    values = await session.execute(
        select(PageCharacteristicValue, Characteristic)
        .join(Characteristic, PageCharacteristicValue.characteristic_id == Characteristic.id)
        .where(PageCharacteristicValue.page_id == page.id)
    )
    char_texts = []
    for val, char in values.all():
        if val.value:
            val_str = ", ".join(val.value) if isinstance(val.value, list) else str(val.value)
            char_texts.append(f"{char.name}: {val_str}")

    narrative_parts = [
        f"TITLE: {page.name}",
        f"CONTENT:\n{page.content or ''}",
        f"AUTOGENERATED:\n{page.autogenerated_content or ''}",
        f"CHARACTERISTICS:\n" + "\n".join(char_texts),
        f"CONCEPT:\n{concept.name}:{concept.description if concept else ''}"
    ]
    narrative_text = "\n\n".join(narrative_parts)

    # Events View
    key_events = await session.execute(select(PageKeyEvent).where(PageKeyEvent.page_id == page.id))
    event_parts = [f"EVENT [{e.event_type}] on {e.event_date}: {e.summary or ''}" for e in key_events]
    events_text = "\n".join(event_parts)

    # Relationships View
    relations = await session.execute(select(PageRelationship).where(PageRelationship.page_id == page.id))
    rel_parts = [
        f"RELATIONSHIP {'->' if r.direction == 'outgoing' else '<-'} {r.target_page_id} ({r.relationship_type}): {r.description or ''}"
        for r in relations
    ]
    relationships_text = "\n".join(rel_parts)

    collection = _get_collection(page.gameworld_id, collection)
    all_chunks = []

    for view_name, text in [
        ("narrative", narrative_text),
        ("event", events_text),
        ("relationship", relationships_text),
    ]:
        if not text.strip():
            continue
        metadata = dict(metadata_base)
        metadata["view"] = _normalize_view_name(view_name)
        view_chunks = _build_document_chunks(text, metadata)
        all_chunks.extend(view_chunks)

    if not all_chunks:
        return False

    _safe_add_documents(collection, all_chunks)
    return True

async def rebuild_world(session: AsyncSession, world_id: int, collection: str | None = None):
    name = collection or f"world_{world_id}"
    collection_obj = _get_collection(world_id, collection)
    _delete_collection(name, collection_obj)

    result = await session.execute(select(Page.id).where(Page.gameworld_id == world_id))
    page_ids = [row[0] for row in result.all()]
    for pid in page_ids:
        await add_page(session, pid, collection)

    agent_result = await session.execute(select(Agent).where(Agent.world_id == world_id))
    agents = agent_result.scalars().all()
    now = datetime.now(timezone.utc)
    for agent in agents:
        agent.vector_db_update_date = now
    await session.commit()

    return len(page_ids)


def query_world(world_id: int, query: str, n_results: int = 5, views: Optional[List[str]] = None, filters: Optional[Dict] = None, collection: str | None = None) -> List[Dict]:
    collection_obj = _get_collection(world_id, collection)
    
    # Build valid Chroma filter format using $and for multiple conditions
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

    if not conditions:
        chroma_filter = None
    elif len(conditions) == 1:
        chroma_filter = conditions[0]
    else:
        chroma_filter = {"$and": conditions}

    print(f"FILTERS USED IN QUERY: {chroma_filter}")

    # Run query
    retrieved = collection_obj.max_marginal_relevance_search(
        query,
        k=n_results * 4,
        filter=chroma_filter if chroma_filter else None
    )

    pages: Dict[int, Dict] = {}
    for doc in retrieved:
        meta = doc.metadata or {}
        page_id = meta.get("page_id")
        if page_id is None:
            continue
        entry = pages.setdefault(
            page_id,
            {"document_parts": [], "metadata": {k: v for k, v in meta.items() if k != "chunk_index"}},
        )
        entry["document_parts"].append((meta.get("chunk_index", 0), doc.page_content))

    results: List[Dict] = []
    for page in pages.values():
        parts = sorted(page["document_parts"], key=lambda x: x[0])
        full_doc = " ".join(p[1] for p in parts)
        results.append({"document": full_doc, **page["metadata"]})

    return results[:n_results]

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
