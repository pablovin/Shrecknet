from __future__ import annotations

from io import BytesIO
import sys
import types

import pytest
from fastapi import HTTPException, UploadFile

from app.api.routers import novelist as novelist_router


def test_normalize_pdf_extracted_text_preserves_structural_boundaries() -> None:
    raw = (
        "● Recap of Kingdom of Salt Plot\n"
        "wrapped continuation\n"
        "still same section\n"
        "● Character Backstories (00:22:54)\n"
        "more details\n"
        "1. Numbered item begins\n"
        "continuation"
    )

    normalized = novelist_router._normalize_pdf_extracted_text(raw)

    assert normalized.count("\n\n") == 2
    blocks = normalized.split("\n\n")
    assert blocks[0].startswith("● Recap of Kingdom of Salt Plot wrapped continuation")
    assert "(00:22:54)" in blocks[1]
    assert blocks[2].startswith("1. Numbered item begins continuation")


def test_extract_text_from_upload_pdf_joins_pages_with_double_newline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakePage:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class _FakePdfReader:
        def __init__(self, _stream: BytesIO) -> None:
            self.pages = [
                _FakePage("● First page item\nwrapped line"),
                _FakePage("● Second page item\nnext line"),
            ]

    fake_module = types.ModuleType("PyPDF2")
    fake_module.PdfReader = _FakePdfReader
    monkeypatch.setitem(sys.modules, "PyPDF2", fake_module)

    upload = UploadFile(filename="session.pdf", file=BytesIO(b"%PDF-1.4\n%fake\n"))
    extracted = novelist_router._extract_text_from_upload(upload)

    assert "● First page item wrapped line" in extracted
    assert "● Second page item next line" in extracted
    assert "\n\n" in extracted


def test_extract_text_from_upload_empty_file_raises() -> None:
    upload = UploadFile(filename="session.txt", file=BytesIO(b""))

    with pytest.raises(HTTPException, match="Uploaded file is empty"):
        novelist_router._extract_text_from_upload(upload)


def test_extract_text_from_upload_unsupported_extension_raises() -> None:
    upload = UploadFile(filename="session.docx", file=BytesIO(b"hello"))

    with pytest.raises(HTTPException, match="Unsupported file type"):
        novelist_router._extract_text_from_upload(upload)
