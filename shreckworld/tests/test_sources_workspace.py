"""Regression tests for source organization, safety and tree invariants."""
from __future__ import annotations

from uuid import uuid4
from pathlib import Path
from types import SimpleNamespace

import app.services.source_workspace as source_workspace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import ShreckWorldLibraryItem, SourceHierarchy
from app.repositories.source_scope import OntologyScope
from app.schemas.sources import (
    AuthoredContent, OrganizationInput, SourceCreate, SourceSection,
)
from app.services.source_workspace import (
    create_authored_source, get_organization, save_organization,
)


def section(id: str, parent: str | None, order: int = 0) -> dict:
    return {
        "id": id, "parent_id": parent, "section_type": "Chapter",
        "title": id, "order": order,
        "content_reference": {"kind": "authored", "paragraphs": []},
    }


def test_tree_rejects_cycles_or_orphans():
    with pytest.raises(ValidationError):
        OrganizationInput(expected_revision=1, nodes=[
            section("root", None), section("a", "b"), section("b", "a"),
        ])
    with pytest.raises(ValidationError):
        OrganizationInput(expected_revision=1, nodes=[
            section("root", None), section("a", "unknown"),
        ])


def test_tree_rejects_duplicate_orders_and_paragraphs():
    with pytest.raises(ValidationError):
        OrganizationInput(expected_revision=1, nodes=[
            section("root", None), section("a", "root"), section("b", "root"),
        ])
    first, second = section("a", "root", 0), section("b", "root", 1)
    first["content_reference"]["paragraphs"] = [{"id": "p1", "text": "A"}]
    second["content_reference"]["paragraphs"] = [{"id": "p1", "text": "B"}]
    with pytest.raises(ValidationError):
        OrganizationInput(expected_revision=1, nodes=[
            section("root", None), first, second,
        ])


@pytest.fixture()
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(SourceHierarchy(
            id="rpg-campaign", version=1, name="RPG Campaign", source_type="narrative",
            section_types=["Adventure", "Story Arc", "Story"], archived=False,
        ))
        session.commit()
        yield session
    engine.dispose()


def test_authored_source_revision_and_world_scoping(db: Session, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(source_workspace, 'get_settings', lambda: SimpleNamespace(media_root=tmp_path))
    world = OntologyScope(7, "Test World")
    item = create_authored_source(
        db, world, SourceCreate(
            title="First Adventure", source_type="narrative",
            hierarchy_template_id="rpg-campaign", hierarchy_template_version=1,
        ),
    )
    assert item.ontology_id == 7 and item.authored
    assert item.mime_type == "text/markdown"
    initial_file = Path(item.storage_path)
    assert initial_file.exists()
    assert "# First Adventure" in initial_file.read_text(encoding="utf-8")
    original = get_organization(db, item)
    assert [node.section_type for node in original.nodes] == ['Source', 'Adventure', 'Story Arc', 'Story']
    root = original.nodes[0]
    nodes = [root, SourceSection(
        id=f"section-{uuid4()}", parent_id=root.id,
        title="Opening", section_type="Story", order=0,
        content_reference=AuthoredContent(paragraphs=[{"id": "p1", "text": "Once upon a time."}]),
    )]
    saved = save_organization(db, item, OrganizationInput(
        expected_revision=original.revision, nodes=nodes,
    ))
    assert saved.revision == original.revision + 1
    db.refresh(item)
    markdown = Path(item.storage_path).read_text(encoding="utf-8")
    assert "## Opening" in markdown
    assert "Once upon a time." in markdown
    assert initial_file.exists(), "Previous saved Markdown revision must remain available"
    assert item.source_sha256 == source_workspace.sha256(markdown.encode()).hexdigest()
    assert get_organization(db, item).nodes[1].content_reference.paragraphs[0].text == "Once upon a time."
    with pytest.raises(HTTPException) as stale:
        save_organization(db, item, OrganizationInput(
            expected_revision=original.revision, nodes=nodes,
        ))
    assert stale.value.status_code == 409


def test_generated_markdown_preserves_section_nesting_and_html():
    from app.schemas.sources import SourceSection, AuthoredContent
    from app.services.source_workspace import render_authored_markdown

    sections = [
        SourceSection(id="root", parent_id=None, section_type="Source", title="Campaign", order=0),
        SourceSection(id="arc", parent_id="root", section_type="Arc", title="War", order=0),
        SourceSection(id="story", parent_id="arc", section_type="Chapter", title="The Battle",
                      order=0, content_reference=AuthoredContent(
                          paragraphs=[{"id": "paragraph-1", "text": "<p><strong>Victory</strong> was costly.</p>"}])),
    ]
    markdown = render_authored_markdown(sections).decode("utf-8")
    assert markdown == (
        "# Campaign\n\n## War\n\n### The Battle\n\n"
        "<p><strong>Victory</strong> was costly.</p>\n"
    )


def test_html_upload_keeps_narrative_type_and_template(db: Session, tmp_path: Path, monkeypatch):
    from io import BytesIO
    from fastapi import UploadFile
    from app.api.routers import shreckworlds
    from app.models import SourceHierarchy

    db.add(SourceHierarchy(
        id="campaign", version=1, name="Campaign",
        source_type="narrative",
        section_types=["Adventure", "Chapter"], archived=False,
    ))
    db.commit()
    monkeypatch.setattr(shreckworlds, "get_settings", lambda: SimpleNamespace(
        media_root=tmp_path, max_document_bytes=10_000,
    ))
    result = shreckworlds.upload_library_item(
        OntologyScope(7, "Test World"), pdf=UploadFile(
            filename="chapter.html", file=BytesIO(b"<h1>Chapter</h1><p>The hero returns.</p>"),
        ), auto_extract_metadata=True, source_type="narrative",
        hierarchy_template_id="campaign", hierarchy_template_version=1,
        session=db,
    )
    assert result.source_type == "narrative"
    assert result.hierarchy_template_id == "campaign"
    assert result.hierarchy_template_version == 1
    assert result.authored is False
    assert Path(result.storage_path).read_bytes().startswith(b"<h1>Chapter</h1>")


def test_untrusted_html_source_download_is_attachment(tmp_path: Path):
    from fastapi import Request
    from app.api.private_files import private_file_response

    original = tmp_path / "source.html"
    original.write_text("<script>alert('unsafe')</script>", encoding="utf-8")
    request = Request({"type": "http", "method": "HEAD", "headers": []})
    response = private_file_response(
        original, request=request, media_type="text/html",
        filename="source.html", revision="f" * 64,
    )
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]
