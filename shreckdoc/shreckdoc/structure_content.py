"""Assign extracted blocks to resolved structural intervals without app coupling."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from .pipeline import NormalizedDocument
from .pipeline import NormalizedSection
from .structure_resolution import StructuralResolution
from .structure_types import NormalizedPageAccounting, SourcePosition


@dataclass(slots=True)
class ContentOwnership:
    """One canonical source-block owner plus page and continuation evidence."""

    block_owner_ids: dict[str, str]
    content_types: dict[str, str]
    page_accounting: list[NormalizedPageAccounting]
    unparsed_pages: list[int]
    possible_continuations: list[tuple[str, str]] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


CONTENT_TYPE = {
    "paragraph": "paragraph", "list": "list", "list_item": "list_item",
    "table": "table", "picture": "image", "image": "image",
    "caption": "caption", "formula": "equation", "equation": "equation",
    "unknown": "unknown", "unparsed_region": "unparsed_region",
    "page_header": "paragraph", "page_footer": "paragraph", "code": "unknown",
    "key_value": "unknown", "heading": "paragraph",
}


def _block_position(block) -> SourcePosition:
    page = min(block.page_numbers) if block.page_numbers else 1
    box = next((value for value in block.bounding_boxes if value), {})
    return SourcePosition(page, max(0, block.reading_order), box.get("left"), box.get("top"))


def _position_key(position: SourcePosition) -> tuple[int, int]:
    return position.physical_page, position.reading_order


def _inside(position: SourcePosition, start: SourcePosition, end: SourcePosition | None) -> bool:
    return _position_key(position) >= _position_key(start) and (end is None or _position_key(position) < _position_key(end))


def attach_content(document: NormalizedDocument, resolution: StructuralResolution) -> ContentOwnership:
    """Choose the narrowest established interval; root owns unsupported blocks."""
    blocks = {block.block_id: block for block in document.blocks}
    candidates = {node.node_id: node for node in resolution.nodes}
    root_id = resolution.nodes[0].node_id
    evidence_to_block = {evidence.evidence_id: evidence.original_reference
                         for evidence in resolution.evidence if evidence.source_kind == "heading"}
    structural_owner: dict[str, str] = {}
    for node in resolution.nodes[1:]:
        for evidence_id in node.evidence_ids:
            block_id = evidence_to_block.get(evidence_id)
            if block_id in blocks:
                structural_owner[block_id] = node.node_id
    established = [node for node in resolution.nodes[1:] if node.resolution_status == "resolved"]
    owners: dict[str, str] = {}
    content_types: dict[str, str] = {}
    for block in sorted(document.blocks, key=lambda value: (min(value.page_numbers or [1]), value.reading_order,
                                                              value.block_id)):
        if block.block_id in structural_owner:
            owners[block.block_id] = structural_owner[block.block_id]
        else:
            position = _block_position(block)
            enclosing = [node for node in established if _inside(position, node.position, node.end_exclusive)]
            owner = max(enclosing, key=lambda node: (node.level, _position_key(node.position)), default=None)
            owners[block.block_id] = owner.node_id if owner else root_id
        content_types[block.block_id] = CONTENT_TYPE.get(block.block_type, "unknown")

    pages_with_blocks = {page for block in document.blocks for page in block.page_numbers}
    unparsed = [page.physical_page_number for page in document.pages
                if page.physical_page_number not in pages_with_blocks]
    accounting = [NormalizedPageAccounting(
        page.physical_page_number,
        "extracted" if page.physical_page_number in pages_with_blocks else "unknown",
        note=None if page.physical_page_number in pages_with_blocks else
        "No extraction evidence; absence of blocks does not establish a blank page",
    ) for page in document.pages]

    # Only explicit parser continuation references create a relation. Adjacent
    # blocks alone never merge tables or paragraphs.
    by_reference = {block.docling_reference: block.block_id for block in document.blocks if block.docling_reference}
    relations: set[tuple[str, str]] = set()
    for block in document.blocks:
        native = block.metadata.get("native") if isinstance(block.metadata.get("native"), dict) else {}
        reference = native.get("continued_from") or native.get("continuation_of")
        earlier = by_reference.get(str(reference)) if reference else None
        if earlier and earlier != block.block_id:
            relations.add((earlier, block.block_id))
    return ContentOwnership(owners, content_types, accounting, unparsed, sorted(relations))


def project_resolved_document(document: NormalizedDocument, resolution: StructuralResolution) -> NormalizedDocument:
    """Project resolved ownership into the existing chunker's section contract."""
    ownership = attach_content(document, resolution)
    structural = {node.node_id: node for node in resolution.nodes}
    sections: dict[str, NormalizedSection] = {}
    for node in resolution.nodes:
        path: list[str] = []
        current = node
        seen: set[str] = set()
        while current.node_type != "document" and current.node_id not in seen:
            seen.add(current.node_id)
            path.append(current.title)
            current = structural.get(current.parent_id) if current.parent_id else resolution.nodes[0]
        sections[node.node_id] = NormalizedSection(
            section_id=node.node_id, parent_section_id=node.parent_id, heading=None if node.node_type == "document" else node.title,
            heading_level=node.level, heading_path=list(reversed(path)), evidence_ids=list(node.evidence_ids),
            source_kind=node.source_kind if node.source_kind in {"bookmark", "toc", "heading", "inferred"} else None,
        )
    copied_blocks = deepcopy(document.blocks)
    by_id = {block.block_id: block for block in copied_blocks}
    structural_heading_ids = {
        evidence.original_reference for node in resolution.nodes[1:] for evidence_id in node.evidence_ids
        for evidence in resolution.evidence
        if evidence.evidence_id == evidence_id and evidence.source_kind == "heading"
    }
    for source_block_id, owner_id in ownership.block_owner_ids.items():
        block = by_id[source_block_id]
        if block.block_type == "heading" and source_block_id not in structural_heading_ids:
            block.block_type = "paragraph"
        block.section_id = owner_id
        section = sections[owner_id]
        section.block_ids.append(block.block_id)
        section.page_numbers = sorted(set(section.page_numbers + block.page_numbers))
        section.page_labels = list(dict.fromkeys(section.page_labels + block.page_labels))
    for section in sections.values():
        section.block_ids.sort(key=lambda block_id: (by_id[block_id].reading_order, block_id))
    pages = deepcopy(document.pages)
    for page, accounting in zip(pages, ownership.page_accounting):
        page.accounting = accounting
    return NormalizedDocument(
        pages=pages, sections=list(sections.values()), blocks=list(by_id.values()),
        evidence=deepcopy(document.evidence), page_accounting=ownership.page_accounting,
    )
