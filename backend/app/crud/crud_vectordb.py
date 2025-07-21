import os
from typing import List, Dict

try:
    from chromadb.errors import ChromaError
except Exception:  # pragma: no cover - fallback
    class ChromaError(Exception):
        pass

import chromadb
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain.docstore.document import Document
from langchain_huggingface import HuggingFaceEmbeddings


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


_text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=300,
    chunk_overlap=50,
    length_function=lambda txt: len(txt.split()),
)


def _safe_add_documents(collection: Chroma, docs: List[Document]) -> None:
    """Safely add documents, splitting on 413 errors."""

    client = collection._collection._client
    try:
        max_size = (
            client.get_max_batch_size()
            if hasattr(client, "get_max_batch_size")
            else getattr(client, "max_batch_size", 0)
        )
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

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timezone

from app.models.model_page import Page, PageCharacteristicValue
from app.models.model_concept import Concept
from app.models.model_characteristic import Characteristic
from app.models.model_agent import Agent
from app.config import settings


# Persistent client storing collections under ./vector_db. ``chromadb``
# expects a filesystem path. ``settings.vector_db_path`` already contains
# the default path, but the location can be overridden with the
# ``VECTOR_DB_PATH`` environment variable.  The previous implementation
# attempted to read an environment variable whose name was the path
# itself which resulted in ``None`` being passed to ``PersistentClient``
# and therefore created an in-memory database.  Use the provided setting
# directly and fall back to it if the environment variable is not set.
_db_path = os.getenv("VECTOR_DB_PATH", settings.vector_db_path)

chromadbURL = os.getenv("VECTOR_DB_URL", settings.vector_db_url)
chromadbPort = int(os.getenv("VECTOR_DB_PORT", settings.vector_db_port))

# Ensure the directory exists so ``chromadb`` can persist collections
os.makedirs(_db_path, exist_ok=True)

# _client = chromadb.PersistentClient(path=_db_path)


def _delete_collection(name: str, _client) -> None:
    """Delete a Chroma collection by name if it exists."""
    
    try:
        
        if hasattr(_client, "delete_collection"):
        
            try:
        
                _client.delete_collection(name)  # type: ignore[arg-type]
        
            except TypeError:
                # some versions require a keyword argument
        
                _client.delete_collection(collection_name=name)  # type: ignore[arg-type]
        
        else:
        
            _client.get_collection(name).delete()
        
    except Exception:
        # Deleting should not fail the rebuild process

        
        pass


def get_chroma_client():
    """Return a Chroma client without requiring a running server."""

    return chromadb.HttpClient(host=chromadbURL, port=chromadbPort)
    
    # _db_path = os.getenv("VECTOR_DB_PATH", settings.vector_db_path)
    # print (f"READING CHROMA CLIENT FROM HERE: " + _db_path)
    # os.makedirs(_db_path, exist_ok=True)
    # return chromadb.PersistentClient(path=_db_path)

def _get_collection(world_id: int):
    name = f"world_{world_id}"
    client = get_chroma_client()   # <-- Created when function runs, in child process
    return Chroma(
        client=client,
        collection_name=name,
        embedding_function=_embedding_fn,
    )


# def _get_collection(world_id: int):
#     name = f"world_{world_id}"
#     return Chroma(
#         client=_client,
#         collection_name=name,
#         embedding_function=_embedding_fn,
#     )


async def add_page(session: AsyncSession, page_id: int):
    result = await session.execute(
        select(Page).where(Page.id == page_id)
    )
    page = result.scalar_one_or_none()    

    if not page:
        return None

    # Load related concept
    concept = await session.get(Concept, page.concept_id)    

    # Load characteristic values
    values = await session.execute(
        select(PageCharacteristicValue, Characteristic)
        .join(Characteristic, PageCharacteristicValue.characteristic_id == Characteristic.id)
        .where(PageCharacteristicValue.page_id == page.id)
    )    
    char_texts = []
    for val, char in values.all():
        if val.value is None:
            continue
        val_str = ", ".join(val.value) if isinstance(val.value, list) else str(val.value)
        char_texts.append(f"{char.name}: {val_str}")

    
    doc_parts = [page.content or ""]
    doc_parts.append(page.autogenerated_content or "")
    
    
    if concept:
        doc_parts.append(concept.description or "")
    if char_texts:
        doc_parts.append("\n".join(char_texts))
    document = "\n".join(doc_parts)

    # print (f" --- FINAL DOCUMENT: {document}")
    metadata = {
        "page_id": page.id,
        "gameworld_id": page.gameworld_id,
        "concept_id": page.concept_id,
        "title": page.name,
    }


    collection = _get_collection(page.gameworld_id)

    docs = _text_splitter.create_documents([document], metadatas=[metadata])
    for i, doc in enumerate(docs):
        doc.metadata["chunk_index"] = i

    # ``chromadb`` can fail on very large batches, so split the data into
    # reasonable chunks based on the client's advertised limit.
    _safe_add_documents(collection, docs)
   

    # print (f" --- API VECTORDB - Page added to the chromadb!")
    return True


async def rebuild_world(session: AsyncSession, world_id: int):
    name = f"world_{world_id}"

    collection = _get_collection(world_id)

    print (f" --- INSIDE REBUILD WORKD: {world_id} --- ")

    _delete_collection(name, collection)
    print (f" --- COLLECTION DELETED!")

       # collection = _get_collection(world_id)

    # print (f" - API VECTORDB - Collection: {collection}")

    result = await session.execute(select(Page.id).where(Page.gameworld_id == world_id))

    page_ids = [row[0] for row in result.all()]
    for pid in page_ids:

        await add_page(session, pid)

       # update agents in this world with current time
    agent_result = await session.execute(select(Agent).where(Agent.world_id == world_id))
    agents = agent_result.scalars().all()
    now = datetime.now(timezone.utc)
    for agent in agents:
        agent.vector_db_update_date = now
    await session.commit()

    return len(page_ids)


def query_world(world_id: int, query: str, n_results: int = 5) -> List[Dict]:
    """Query the vector DB for documents related to the given query."""
    collection = _get_collection(world_id)
    retrieved = collection.max_marginal_relevance_search(query, k=n_results * 4)

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
