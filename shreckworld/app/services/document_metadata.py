"""Best-effort upload autofill, independent of Librarian storage and jobs."""
from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

import fitz
from PyPDF2 import PdfReader

logger = logging.getLogger(__name__)


@dataclass
class DocumentMetadata:
    title: str | None = None
    authors: str | None = None
    description: str | None = None
    cover: bytes | None = None


def _text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None


def extract_document_metadata(raw: bytes, filename: str) -> DocumentMetadata:
    """Read embedded properties; PDF covers are bounded first-page PNG renders.

    Malformed, encrypted, or metadata-free sources remain uploadable. Optional
    XMP and cover failures must not discard already recovered properties.
    """
    result = DocumentMetadata()
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        try:
            reader = PdfReader(BytesIO(raw))
            info = reader.metadata or {}
            result.title = _text(info.get("/Title"))
            result.authors = _text(info.get("/Author"))
            result.description = _text(info.get("/Subject"))
            try:
                xmp = reader.xmp_metadata
                if xmp:
                    result.title = result.title or next(
                        (text for value in (xmp.dc_title or {}).values() if (text := _text(value))), None
                    )
                    result.authors = result.authors or _text(
                        ", ".join(text for value in (xmp.dc_creator or []) if (text := _text(value)))
                    )
                    result.description = result.description or next(
                        (text for value in (xmp.dc_description or {}).values() if (text := _text(value))), None
                    )
            except Exception:
                logger.debug("Could not extract PDF XMP properties", exc_info=True)
        except Exception:
            logger.debug("Could not extract PDF properties", exc_info=True)
        try:
            with fitz.open(stream=raw, filetype="pdf") as document:
                if not document.needs_pass and len(document):
                    page = document[0]
                    scale = min(1.5, 1200 / max(page.rect.width, page.rect.height, 1))
                    result.cover = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")
        except Exception:
            logger.debug("Could not render PDF cover", exc_info=True)
    elif suffix == ".docx":
        try:
            with zipfile.ZipFile(BytesIO(raw)) as archive:
                # Bound decompression of the small properties part independently
                # of the upload's compressed size limit.
                part = archive.getinfo("docProps/core.xml")
                if part.file_size > 1024 * 1024:
                    return result
                root = ElementTree.fromstring(archive.read(part))
            namespace = {"dc": "http://purl.org/dc/elements/1.1/"}
            result.title = _text(root.findtext("dc:title", namespaces=namespace))
            result.authors = _text(root.findtext("dc:creator", namespaces=namespace))
            result.description = _text(root.findtext("dc:description", namespaces=namespace))
        except Exception:
            logger.debug("Could not extract DOCX properties", exc_info=True)
    return result


def resolve_upload_metadata(
    raw: bytes, *, filename: str, title: str | None, authors: str | None,
    description: str | None, auto_extract_metadata: bool,
) -> DocumentMetadata:
    """Fill empty values only; extracted bounded fields respect item limits."""
    result = extract_document_metadata(raw, filename) if auto_extract_metadata else DocumentMetadata()
    result.title = _text(title) or (result.title[:255] if result.title else None)
    if not result.title:
        if not auto_extract_metadata:
            raise ValueError("title is required when auto_extract_metadata is false")
        result.title = (Path(filename).stem.replace("_", " ").strip() or "Untitled")[:255]
    result.authors = _text(authors) or (result.authors[:512] if result.authors else None)
    result.description = _text(description) or result.description
    if len(result.title) > 255 or (result.authors and len(result.authors) > 512):
        raise ValueError("title must be at most 255 characters and authors at most 512 characters")
    return result
