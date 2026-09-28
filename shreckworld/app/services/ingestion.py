from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from PyPDF2 import PdfReader
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import ShreckWorldDocumentChunk, ShreckWorldEmbeddingJob, ShreckWorldLibraryItem
from app.services.embedding import get_embedder


def _chunks(text: str, size: int = 350) -> list[str]:
    words = text.split()
    return [" ".join(words[offset:offset + size]) for offset in range(0, len(words), size) if words[offset:offset + size]]


def embed_library_item(session: Session, *, library_item_id: str, job_id: str) -> None:
    job = session.get(ShreckWorldEmbeddingJob, job_id)
    item = session.get(ShreckWorldLibraryItem, library_item_id)
    if job is None or item is None:
        return
    job.status, job.progress, item.embedding_status, item.embedding_error = "running", 1, "embedding", None
    session.commit()
    try:
        path = Path(item.pdf_path)
        reader = PdfReader(str(path))
        page_chunks: list[tuple[int, str]] = []
        for page_number, page in enumerate(reader.pages, 1):
            page_chunks.extend((page_number, chunk) for chunk in _chunks(page.extract_text() or ""))
        if not page_chunks:
            raise ValueError("The PDF has no extractable text")

        session.execute(delete(ShreckWorldDocumentChunk).where(ShreckWorldDocumentChunk.library_item_id == item.id))
        embedder = get_embedder()
        for ordinal, (page_number, text) in enumerate(page_chunks):
            session.add(ShreckWorldDocumentChunk(
                library_item_id=item.id,
                page_number=page_number,
                ordinal=ordinal,
                text=text,
                embedding_json=json.dumps(embedder.embed(text)),
            ))
            job.progress = min(99, max(2, int((ordinal + 1) / len(page_chunks) * 100)))
        item.embedding_status, item.embedded_at = "ready", datetime.now(timezone.utc)
        job.status, job.progress, job.detail, job.completed_at = "completed", 100, f"Embedded {len(page_chunks)} chunks", datetime.now(timezone.utc)
        session.commit()
    except Exception as exc:
        item.embedding_status, item.embedding_error = "failed", str(exc)[:2000]
        job.status, job.detail, job.completed_at = "failed", item.embedding_error, datetime.now(timezone.utc)
        session.commit()
