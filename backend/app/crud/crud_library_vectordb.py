import os
import os
from datetime import datetime, timezone
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from langchain.docstore.document import Document
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.crud.crud_library_item import get_item
from app.models.model_library_item import LibraryItem
from app.crud.crud_specialist_vectordb import (
    get_chroma_client,
    _delete_collection,
    _embedding_fn,
    chunk_pages_with_word_overlap,
    _extract_pdf_by_page,
    _safe_add_documents,
)

_text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=300,
    chunk_overlap=50,
    length_function=lambda txt: len(txt.split()),
)


def _get_collection(item_id: int) -> Chroma:
    name = f"library_{item_id}"
    client = get_chroma_client()
    return Chroma(
        client=client,
        collection_name=name,
        embedding_function=_embedding_fn,
    )


def _load_item(item: LibraryItem):
    if item.path and os.path.isfile(item.path):
        if item.path.lower().endswith(".pdf"):
            try:
                return _extract_pdf_by_page(item.path)
            except Exception:
                return ""
        try:
            with open(item.path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return ""
    return ""


async def rebuild_item_with_progress(
    session: AsyncSession, item_id: int, progress_cb=None
) -> int:
    item = await get_item(session, item_id)
    if not item:
        return 0
    text = _load_item(item)
    if not text:
        return 0

    if isinstance(text, list):
        chunks = chunk_pages_with_word_overlap(text, overlap_words=50)
    else:
        chunks = [text.strip()]

    collection = _get_collection(item_id)
    _delete_collection(f"library_{item_id}", collection)

    docs: List[Document] = []
    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        docs.append(Document(page_content=chunk, metadata={"chunk_index": idx}))
        if progress_cb:
            progress_cb(idx, total)

    if docs:
        _safe_add_documents(collection, docs)

    item.vector_db_update_date = datetime.now(timezone.utc)
    await session.commit()
    return len(docs)
