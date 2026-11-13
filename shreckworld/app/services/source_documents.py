"""Read-only previews for imported documents and revision-safe Markdown editing."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

from fastapi import HTTPException
from pydantic import Field
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ShreckWorldLibraryItem
from app.schemas.sources import Contract


class DocumentRead(Contract):
    source_id: str
    revision: int
    format: str
    content: str
    truncated: bool = False
    editable: bool = False


class DocumentSave(Contract):
    expected_revision: int = Field(ge=1)
    content: str = Field(max_length=2_000_000)


def _error(status: int, message: str):
    raise HTTPException(status_code=status, detail=message)


def _path(item: ShreckWorldLibraryItem) -> Path:
    original = item.storage_path or item.pdf_path
    if not original or original == "pending":
        _error(404, "Original source file is not available")
    path = Path(original).resolve()
    media = get_settings().media_root.resolve()
    if media not in path.parents or not path.is_file():
        _error(404, "Original source file is not available")
    return path


def _docx_text(path: Path) -> tuple[str, bool]:
    """Bounded plain-document rendering, no external assets or macro execution."""
    try:
        with ZipFile(path) as document:
            info = document.getinfo("word/document.xml")
            if info.file_size > 8 * 1024 * 1024:
                _error(413, "Document is too large for the built-in preview")
            root = ElementTree.fromstring(document.read(info))
    except (KeyError, BadZipFile, ElementTree.ParseError):
        _error(422, "This DOCX could not be previewed")
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    size = 0
    for p in root.iter(ns + "p"):
        value = "".join(node.text or "" for node in p.iter(ns + "t"))
        if not value.strip():
            continue
        size += len(value.encode("utf-8")) + 2
        if size > 2_000_000:
            return "\n\n".join(paragraphs), True
        paragraphs.append(value)
    return "\n\n".join(paragraphs), False


def read_document(item: ShreckWorldLibraryItem) -> DocumentRead:
    path = _path(item)
    suffix = Path(item.original_filename or path.name).suffix.lower()
    if suffix == ".pdf":
        # PDFs are read through the existing authenticated streaming endpoint.
        return DocumentRead(source_id=item.id, revision=item.source_revision,
                            format="pdf", content="", editable=False)
    if suffix == ".docx":
        content, truncated = _docx_text(path)
        return DocumentRead(source_id=item.id, revision=item.source_revision,
                            format="docx", content=content, truncated=truncated)
    if suffix not in {".md", ".txt", ".html", ".htm"}:
        _error(415, "No built-in reader for this document type")
    limit = 2_000_000
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    truncated = len(raw) > limit
    content = raw[:limit].decode("utf-8", errors="replace")
    return DocumentRead(
        source_id=item.id, revision=item.source_revision,
        format=suffix[1:], content=content, truncated=truncated,
        editable=suffix == ".md" and not item.authored and not truncated,
    )


def save_imported_markdown(
    db: Session, item: ShreckWorldLibraryItem, payload: DocumentSave,
) -> DocumentRead:
    """Save imported .md without modifying preserved Book Map generations.

    Authored sources still use their structured organization editor, never
    this raw-file endpoint, so there cannot be two competing edit authorities.
    """
    if item.authored:
        _error(422, "Edit authored Markdown through its structured source editor")
    if Path(item.original_filename or "").suffix.lower() != ".md":
        _error(415, "Only Markdown documents are editable")
    if item.source_revision != payload.expected_revision:
        _error(409, "Source changed; reload before saving")
    current = _path(item)
    if len(payload.content.encode("utf-8")) > 2_000_000:
        _error(413, "Markdown document exceeds the 2 MB editor limit")
    destination = current.parent / ("document-" + uuid4().hex + ".md")
    try:
        with destination.open("xb") as output:
            output.write(payload.content.encode("utf-8"))
            output.flush()
            import os
            os.fsync(output.fileno())
        result = db.execute(update(ShreckWorldLibraryItem).where(
            ShreckWorldLibraryItem.id == item.id,
            ShreckWorldLibraryItem.source_revision == payload.expected_revision,
        ).values(
            source_revision=payload.expected_revision + 1,
            storage_path=str(destination), pdf_path=str(destination),
            source_sha256=sha256(payload.content.encode("utf-8")).hexdigest(),
            file_size=len(payload.content.encode("utf-8")), index_status="stale",
        ))
        if result.rowcount != 1:
            db.rollback()
            _error(409, "Source changed while saving")
        db.commit()
    except Exception:
        db.rollback()
        destination.unlink(missing_ok=True)
        raise
    db.refresh(item)
    return DocumentRead(
        source_id=item.id, revision=item.source_revision,
        format="md", content=payload.content, editable=True,
    )
