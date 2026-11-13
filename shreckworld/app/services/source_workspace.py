"""Source hierarchy and editorial organization service; graph and vectors remain untouched."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import os
import re
from app.core.config import get_settings
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    ShreckWorldDocument, ShreckWorldDocumentBookMap, ShreckWorldLibraryItem,
    SourceHierarchy, SourceOrganization,
)
from app.repositories.source_scope import OntologyScope, scoped_item
from app.schemas.book_map import BookMapV4
from app.schemas.sources import (
    AuthoredContent, ExtractedContent, HierarchyInput, HierarchyRead,
    OrganizationInput, OrganizationRead, SourceCreate, SourceSection, SourceSettings,
)
from app.services.book_map_contract import try_read_book_map


def _fail(status: int, detail: str):
    raise HTTPException(status_code=status, detail=detail)


def scoped_source(db: Session, scope: OntologyScope, source_id: str) -> ShreckWorldLibraryItem:
    item = scoped_item(db, scope, source_id)
    if item is None:
        _fail(404, "Source not found in this world")
    return item


def latest_hierarchy(db: Session, hierarchy_id: str) -> SourceHierarchy:
    record = db.scalar(select(SourceHierarchy).where(
        SourceHierarchy.id == hierarchy_id,
    ).order_by(SourceHierarchy.version.desc()).limit(1))
    if record is None:
        _fail(404, "Hierarchy template not found")
    return record


def hierarchy_read(record: SourceHierarchy) -> HierarchyRead:
    return HierarchyRead(
        id=record.id, version=record.version, name=record.name,
        description=record.description or "", section_types=record.section_types,
        archived=record.archived, has_image=bool(record.image_path),
    )


def list_hierarchies(db: Session) -> list[HierarchyRead]:
    # Version history remains accessible by explicit id + version, but the picker
    # shows only the latest record for each template.
    records = db.scalars(select(SourceHierarchy).order_by(
        SourceHierarchy.id, SourceHierarchy.version.desc(),
    )).all()
    latest: dict[str, SourceHierarchy] = {}
    for record in records:
        latest.setdefault(record.id, record)
    return [hierarchy_read(row) for row in latest.values()]


def get_hierarchy(db: Session, hierarchy_id: str, version: int) -> HierarchyRead:
    record = db.get(SourceHierarchy, (hierarchy_id, version))
    if record is None:
        _fail(404, "Hierarchy template version not found")
    return hierarchy_read(record)


def save_hierarchy(db: Session, payload: HierarchyInput, hierarchy_id: str | None = None) -> HierarchyRead:
    if hierarchy_id is None:
        if payload.expected_version is not None:
            _fail(422, "expected_version is only valid for updates")
        hierarchy_id = str(uuid4())
        version = 1
        image_path = None
    else:
        previous = latest_hierarchy(db, hierarchy_id)
        if payload.expected_version != previous.version:
            _fail(409, "Template changed; reload before saving")
        version = previous.version + 1
        image_path = previous.image_path
    record = SourceHierarchy(
        id=hierarchy_id, version=version, name=payload.name,
        description=payload.description, section_types=payload.section_types,
        archived=payload.archived, image_path=image_path,
    )
    try:
        db.add(record)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _fail(409, "Hierarchy template was updated concurrently; reload and retry")
    return hierarchy_read(record)


def render_authored_markdown(nodes: list[SourceSection]) -> bytes:
    """Materialize the source tree into one readable Markdown file.

    The organization is authoritative for authored sections. Each saved revision
    receives a new physical .md file, keeping earlier snapshots intact.
    """
    root = next(node for node in nodes if node.parent_id is None)
    children: dict[str, list[SourceSection]] = {}
    for node in nodes:
        if node.parent_id is not None:
            children.setdefault(node.parent_id, []).append(node)
    lines: list[str] = []

    def visit(node: SourceSection, depth: int) -> None:
        lines.append(f'{"#" * min(depth, 6)} {node.title}')
        lines.append("")
        if isinstance(node.content_reference, AuthoredContent):
            for paragraph in node.content_reference.paragraphs:
                if paragraph.text.strip():
                    lines.append(paragraph.text.rstrip())
                    lines.append("")
        for child in sorted(children.get(node.id, []), key=lambda child: (child.order, child.id)):
            visit(child, depth + 1)

    visit(root, 1)
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _write_authored_revision(item: ShreckWorldLibraryItem, data: bytes) -> Path:
    """Stage immutable revision data before the database revision switch."""
    root = (get_settings().media_root / "ontologies" / str(item.ontology_id) / item.id).resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"source-{uuid4().hex}.md"
    with path.open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    return path


def create_authored_source(db: Session, scope: OntologyScope, payload: SourceCreate) -> ShreckWorldLibraryItem:
    template = db.get(SourceHierarchy, (payload.hierarchy_template_id, payload.hierarchy_template_version))
    if template is None or template.archived:
        _fail(422, "Select an active hierarchy template version")
    item = ShreckWorldLibraryItem(
        id=str(uuid4()), ontology_id=scope.ontology_id, shreckworld_id=None,
        source_type=payload.source_type, hierarchy_template_id=template.id,
        hierarchy_template_version=template.version, source_revision=1, authored=True,
        title=payload.title, authority_tier="custom", authority_priority=100,
        pdf_path="", source_sha256=sha256(b"").hexdigest(),
        embedding_status="not_embedded", index_status="unindexed",
        mime_type="text/plain",
    )
    # Start each authored Source from its pinned template without adding
    # template levels to the ontology or overwriting an existing source.
    outline = [_root(item)]
    parent_id = outline[0].id
    for section_type in template.section_types:
        section_id = str(uuid4())
        outline.append(SourceSection(
            id=section_id, parent_id=parent_id, order=0,
            section_type=section_type, title=f"Untitled {section_type}",
            content_reference=AuthoredContent(),
        ))
        parent_id = section_id
    content = render_authored_markdown(outline)
    try:
        path = _write_authored_revision(item, content)
    except OSError as exc:
        _fail(500, "Could not create the Markdown source")
    item.pdf_path = str(path)
    item.storage_path = str(path)
    item.original_filename = re.sub(r"[^a-zA-Z0-9._-]+", "-", item.title).strip("-.")[:100] + ".md"
    item.mime_type = "text/markdown"
    item.source_sha256 = sha256(content).hexdigest()
    item.file_size = len(content)
    try:
        db.add(item)
        db.add(SourceOrganization(
            source_id=item.id, revision=1,
            nodes=[node.model_dump(mode="json") for node in outline],
        ))
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    db.refresh(item)
    return item


def update_source_settings(db: Session, scope: OntologyScope, source_id: str, payload: SourceSettings) -> ShreckWorldLibraryItem:
    item = scoped_source(db, scope, source_id)
    template = db.get(SourceHierarchy, (payload.hierarchy_template_id, payload.hierarchy_template_version))
    if template is None:
        _fail(422, "Unknown hierarchy template version")
    if item.source_revision != payload.expected_revision:
        _fail(409, "Source changed; reload before saving")
    result = db.execute(update(ShreckWorldLibraryItem).where(
        ShreckWorldLibraryItem.id == item.id,
        ShreckWorldLibraryItem.source_revision == payload.expected_revision,
    ).values(title=payload.title, source_type=payload.source_type,
             hierarchy_template_id=template.id, hierarchy_template_version=template.version,
             source_revision=payload.expected_revision + 1))
    if result.rowcount != 1:
        db.rollback()
        _fail(409, "Source changed; reload before saving")
    db.commit()
    db.refresh(item)
    return item


def _root(item: ShreckWorldLibraryItem) -> SourceSection:
    return SourceSection(
        id=f"source-{item.id}", parent_id=None, order=0,
        section_type="Source", title=item.title,
        content_reference=AuthoredContent(),
    )


def _section_id(item_id: str, document_id: str, node_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"shreckworld:{item_id}:{document_id}:{node_id}"))


def _project_document(db: Session, item: ShreckWorldLibraryItem) -> list[SourceSection]:
    root = _root(item)
    document = db.scalar(select(ShreckWorldDocument).where(
        ShreckWorldDocument.library_item_id == item.id,
        ShreckWorldDocument.is_active.is_(True),
    ).order_by(ShreckWorldDocument.created_at.desc()).limit(1))
    if document is None:
        return [root]
    stored = db.get(ShreckWorldDocumentBookMap, document.id)
    if stored is None:
        return [root]
    book, error = try_read_book_map(stored.payload)
    if book is None:
        # Do not imply a valid outline when extraction is invalid/unavailable.
        return [root]
    if isinstance(book, BookMapV4):
        visible = [node for node in book.nodes if node.node_type in {
            "part", "chapter", "section", "appendix", "contents",
        }]
        visible.sort(key=lambda n: (n.position.physical_page, n.position.reading_order, n.level, n.id))
        parent_by_id = {node.id: node.parent_id for node in book.nodes}
        def entry(node):
            return node.id, node.title or node.node_type.title(), node.node_type.title(), node.parent_id
    else:
        visible = sorted(book.sections, key=lambda n: (n.pages.start or 0, n.level, n.id))
        parent_by_id = {node.id: node.parent_id for node in visible}
        def entry(node):
            return node.id, node.title, node.node_type.title(), node.parent_id
    ids = {node.id for node in visible}
    offsets: dict[str, int] = {}
    sections = [root]
    for node in visible:
        node_id, title, kind, parent = entry(node)
        while parent and parent not in ids:
            parent = parent_by_id.get(parent)
        parent_key = _section_id(item.id, document.id, parent) if parent else root.id
        order = offsets.get(parent_key, 0)
        offsets[parent_key] = order + 1
        sections.append(SourceSection(
            id=_section_id(item.id, document.id, node_id), parent_id=parent_key,
            section_type=kind, title=title or "Untitled section", order=order,
            content_reference=ExtractedContent(
                document_id=document.id, node_id=node_id, source_sha256=document.source_sha256,
            ),
        ))
    return sections


def get_organization(db: Session, item: ShreckWorldLibraryItem) -> OrganizationRead:
    record = db.scalar(select(SourceOrganization).where(
        SourceOrganization.source_id == item.id,
    ).order_by(SourceOrganization.revision.desc()).limit(1))
    if record is None:
        return OrganizationRead(revision=item.source_revision, nodes=_project_document(db, item))
    nodes = [SourceSection.model_validate(row) for row in record.nodes]
    missing: list[str] = []
    for node in nodes:
        ref = node.content_reference
        if isinstance(ref, ExtractedContent):
            document = db.get(ShreckWorldDocument, ref.document_id)
            if document is None or document.library_item_id != item.id or document.source_sha256 != ref.source_sha256:
                missing.append(node.id)
    return OrganizationRead(revision=item.source_revision, nodes=nodes, unresolved_section_ids=missing)


def save_organization(db: Session, item: ShreckWorldLibraryItem, payload: OrganizationInput) -> OrganizationRead:
    if payload.expected_revision != item.source_revision:
        _fail(409, "Source changed; reload and reconcile your edits")
    root_ids = [node.id for node in payload.nodes if node.parent_id is None]
    if root_ids != [f"source-{item.id}"]:
        _fail(422, "Source root identity cannot change")
    documents = {
        doc.id: doc for doc in db.scalars(select(ShreckWorldDocument).where(
            ShreckWorldDocument.library_item_id == item.id,
        )).all()
    }
    valid_node_ids: dict[str, set[str]] = {}
    for node in payload.nodes:
        ref = node.content_reference
        if isinstance(ref, AuthoredContent):
            if not item.authored and ref.paragraphs:
                _fail(422, "Extracted source text cannot be overwritten")
            continue
        doc = documents.get(ref.document_id)
        if doc is None or doc.source_sha256 != ref.source_sha256:
            _fail(422, "Extracted references must belong to this source and generation")
        if item.authored:
            _fail(422, "Authored sources cannot reference PDF generations")
        if doc.id not in valid_node_ids:
            record = db.get(ShreckWorldDocumentBookMap, doc.id)
            book, error = try_read_book_map(record.payload) if record else (None, "missing")
            if book is None:
                _fail(422, "Referenced Book Map generation is unavailable")
            valid_node_ids[doc.id] = {n.id for n in book.nodes} if isinstance(book, BookMapV4) else {n.id for n in book.sections}
        if ref.node_id not in valid_node_ids[doc.id]:
            _fail(422, "Referenced Book Map node is missing")
    next_revision = item.source_revision + 1
    new_file = None
    values: dict = {"source_revision": next_revision}
    if item.authored:
        markdown = render_authored_markdown(payload.nodes)
        try:
            new_file = _write_authored_revision(item, markdown)
        except OSError as exc:
            _fail(500, "Could not save the Markdown revision")
        values.update(
            pdf_path=str(new_file), storage_path=str(new_file),
            source_sha256=sha256(markdown).hexdigest(),
            file_size=len(markdown),
            index_status="stale" if item.index_status in {"ready", "stale"} else "unindexed",
        )
    try:
        result = db.execute(update(ShreckWorldLibraryItem).where(
            ShreckWorldLibraryItem.id == item.id,
            ShreckWorldLibraryItem.source_revision == payload.expected_revision,
        ).values(**values))
        if result.rowcount != 1:
            db.rollback()
            _fail(409, "Source changed; reload and reconcile your edits")
        db.add(SourceOrganization(
            source_id=item.id, revision=next_revision,
            nodes=[node.model_dump(mode="json") for node in payload.nodes],
        ))
        db.commit()
    except Exception:
        db.rollback()
        if new_file:
            new_file.unlink(missing_ok=True)
        raise
    return OrganizationRead(revision=next_revision, nodes=payload.nodes)


def save_hierarchy_image(
    db: Session, hierarchy_id: str, expected_version: int, image: bytes,
    content_type: str | None,
) -> HierarchyRead:
    """Append a template revision containing a validated, privately stored image."""
    if not image or len(image) > 5 * 1024 * 1024:
        _fail(413, "Hierarchy image must be between 1 byte and 5 MB")
    signatures = {
        "image/png": (image.startswith(bytes.fromhex("89504e470d0a1a0a")), "png"),
        "image/jpeg": (image.startswith(bytes.fromhex("ffd8ff")) and image.endswith(bytes.fromhex("ffd9")), "jpg"),
        "image/webp": (image.startswith(b"RIFF") and image[8:12] == b"WEBP", "webp"),
    }
    valid, extension = signatures.get(content_type or "", (False, ""))
    if not valid:
        _fail(415, "Only PNG, JPEG, or WebP images are supported")
    previous = latest_hierarchy(db, hierarchy_id)
    if previous.version != expected_version:
        _fail(409, "Template changed; reload before uploading an image")
    if previous.archived:
        _fail(409, "Archived templates cannot be modified")
    root = get_settings().media_root / "hierarchy-images" / sha256(hierarchy_id.encode()).hexdigest()[:20]
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{uuid4()}.{extension}"
    try:
        target.write_bytes(image)
        record = SourceHierarchy(
            id=previous.id, version=expected_version + 1, name=previous.name,
            description=previous.description, section_types=list(previous.section_types),
            archived=previous.archived, image_path=str(target),
        )
        db.add(record)
        db.commit()
    except IntegrityError:
        db.rollback()
        target.unlink(missing_ok=True)
        _fail(409, "Hierarchy template changed while uploading; reload and retry")
    except Exception:
        db.rollback()
        target.unlink(missing_ok=True)
        raise
    return hierarchy_read(record)


def hierarchy_image_path(db: Session, hierarchy_id: str, version: int) -> Path:
    record = db.get(SourceHierarchy, (hierarchy_id, version))
    if record is None or not record.image_path:
        _fail(404, "Hierarchy image not found")
    path = Path(record.image_path).resolve()
    media_root = get_settings().media_root.resolve()
    if media_root not in path.parents or not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".webp"}:
        _fail(404, "Hierarchy image not available")
    return path
