"""Assign extracted blocks to resolved structural intervals without app coupling."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field

from .pipeline import NormalizedDocument, NormalizedBlock
from .pipeline import NormalizedSection
from .structure_resolution import StructuralResolution
from .structure_types import NormalizedPageAccounting, SourcePosition, position_inside_interval
from .structure_validation import stable_node_id


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


def block_source_positions(block: NormalizedBlock) -> list[SourcePosition]:
    """Retain every parser locator with its own page and coordinates."""
    provenance = block.metadata.get("provenance") or []
    positions = [SourcePosition(int(row["page_no"]), max(0, block.reading_order),
                                (row.get("bbox") or {}).get("left"),
                                (row.get("bbox") or {}).get("top"))
                 for row in provenance if row.get("page_no")]
    observed = {position.physical_page for position in positions}
    for index, page in enumerate(block.page_numbers or [1]):
        if page not in observed:
            box = block.bounding_boxes[index] if index < len(block.bounding_boxes) else {}
            positions.append(SourcePosition(page, max(0, block.reading_order), box.get("left"), box.get("top")))
    return positions


def block_position(block: NormalizedBlock) -> SourcePosition:
    return min(block_source_positions(block), key=lambda position: (position.physical_page, position.reading_order))


def _position_key(position: SourcePosition) -> tuple[int, int]:
    return position.physical_page, position.reading_order


def _block_interval_owners(block: NormalizedBlock, resolution: StructuralResolution) -> set[str]:
    """Check each source locator, retaining coordinate-based same-page transitions."""
    positions = block_source_positions(block)
    owners = set()
    for position in positions:
        enclosing = [node for node in resolution.nodes[1:]
                     if position_inside_interval(position, node.position, node.end_exclusive)]
        owner = max(enclosing, key=lambda node: (node.level, _position_key(node.position)), default=resolution.nodes[0])
        owners.add(owner.node_id)
    return owners


def segment_boundary_content(document: NormalizedDocument, resolution: StructuralResolution) -> NormalizedDocument:
    """Split prose on nonoverlapping parser spans covering all non-whitespace text.

    Original references and full source evidence remain in each segment's metadata.
    Missing, overlapping or partial spans are deliberately left for ownership diagnostics.
    """
    result = deepcopy(document)
    if "bookmark_authoritative" not in resolution.diagnostics:
        return result
    pages = {page.physical_page_number: page for page in document.pages}
    output = []
    for block in result.blocks:
        provenance = block.metadata.get("provenance") or []
        if block.block_type not in {"paragraph", "list", "list_item"} or len(_block_interval_owners(block, resolution)) < 2:
            output.append(block)
            continue
        spans = [row.get("charspan") for row in provenance]
        reliable = bool(spans) and all(isinstance(span, (list, tuple)) and len(span) == 2
            and all(type(value) is int for value in span) for span in spans)
        if reliable:
            reliable = ({int(row.get("page_no") or 0) for row in provenance} == set(block.page_numbers)
                and spans[0][0] == 0 and spans[-1][1] == len(block.display_text)
                and all(0 <= start < end <= len(block.display_text) for start, end in spans)
                and all(left[1] <= right[0] and not block.display_text[left[1]:right[0]].strip()
                        for left, right in zip(spans, spans[1:])))
        if not reliable:
            output.append(block)
            continue
        for index, (row, (start, source_end)) in enumerate(zip(provenance, spans)):
            # Inter-span whitespace belongs to the preceding segment, preserving
            # every character without attributing unobserved words to a page.
            end = spans[index + 1][0] if index + 1 < len(spans) else source_end
            segment = deepcopy(block)
            segment.block_id = stable_node_id(resolution.document_id, "block-segment", f"{block.block_id}:{start}:{end}")
            segment.display_text = block.display_text[start:end]
            segment.page_numbers = [int(row["page_no"])]
            page = pages.get(segment.page_numbers[0])
            segment.page_labels = [page.displayed_page_label] if page and page.displayed_page_label is not None else []
            segment.bounding_boxes = [deepcopy(row["bbox"])] if row.get("bbox") else []
            segment.metadata["source_segmentation"] = {
                "original_block_id": block.block_id, "charspan": [start, end],
                "original_provenance": deepcopy(provenance),
                "original_bounding_boxes": deepcopy(block.bounding_boxes),
                "original_page_numbers": list(block.page_numbers),
                "original_source_spans": [{"start": span.start, "end": span.end} for span in block.source_spans],
            }
            segment.metadata["provenance"] = [{**deepcopy(row), "charspan": [0, end - start]}]
            segment.source_spans = []
            output.append(segment)
    result.blocks = output
    return result


def attach_content(document: NormalizedDocument, resolution: StructuralResolution) -> ContentOwnership:
    """Choose the narrowest established interval; root owns unsupported blocks."""
    blocks = {block.block_id: block for block in document.blocks}
    root_id = resolution.nodes[0].node_id
    evidence_to_block = {evidence.evidence_id: evidence.original_reference
                         for evidence in resolution.evidence if evidence.source_kind == "heading"}
    structural_owner: dict[str, str] = {}
    diagnostics: list[str] = []
    bookmark_mode = "bookmark_authoritative" in resolution.diagnostics
    contents = next((node for node in resolution.nodes if node.node_type == "contents"), None)
    if contents:
        for evidence in resolution.evidence:
            if evidence.source_kind == "toc_entry" and evidence.original_reference in blocks:
                block = blocks[evidence.original_reference]
                if position_inside_interval(block_position(block), contents.position, contents.end_exclusive):
                    if not bookmark_mode:
                        structural_owner[block.block_id] = contents.node_id
                else:
                    diagnostics.append(f"toc_evidence_outside_contents:{block.block_id}")
    if bookmark_mode and contents is None:
        diagnostics.extend(f"toc_evidence_outside_contents:{evidence.original_reference}"
                           for evidence in resolution.evidence if evidence.source_kind == "toc_entry"
                           and evidence.original_reference in blocks)
    for node in resolution.nodes[1:]:
        for evidence_id in node.evidence_ids:
            block_id = evidence_to_block.get(evidence_id)
            if block_id in blocks and not bookmark_mode:
                structural_owner[block_id] = node.node_id
    established = [node for node in resolution.nodes[1:] if node.resolution_status in {"resolved", "uncertain"}]
    owners: dict[str, str] = {}
    content_types: dict[str, str] = {}
    for block in sorted(document.blocks, key=lambda value: (min(value.page_numbers or [1]), value.reading_order,
                                                              value.block_id)):
        if block.block_id in structural_owner:
            owners[block.block_id] = structural_owner[block.block_id]
        else:
            position = block_position(block)
            enclosing = [node for node in established if position_inside_interval(position, node.position, node.end_exclusive)]
            owner = max(enclosing, key=lambda node: (node.level, _position_key(node.position)), default=None)
            owners[block.block_id] = owner.node_id if owner else root_id
        if bookmark_mode and len(_block_interval_owners(block, resolution)) > 1:
            owners[block.block_id] = root_id
            diagnostics.append(f"unresolved_cross_boundary_provenance:{block.block_id}")
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
    return ContentOwnership(owners, content_types, accounting, unparsed, sorted(relations), diagnostics)


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
        and ownership.block_owner_ids.get(evidence.original_reference) == node.node_id
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
