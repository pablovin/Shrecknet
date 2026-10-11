"""Extract canonical Novelist upload text while retaining original PDF pages."""
from __future__ import annotations
import re
from io import BytesIO
from app.schemas.novelist import SourcePage

_BULLET_OR_NUMBERED_START = re.compile(r"^\s*(?:[●•\-\*]\s+|\d+[.)]\s+)")
_TIMESTAMP_MARKER = re.compile(r"\(\d{1,2}:\d{2}:\d{2}\)")


def normalize_pdf_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if (_BULLET_OR_NUMBERED_START.match(line) or _TIMESTAMP_MARKER.search(line)) and current:
            blocks.append(current)
            current = []
        current.append(line)
    if current:
        blocks.append(current)
    return '\n\n'.join(' '.join(block) for block in blocks)


def extract_source(filename: str, raw: bytes) -> tuple[str, list[SourcePage]]:
    if not raw:
        raise ValueError("Uploaded file is empty")
    suffix = filename.lower().rsplit('.', 1)[-1]
    if suffix in {'txt', 'md', 'json'}:
        return raw.decode('utf-8', errors='ignore').strip(), []
    if suffix != 'pdf':
        raise ValueError("Unsupported file type. Use .txt, .md, .json, or .pdf")
    try:
        from PyPDF2 import PdfReader
    except ImportError as exc:
        raise RuntimeError("PyPDF2 is required to process PDF files") from exc
    text = ''
    pages = []
    for number, page in enumerate(PdfReader(BytesIO(raw)).pages, start=1):
        try:
            content = normalize_pdf_text(page.extract_text() or '')
        except Exception as exc:
            raise ValueError(f"Could not extract PDF page {number}") from exc
        if not content:
            continue
        if text:
            text += '\n\n'
        start = len(text)
        text += content
        pages.append(SourcePage(page=number, start=start, end=len(text)))
    return text, pages
