"""Source hierarchy and editorial organization service; graph and vectors remain untouched."""
from __future__ import annotations

from hashlib import sha256
from html.parser import HTMLParser
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
    SourceHierarchy, SourceOrganization, SourceCollection,
)
from app.repositories.source_scope import OntologyScope, scoped_item
from app.schemas.book_map import BookMapV4
from app.schemas.sources import (
    AuthoredContent, ExtractedContent, HierarchyInput, HierarchyRead, Paragraph,
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
        source_type=record.source_type,
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
        source_type = payload.source_type
    else:
        previous = latest_hierarchy(db, hierarchy_id)
        if payload.expected_version != previous.version:
            _fail(409, "Template changed; reload before saving")
        version = previous.version + 1
        image_path = previous.image_path
        if previous.source_type != payload.source_type:
            _fail(422, "Template category cannot be changed after creation")
        source_type = previous.source_type
    record = SourceHierarchy(
        id=hierarchy_id, version=version, name=payload.name,
        description=payload.description, section_types=payload.section_types,
        source_type=source_type,
        archived=payload.archived, image_path=image_path,
    )
    try:
        db.add(record)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        _fail(409, "Hierarchy template was updated concurrently; reload and retry")
    return hierarchy_read(record)


def delete_hierarchy(db: Session, scope: OntologyScope, hierarchy_id: str, expected_version: int,
                     remove_items: bool = False) -> HierarchyRead:
    """Retire a template and optionally remove its entries from this ontology."""
    previous = latest_hierarchy(db, hierarchy_id)
    if previous.version != expected_version:
        _fail(409, "Template changed; reload before deleting")
    if remove_items:
        db.query(SourceCollection).filter(
            SourceCollection.ontology_id == scope.ontology_id,
            SourceCollection.hierarchy_template_id == hierarchy_id,
        ).delete(synchronize_session=False)
        db.commit()
    return save_hierarchy(db, HierarchyInput(
        source_type=previous.source_type, name=previous.name,
        description=previous.description or "", section_types=previous.section_types,
        archived=True, expected_version=previous.version,
    ), hierarchy_id)


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


class _AuthoredDocumentParser(HTMLParser):
    """Split rich-editor HTML into heading sections and their original markup."""

    def __init__(self, content: str):
        super().__init__(convert_charrefs=False)
        self.sections: list[tuple[int | None, str, str]] = []
        self.heading_level: int | None = None
        self.heading_tag: str | None = None
        self.heading_text: list[str] = []
        self.section_title: str | None = None
        self.section_level: int | None = None
        self.body: list[str] = []
        self.feed(content)
        self.close()
        self._flush_section()

    def _flush_section(self) -> None:
        if self.section_title is not None:
            self.sections.append((self.section_level, self.section_title, "".join(self.body)))
        elif self.body:
            self.sections.append((None, "", "".join(self.body)))
        self.body = []

    def handle_starttag(self, tag: str, attrs):
        if self.heading_level is not None:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._flush_section()
            self.heading_level = int(tag[1])
            self.heading_tag = tag
            self.heading_text = []
            return
        self.body.append(self.get_starttag_text() or f"<{tag}>")

    def handle_startendtag(self, tag: str, attrs):
        if self.heading_level is None:
            self.body.append(self.get_starttag_text() or f"<{tag}/>")

    def handle_endtag(self, tag: str):
        if self.heading_level is not None:
            if tag == self.heading_tag:
                title = " ".join("".join(self.heading_text).split())
                self.section_level, self.section_title = self.heading_level, title
                self.heading_level = None
                self.heading_tag = None
                self.heading_text = []
            return
        self.body.append(f"</{tag}>")

    def handle_data(self, data: str):
        if self.heading_level is not None:
            self.heading_text.append(data)
        else:
            self.body.append(data)

    def handle_entityref(self, name: str):
        token = f"&{name};"
        if self.heading_level is not None:
            from html import unescape
            self.heading_text.append(unescape(token))
        else:
            self.body.append(token)

    def handle_charref(self, name: str):
        token = f"&#{name};"
        if self.heading_level is not None:
            from html import unescape
            self.heading_text.append(unescape(token))
        else:
            self.body.append(token)


def authored_document_sections(content: str) -> list[tuple[int | None, str, str]]:
    parser = _AuthoredDocumentParser(content)
    if parser.heading_level is not None:
        _fail(422, "A heading is not closed in the rich-text document")
    return parser.sections


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
    if template.source_type != payload.source_type:
        _fail(422, "Hierarchy template does not belong to this source category")
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
    if template.source_type != payload.source_type:
        _fail(422, "Hierarchy template does not belong to this source category")
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


def save_organization(
    db: Session, item: ShreckWorldLibraryItem, payload: OrganizationInput,
    *, markdown_override: str | None = None,
) -> OrganizationRead:
    if payload.expected_revision != item.source_revision:
        _fail(409, "Source changed; reload and reconcile your edits")
    from app.services.source_assets import validate_asset_references, update_markdown_assets
    # Older editors may omit the new asset metadata when saving a section.
    # Preserve existing references so those writes cannot silently detach
    # imported Markdown and image assets.
    previous = {node.id: node for node in get_organization(db, item).nodes}
    for node in payload.nodes:
        old = previous.get(node.id)
        if old is None:
            continue
        if node.metadata.markdown_asset_id is None:
            node.metadata.markdown_asset_id = old.metadata.markdown_asset_id
        if node.metadata.image_asset_id is None:
            node.metadata.image_asset_id = old.metadata.image_asset_id
        if node.metadata.legacy_image_url is None:
            node.metadata.legacy_image_url = old.metadata.legacy_image_url
        if not node.metadata.description and old.metadata.description:
            node.metadata.description = old.metadata.description
    validate_asset_references(db, item.id, payload.nodes)
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
    staged_assets = []
    values: dict = {"source_revision": next_revision}
    if item.authored:
        markdown = (markdown_override.encode("utf-8") if markdown_override is not None
                    else render_authored_markdown(payload.nodes))
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
        if item.authored:
            staged_assets = update_markdown_assets(db, item.id, payload.nodes)
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
        for staged in staged_assets:
            staged.unlink(missing_ok=True)
        raise
    return OrganizationRead(revision=next_revision, nodes=payload.nodes)


def save_authored_document(
    db: Session, item: ShreckWorldLibraryItem, expected_revision: int, content: str,
) -> OrganizationRead:
    """Save the rich editor's complete authored Markdown and its section projection."""
    if not item.authored:
        _fail(422, "Only authored Markdown uses the source organization editor")
    if expected_revision != item.source_revision:
        _fail(409, "Source changed; reload before saving")
    if len(content.encode("utf-8")) > 2_000_000:
        _fail(413, "Markdown document exceeds the 2 MB editor limit")
    current = get_organization(db, item)
    root = next((node for node in current.nodes if node.parent_id is None), None)
    if root is None:
        _fail(422, "Authored source root is unavailable")
    template = db.get(SourceHierarchy, (item.hierarchy_template_id, item.hierarchy_template_version))
    if template is None:
        _fail(422, "Authored source hierarchy is unavailable")

    old_by_depth: dict[int, list[SourceSection]] = {}
    old_children: dict[str, list[SourceSection]] = {}
    for node in current.nodes:
        if node.parent_id is not None:
            old_children.setdefault(node.parent_id, []).append(node)

    def gather(parent_id: str, depth: int) -> None:
        children = sorted(old_children.get(parent_id, []), key=lambda node: (node.order, node.id))
        old_by_depth.setdefault(depth, []).extend(children)
        for child in children:
            gather(child.id, depth + 1)

    gather(root.id, 1)
    used_ids: set[str] = {root.id}
    depth_offsets: dict[int, int] = {}
    old_by_title: dict[tuple[int, str], list[SourceSection]] = {}
    for depth, rows in old_by_depth.items():
        for node in rows:
            old_by_title.setdefault((depth, node.title.casefold()), []).append(node)

    def paragraph_rows(text: str, existing: SourceSection | None) -> list[Paragraph]:
        if not text.strip():
            return []
        previous = existing.content_reference.paragraphs if existing and isinstance(existing.content_reference, AuthoredContent) else []
        paragraph_id = previous[0].id if previous else str(uuid4())
        return [Paragraph(id=paragraph_id, text=text.strip())]

    root_title = root.title
    root_body: list[str] = []
    headings = authored_document_sections(content)
    root_heading_seen = False
    root_body.extend(body for depth, _, body in headings if depth is None and body.strip())
    new_nodes: list[SourceSection] = []
    root_updated = root.model_copy(deep=True)
    stack: dict[int, SourceSection] = {0: root_updated}
    last_depth = 0

    for heading_level, title, body in headings:
        if heading_level is None:
            continue
        if not title:
            _fail(422, "Headings in an authored Markdown document cannot be empty")
        if heading_level == 1:
            if root_heading_seen:
                _fail(422, "An authored Markdown document can have only one level-one heading")
            root_heading_seen = True
            root_title = title
            if body.strip():
                root_body.append(body)
            continue
        depth = heading_level - 1
        if depth > len(template.section_types):
            _fail(422, "Heading depth exceeds the source hierarchy template")
        if depth > last_depth + 1:
            _fail(422, "Headings cannot skip a level")
        parent = stack.get(depth - 1)
        if parent is None:
            _fail(422, "Heading has no parent section")
        candidates = old_by_title.get((depth, title.casefold()), [])
        old = next((node for node in candidates if node.id not in used_ids), None)
        if old is None:
            rows = old_by_depth.get(depth, [])
            offset = depth_offsets.get(depth, 0)
            old = next((node for node in rows[offset:] if node.id not in used_ids), None)
        depth_offsets[depth] = depth_offsets.get(depth, 0) + 1
        if old is not None:
            used_ids.add(old.id)
            section_id = old.id
            metadata = old.metadata.model_copy(deep=True)
            section_type = old.section_type
        else:
            section_id = str(uuid4())
            metadata = None
            section_type = template.section_types[depth - 1]
        existing = old
        section = SourceSection(
            id=section_id, parent_id=parent.id,
            section_type=section_type, title=title,
            order=sum(1 for node in new_nodes if node.parent_id == parent.id),
            content_reference=AuthoredContent(paragraphs=paragraph_rows(body, existing)),
            metadata=metadata or {},
        )
        new_nodes.append(section)
        stack[depth] = section
        for deeper in [level for level in stack if level > depth]:
            del stack[deeper]
        last_depth = depth

    root_updated.title = root_title
    root_updated.content_reference = AuthoredContent(paragraphs=paragraph_rows("\n\n".join(root_body), root))
    nodes = [root_updated, *new_nodes]
    payload = OrganizationInput(expected_revision=expected_revision, nodes=nodes)
    return save_organization(db, item, payload, markdown_override=content)


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
            source_type=previous.source_type, archived=previous.archived, image_path=str(target),
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
