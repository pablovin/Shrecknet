"""Source document read/edit contracts and immutable Markdown revisions."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import ShreckWorldLibraryItem
from app.services import source_documents
from app.services.source_documents import DocumentSave, read_document, save_imported_markdown


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(source_documents, "get_settings", lambda: SimpleNamespace(media_root=tmp_path))
    with Session(engine) as session:
        yield session
    engine.dispose()


def make_item(db: Session, path: Path, filename: str, authored: bool = False):
    from hashlib import sha256
    record = ShreckWorldLibraryItem(
        ontology_id=7, source_type="narrative",
        title=filename, pdf_path=str(path), storage_path=str(path),
        original_filename=filename, mime_type="text/markdown",
        source_sha256=sha256(path.read_bytes()).hexdigest(),
        authored=authored, index_status="ready",
    )
    db.add(record); db.commit(); db.refresh(record)
    return record


def test_uploaded_markdown_is_editable_and_revisioned(db, tmp_path):
    original = tmp_path / "original.md"
    original.write_text("# Opening\n", encoding="utf-8")
    item = make_item(db, original, "opening.md")
    before = read_document(item)
    assert before.editable and before.revision == 1
    saved = save_imported_markdown(db, item, DocumentSave(
        expected_revision=1, content="<h1>Opening</h1><p>New paragraph.</p>",
    ))
    assert saved.revision == 2 and saved.editable
    assert original.read_text() == "# Opening\n"
    assert Path(item.storage_path).read_text() == saved.content
    assert item.index_status == "stale"
    with pytest.raises(HTTPException) as stale:
        save_imported_markdown(db, item, DocumentSave(expected_revision=1, content="stale"))
    assert stale.value.status_code == 409


def test_authored_document_keeps_organization_as_edit_authority(db, tmp_path):
    path = tmp_path / "created.md"
    path.write_text("# Story\n", encoding="utf-8")
    item = make_item(db, path, "story.md", authored=True)
    assert read_document(item).editable is False
    with pytest.raises(HTTPException) as conflict:
        save_imported_markdown(db, item, DocumentSave(expected_revision=1, content="override"))
    assert conflict.value.status_code == 422


def test_docx_preview_is_nonexecutable_read_only(db, tmp_path):
    path = tmp_path / "sample.docx"
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body><w:p><w:r><w:t>First line</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>Second line</w:t></w:r></w:p></w:body></w:document>'
    )
    with ZipFile(path, "w") as doc:
        doc.writestr("word/document.xml", xml)
    item = make_item(db, path, "sample.docx")
    preview = read_document(item)
    assert preview.format == "docx" and not preview.editable
    assert preview.content == "First line\n\nSecond line"
