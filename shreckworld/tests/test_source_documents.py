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
from app.models import ShreckWorldLibraryItem, SourceHierarchy
from app.repositories.source_scope import OntologyScope
from app.services import source_documents
from app.services.source_documents import DocumentSave, read_document, save_imported_markdown
from app.services import source_workspace
from app.schemas.sources import SourceCreate
from app.services.source_workspace import create_authored_source, get_organization


@pytest.fixture()
def db(tmp_path, monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(source_documents, "get_settings", lambda: SimpleNamespace(media_root=tmp_path))
    monkeypatch.setattr(source_workspace, "get_settings", lambda: SimpleNamespace(media_root=tmp_path))
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


def test_authored_document_edit_updates_file_and_organization(db, tmp_path):
    db.add(SourceHierarchy(
        id="narrative", version=1, name="Narrative", source_type="narrative",
        section_types=["Chapter", "Scene"], archived=False,
    ))
    db.commit()
    item = create_authored_source(db, OntologyScope(7, "Test World"), SourceCreate(
        title="Story", source_type="narrative", hierarchy_template_id="narrative",
        hierarchy_template_version=1,
    ))
    original_file = Path(item.storage_path)
    item.index_status = "ready"
    db.commit()
    assert read_document(item).editable is True
    content = "<h1>Story, revised</h1><p>A <strong>new</strong> beginning.</p><h2>Chapter One</h2><p>It starts here.</p>"
    saved = save_imported_markdown(db, item, DocumentSave(expected_revision=1, content=content))
    assert saved.revision == 2 and saved.editable and saved.content == content
    assert original_file.exists(), "Previous authored Markdown revision must remain available"
    assert Path(item.storage_path).read_text(encoding="utf-8") == content
    organization = get_organization(db, item)
    assert organization.nodes[0].title == "Story, revised"
    chapter = next(node for node in organization.nodes if node.title == "Chapter One")
    assert chapter.content_reference.paragraphs[0].text == "<p>It starts here.</p>"
    assert item.index_status == "stale"
    with pytest.raises(HTTPException) as stale:
        save_imported_markdown(db, item, DocumentSave(expected_revision=1, content="stale"))
    assert stale.value.status_code == 409


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
