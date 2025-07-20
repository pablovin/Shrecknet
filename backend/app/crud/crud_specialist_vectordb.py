import os
from typing import List, Union

try:
    from chromadb.errors import ChromaError
except Exception:  # pragma: no cover - fallback when chromadb not installed
    class ChromaError(Exception):
        pass
from datetime import datetime, timezone

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain.docstore.document import Document
from langchain_chroma import Chroma
import requests

from pdfminer.high_level import extract_text as pdf_extract_text
from pdfminer.high_level import extract_pages as pdf_extract_pages
from pdfminer.layout import LTTextContainer

from app.models.model_specialist_source import SpecialistSource
from app.models.model_agent import Agent
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.crud.crud_vectordb import (
    get_chroma_client,
    _delete_collection,
    _embedding_fn,
)


_text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=300,
    chunk_overlap=50,
    length_function=lambda txt: len(txt.split()),
)

def chunk_pages_with_word_overlap(pages: List[str], overlap_words: int = 50) -> List[str]:
    chunks = []

    for i, page in enumerate(pages):
        page = page.strip()
        if i == 0:
            chunks.append(page)
        else:
            prev_words = pages[i - 1].strip().split()
            tail_words = prev_words[-overlap_words:] if len(prev_words) >= overlap_words else prev_words
            tail_text = " ".join(tail_words)
            combined = f"{tail_text} {page}"
            chunks.append(combined.strip())

    return chunks

def _safe_add_documents(collection: Chroma, docs: List[Document]) -> None:
    """Add documents in batches, splitting further on 413 errors."""

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


def _safe_add_records(
    collection: Chroma,
    ids: list[str] | None,
    embeddings: list[list[float]],
    documents: list[str],
    metadatas: list[dict],
) -> None:
    """Safely add raw records with optional IDs, handling 413 errors."""

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

    def _add_batch(start: int, end: int) -> None:
        if end <= start:
            return
        try:
            collection._collection.add(
                ids=ids[start:end] if ids else None,
                embeddings=embeddings[start:end],
                documents=documents[start:end],
                metadatas=metadatas[start:end],
            )
        except Exception as exc:
            msg = str(exc).lower()
            if (
                isinstance(exc, ChromaError)
                or "payload" in msg
                or "length" in msg
                or "413" in msg
            ) and end - start > 1:
                mid = start + (end - start) // 2
                _add_batch(start, mid)
                _add_batch(mid, end)
            else:
                raise

    for i in range(0, len(documents), max_size):
        _add_batch(i, min(i + max_size, len(documents)))


def _get_collection(agent_id: int) -> Chroma:
    name = f"specialist_{agent_id}"
    client = get_chroma_client()
    return Chroma(
        client=client,
        collection_name=name,
        embedding_function=_embedding_fn,
    )

def _extract_pdf_by_page(pdf_path: str) -> List[str]:
    pages_text = []

    for page_layout in pdf_extract_pages(pdf_path):
        page_text = ""
        for element in page_layout:
            if isinstance(element, LTTextContainer):
                page_text += element.get_text()
        pages_text.append(page_text.strip())

    return pages_text

def _load_source(src: SpecialistSource) -> str | list[str]:
    if src.type == "file" and src.path:
        if src.path.lower().endswith(".pdf"):
            try:
                return _extract_pdf_by_page(src.path)
            except Exception:
                return ""
        try:
            with open(src.path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""
    if src.type == "link" and src.url:
        try:
            resp = requests.get(src.url, timeout=10)
            resp.raise_for_status()
            return resp.text
        except Exception:
            return ""
    return ""


async def rebuild_agent(session: AsyncSession, agent_id: int) -> int:
    result = await session.execute(
        select(SpecialistSource).where(SpecialistSource.agent_id == agent_id)
    )
    sources = result.scalars().all()
    collection = _get_collection(agent_id)
    _delete_collection(f"specialist_{agent_id}", collection)

    docs: List[Document] = []
    for src in sources:
        text = _load_source(src)
        if not text:
            continue

        chunks = chunk_pages_with_word_overlap(text, overlap_words=50)
        for i, chunk in enumerate(chunks):
            doc = Document(
                page_content=chunk,
                metadata={"source_id": src.id, "chunk_index": i}
            )
            docs.append(doc)
    
    if docs:
        _safe_add_documents(collection, docs)

    agent = await session.get(Agent, agent_id)
    if agent:
        agent.specialist_update_date = datetime.now(timezone.utc)
        await session.commit()
    return len(docs)


def export_agent_vectordb(agent_id: int) -> dict:
    """Export the vector database for an agent as a serializable dict."""
    collection = _get_collection(agent_id)
    # `ids` are always returned by Chroma's ``get`` method and should not be
    # passed in the ``include`` list. Newer versions of Chroma raise a
    # ``ValueError`` when ``ids`` is included, which resulted in 500 errors when
    # exporting a vector database. We only request the optional fields that are
    # allowed and rely on the default behaviour to include the document IDs.
    data = collection.get(include=["documents", "metadatas", "embeddings"])

    # ``embeddings`` may contain numpy arrays which aren't JSON serialisable.
    # Convert any ndarray entries to plain Python lists so the returned
    # structure can be safely dumped to JSON by the API layer.
    raw_embeddings = data.get("embeddings", [])
    embeddings = []
    for emb in raw_embeddings:
        try:
            embeddings.append(emb.tolist())
        except AttributeError:
            embeddings.append(emb)

    return {
        "documents": data.get("documents", []),
        "metadatas": data.get("metadatas", []),
        "embeddings": embeddings,
        "ids": data.get("ids", []),
    }


async def import_agent_vectordb(session: AsyncSession, agent_id: int, data: dict) -> int:
    """Import vector DB data for an agent, replacing existing data."""
    docs = data.get("documents") or []
    metas = data.get("metadatas") or []
    embeds = data.get("embeddings") or []
    ids = data.get("ids") or None

    collection = _get_collection(agent_id)
    _delete_collection(f"specialist_{agent_id}", collection)

    if docs:
        _safe_add_records(collection, ids, embeds, docs, metas)

    agent = await session.get(Agent, agent_id)
    if agent:
        agent.specialist_update_date = datetime.now(timezone.utc)
        await session.commit()
    return len(docs)



def query_agent(agent_id: int, query: str, n_results: int = 5) -> List[dict]:
    """Query the specialist vector DB for relevant documents."""
    collection = _get_collection(agent_id)
    retrieved = collection.max_marginal_relevance_search(query, k=n_results * 4)

    sources: dict[int, dict] = {}
    for doc in retrieved:
        meta = doc.metadata or {}
        sid = meta.get("source_id")
        if sid is None:
            continue
        entry = sources.setdefault(
            sid,
            {"document_parts": [], "metadata": {k: v for k, v in meta.items() if k != "chunk_index"}},
        )
        entry["document_parts"].append((meta.get("chunk_index", 0), doc.page_content))

    results: List[dict] = []
    for src in sources.values():
        parts = sorted(src["document_parts"], key=lambda x: x[0])
        full_doc = " ".join(p[1] for p in parts)
        results.append({"document": full_doc, **src["metadata"]})

    return results[:n_results]

async def rebuild_agent_with_progress(
    session: AsyncSession,
    agent_id: int,
    progress_callback,
) -> int:
    """Rebuild agent vector DB with progress callback."""
    result = await session.execute(
        select(SpecialistSource).where(SpecialistSource.agent_id == agent_id)
    )
    sources = result.scalars().all()
    collection = _get_collection(agent_id)
    _delete_collection(f"specialist_{agent_id}", collection)

    docs: List[Document] = []
    total = len(sources)
    for idx, src in enumerate(sources, start=1):
        if progress_callback:
            progress_callback(f"reading document {src.name} {idx}/{total}")
        text: Union[str, list[str]] = _load_source(src)
        if not text:
            continue
        if progress_callback:
            progress_callback(f"creating chunks of document {src.name} {idx}/{total}")
        if isinstance(text, list):
            chunks = chunk_pages_with_word_overlap(text, overlap_words=50)
        else:
            chunks = [text.strip()]  # fallback: treat whole text as one chunk
        for i, chunk in enumerate(chunks):
            doc = Document(
                page_content=chunk,
                metadata={"source_id": src.id, "chunk_index": i}
            )
            docs.append(doc)
        
    if docs:
        if progress_callback:
            progress_callback(f"embedding documents {len(docs)}")
        _safe_add_documents(collection, docs)

    agent = await session.get(Agent, agent_id)
    if agent:
        agent.specialist_update_date = datetime.now(timezone.utc)
        await session.commit()
    return len(docs)

