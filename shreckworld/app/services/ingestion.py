from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from PyPDF2 import PdfReader
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import ShreckWorldDocumentChunk, ShreckWorldEmbeddingJob, ShreckWorldLibraryItem
from app.services.embedding import get_embedder
from app.services.retrieval_index import upsert_item_chunks


def _chunks(text: str, size: int = 350) -> list[str]:
    words = text.split()
    return [" ".join(words[offset:offset + size]) for offset in range(0, len(words), size) if words[offset:offset + size]]


def _extract_pages(path: Path) -> list[tuple[int, str]]:
    """Normalize Phase-1 source formats before structural Docling refinement."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return [(number, page.extract_text() or "") for number, page in enumerate(PdfReader(str(path)).pages, 1)]
    if suffix in {".md", ".txt"}:
        return [(1, path.read_text(encoding="utf-8", errors="replace"))]
    if suffix == ".docx":
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
        text = re.sub(r"<[^>]+>", " ", xml)
        return [(1, re.sub(r"\s+", " ", text))]
    raise ValueError("unsupported source type")


def embed_library_item(session: Session, *, library_item_id: str, job_id: str) -> None:
    job = session.get(ShreckWorldEmbeddingJob, job_id)
    item = session.get(ShreckWorldLibraryItem, library_item_id)
    if job is None or item is None:
        return
    job.status, job.progress, item.embedding_status, item.embedding_error = "running", 1, "embedding", None
    item.index_status, item.index_error = "parsing", None
    session.commit()
    try:
        path = Path(item.storage_path or item.pdf_path)
        page_chunks: list[tuple[int, str]] = []
        for page_number, page_text in _extract_pages(path):
            page_chunks.extend((page_number, chunk) for chunk in _chunks(page_text))
        if not page_chunks:
            raise ValueError("The PDF has no extractable text")

        session.execute(delete(ShreckWorldDocumentChunk).where(ShreckWorldDocumentChunk.library_item_id == item.id))
        embedder = get_embedder()
        item.index_status = "embedding"
        created_chunks: list[ShreckWorldDocumentChunk] = []
        for ordinal, (page_number, text) in enumerate(page_chunks):
            chunk = ShreckWorldDocumentChunk(
                library_item_id=item.id,
                page_number=page_number,
                ordinal=ordinal,
                text=text,
                embedding_json=json.dumps(embedder.embed(text)),
            )
            created_chunks.append(chunk)
            session.add(chunk)
            job.progress = min(99, max(2, int((ordinal + 1) / len(page_chunks) * 100)))
        session.flush()
        upsert_item_chunks(item, created_chunks)
        item.embedding_status, item.embedded_at, item.index_status = "ready", datetime.now(timezone.utc), "ready"
        job.status, job.progress, job.detail, job.completed_at = "completed", 100, f"Embedded {len(page_chunks)} chunks", datetime.now(timezone.utc)
        session.commit()
    except Exception as exc:
        item.embedding_status, item.embedding_error = "failed", str(exc)[:2000]
        item.index_status, item.index_error = "failed", item.embedding_error
        job.status, job.detail, job.completed_at = "failed", item.embedding_error, datetime.now(timezone.utc)
        session.commit()
