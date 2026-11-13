"""Project resolved extraction evidence into the typed Book Map V4 contract."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from typing import Any

from shreckdoc.pipeline import NormalizedDocument
from shreckdoc.structure_content import attach_content
from shreckdoc.structure_resolution import StructuralResolution
from shreckdoc.structure_types import SourcePosition
from shreckdoc.structure_validation import require_valid_book_map, stable_node_id

from app.schemas.book_map import (
    BookMapEvidence, BookMapEvidenceDiagnostic, BookMapNode, BookMapPageLocator,
    BookMapPageRange, BookMapQuality, BookMapV4,
)


RESOLVER_VERSION = "book-map-v4-resolver-1"
CHUNKING_VERSION = "book-map-v4-context-1"


def _position(position: SourcePosition) -> dict[str, Any]:
    return {"physical_page": position.physical_page, "reading_order": position.reading_order,
            "x": position.x, "y": position.y}


def _block_position(block) -> SourcePosition:
    page = min(block.page_numbers) if block.page_numbers else 1
    box = next((value for value in block.bounding_boxes if value), {})
    return SourcePosition(page, max(0, block.reading_order), box.get("left"), box.get("top"))


def _node_order(node: BookMapNode) -> tuple[int, int, str]:
    return node.position.physical_page, node.position.reading_order, node.id


def build_book_map_v4(*, document_id: str, source_sha256: str, document: NormalizedDocument,
                      resolution: StructuralResolution, parser_name: str | None = None,
                      parser_version: str | None = None, chunk_ids_by_block: dict[str, list[str]] | None = None,
                      index_alignment: str = "legacy_context") -> BookMapV4:
    """Build, account, and hard-validate a generation-pinned Book Map tree."""
    ownership = attach_content(document, resolution)
    pages = {page.physical_page_number: page for page in document.pages}
    blocks = {block.block_id: block for block in document.blocks}
    structural_by_id = {node.node_id: node for node in resolution.nodes}
    root = resolution.nodes[0]
    evidence: dict[str, BookMapEvidence] = {}
    for row in resolution.evidence:
        evidence[row.evidence_id] = BookMapEvidence(
            id=row.evidence_id, source_kind=row.source_kind, original_reference=row.original_reference,
            locator=_position(row.position), parser_name=parser_name, parser_version=parser_version,
            observed_features=row.observed_features,
            diagnostics=[BookMapEvidenceDiagnostic(code=entry.code, message=entry.message, rejected=entry.rejected)
                         for entry in row.diagnostics],
        )

    page_evidence: dict[int, str] = {}
    block_evidence: dict[str, str] = {}
    for block in document.blocks:
        evidence_id = stable_node_id(document_id, "block-evidence", block.block_id)
        block_evidence[block.block_id] = evidence_id
        position = _block_position(block)
        evidence[evidence_id] = BookMapEvidence(
            id=evidence_id, source_kind="block", original_reference=block.docling_reference or block.block_id,
            locator=_position(position), parser_name=parser_name, parser_version=parser_version,
            observed_features=[f"block_type:{block.block_type}",
                               *([f"native_type:{block.native_type}"] if block.native_type else [])],
        )
    page_block_ids: dict[int, list[str]] = defaultdict(list)
    for block in document.blocks:
        for number in block.page_numbers:
            page_block_ids[number].append(block.block_id)
    for page in document.pages:
        page_no = page.physical_page_number
        evidence_id = stable_node_id(document_id, "page-evidence", str(page_no))
        page_evidence[page_no] = evidence_id
        coverage = "extracted" if page_block_ids.get(page_no) else "unknown"
        diagnostic = [] if coverage == "extracted" else [BookMapEvidenceDiagnostic(
            code="page_not_observed", message="No parser block or confirmed-blank signal was retained", rejected=False)]
        evidence[evidence_id] = BookMapEvidence(
            id=evidence_id, source_kind="page", original_reference=page.page_id,
            locator={"physical_page": page_no, "reading_order": 0}, parser_name=parser_name,
            parser_version=parser_version, observed_features=[f"coverage:{coverage}"], diagnostics=diagnostic,
        )
        page.accounting = next((item for item in ownership.page_accounting if item.physical_page_number == page_no), None)

    own_blocks: dict[str, list[str]] = defaultdict(list)
    heading_block_owners: dict[str, str] = {}
    for structural in resolution.nodes[1:]:
        for evidence_id in structural.evidence_ids:
            for row in resolution.evidence:
                if row.evidence_id == evidence_id and row.source_kind == "heading" and row.original_reference in blocks:
                    heading_block_owners[row.original_reference] = structural.node_id
                    own_blocks[structural.node_id].append(row.original_reference)

    structural_ranges: dict[str, tuple[int, int]] = {}
    for structural in reversed(resolution.nodes[1:]):
        observed_pages = [structural.position.physical_page]
        for block_id in own_blocks.get(structural.node_id, []):
            observed_pages.extend(blocks[block_id].page_numbers)
        for child_id in structural.child_ids:
            if child_id in structural_ranges:
                observed_pages.extend(structural_ranges[child_id])
        structural_ranges[structural.node_id] = (min(observed_pages), max(observed_pages))

    def heading_path(node_id: str | None) -> list[str]:
        path: list[str] = []
        current = structural_by_id.get(node_id) if node_id else None
        visited: set[str] = set()
        while current and current.node_id != root.node_id and current.node_id not in visited:
            visited.add(current.node_id)
            path.append(current.title)
            current = structural_by_id.get(current.parent_id) if current.parent_id else None
        return list(reversed(path))

    nodes: dict[str, BookMapNode] = {}
    for structural in resolution.nodes:
        start, end = ((1, max(pages, default=1)) if structural.node_type == "document"
                      else structural_ranges.get(structural.node_id,
                                                 (structural.position.physical_page, structural.position.physical_page)))
        if structural.node_type == "document" and not pages:
            start = end = structural.position.physical_page
        locators: list[BookMapPageLocator] = []
        for block_id in own_blocks.get(structural.node_id, []):
            block = blocks[block_id]
            for number in block.page_numbers or [structural.position.physical_page]:
                page = pages.get(number)
                box = next((value for value in block.bounding_boxes if value), {})
                locators.append(BookMapPageLocator(physical_page=number,
                    printed_label=page.displayed_page_label if page else None,
                    reading_order=block.reading_order, x=box.get("left"), y=box.get("top")))
        source_ids = list(dict.fromkeys(own_blocks.get(structural.node_id, [])))
        evidence_ids = list(structural.evidence_ids)
        evidence_ids.extend(block_evidence[block_id] for block_id in source_ids)
        nodes[structural.node_id] = BookMapNode(
            id=structural.node_id, document_id=document_id, node_type=structural.node_type,
            title=structural.title, position=_position(structural.position), level=structural.level,
            parent_id=structural.parent_id, child_ids=list(structural.child_ids),
            heading_path=heading_path(structural.node_id), structural_node_id=None,
            page_locators=locators, pages=BookMapPageRange(start=start, end=end),
            source_block_ids=source_ids, evidence_ids=list(dict.fromkeys(evidence_ids)),
            chunk_ids=list(dict.fromkeys(chunk_id for block_id in source_ids
                                         for chunk_id in (chunk_ids_by_block or {}).get(block_id, []))),
            resolution_status=structural.resolution_status, parent_confidence=structural.confidence,
            structural_source=structural.source_kind,
            validation_warnings=list(structural.diagnostics),
        )

    content_node_by_block: dict[str, BookMapNode] = {}
    source_ref_to_node: dict[str, list[BookMapNode]] = defaultdict(list)
    for block in sorted(document.blocks, key=lambda row: (min(row.page_numbers or [1]), row.reading_order, row.block_id)):
        if block.block_id in heading_block_owners:
            continue
        owner_id = ownership.block_owner_ids[block.block_id]
        owner = structural_by_id.get(owner_id)
        page_range = (min(block.page_numbers), max(block.page_numbers)) if block.page_numbers else (1, 1)
        content_type = ownership.content_types[block.block_id]
        node_id = stable_node_id(document_id, content_type, block.block_id)
        page_locators = []
        for number in block.page_numbers or [1]:
            page = pages.get(number)
            box = next((value for value in block.bounding_boxes if value), {})
            page_locators.append(BookMapPageLocator(physical_page=number,
                printed_label=page.displayed_page_label if page else None,
                reading_order=block.reading_order, x=box.get("left"), y=box.get("top")))
        node = BookMapNode(
            id=node_id, document_id=document_id, node_type=content_type, title=None,
            position=_position(_block_position(block)), level=(owner.level + 1) if owner else 1,
            parent_id=owner_id, child_ids=[], heading_path=heading_path(owner_id),
            structural_node_id=owner_id,
            page_locators=page_locators,
            pages=BookMapPageRange(start=page_range[0], end=page_range[1]),
            source_block_ids=[block.block_id], evidence_ids=[block_evidence[block.block_id]],
            chunk_ids=list((chunk_ids_by_block or {}).get(block.block_id, [])),
            content_ref=block.block_id, table_ref=block.block_id if content_type == "table" else None,
            media_ref=block.block_id if content_type == "image" else None,
            resolution_status="resolved" if owner else "uncertain",
            validation_warnings=[] if owner else ["content_attached_to_document_root_no_supported_interval"],
        )
        nodes[node_id] = node
        nodes[owner_id].child_ids.append(node_id)
        content_node_by_block[block.block_id] = node
        source_ref_to_node[block.docling_reference or block.block_id].append(node)

    for page_no in ownership.unparsed_pages:
        page = pages[page_no]
        node_id = stable_node_id(document_id, "unparsed_region", f"page-{page_no}")
        nodes[node_id] = BookMapNode(
            id=node_id, document_id=document_id, node_type="unparsed_region", title="Unparsed page",
            position={"physical_page": page_no, "reading_order": 0}, level=1, parent_id=root.node_id,
            child_ids=[], heading_path=[], structural_node_id=root.node_id,
            page_locators=[BookMapPageLocator(physical_page=page_no, printed_label=page.displayed_page_label,
                                              reading_order=0)],
            pages=BookMapPageRange(start=page_no, end=page_no), source_block_ids=[],
            evidence_ids=[page_evidence[page_no]], resolution_status="unresolved",
            validation_warnings=["page_extraction_coverage_unknown"],
        )
        nodes[root.node_id].child_ids.append(node_id)

    for block in document.blocks:
        node = content_node_by_block.get(block.block_id)
        if node is None or node.node_type != "caption":
            continue
        native = block.metadata.get("native") if isinstance(block.metadata.get("native"), dict) else {}
        parent_ref = block.metadata.get("parent_reference") or native.get("parent_reference")
        if parent_ref:
            for parent in source_ref_to_node.get(str(parent_ref), []):
                if parent.node_type in {"image", "table"}:
                    parent.caption_node_ids.append(node.id)

    for source_id, target_id in ownership.possible_continuations:
        source_node = content_node_by_block.get(source_id)
        target_node = content_node_by_block.get(target_id)
        if source_node and target_node:
            source_node.related_node_ids.append(target_node.id)
            target_node.related_node_ids.append(source_node.id)

    # Parent and document ranges include all descendant evidence spans.
    for node in reversed(list(nodes.values())):
        if node.node_type in {"document", "part", "chapter", "section", "appendix"}:
            starts = [node.position.physical_page]
            ends = [node.position.physical_page]
            for block_id in node.source_block_ids:
                starts.extend(blocks[block_id].page_numbers)
                ends.extend(blocks[block_id].page_numbers)
            for child_id in node.child_ids:
                child_range = nodes[child_id].pages
                if child_range:
                    starts.append(child_range.start)
                    ends.append(child_range.end)
            node.pages = BookMapPageRange(start=min(starts), end=max(ends))
        node.child_ids.sort(key=lambda child_id: _node_order(nodes[child_id]))

    owned_block_ids = {block_id for node in nodes.values() for block_id in node.source_block_ids}
    if owned_block_ids != set(blocks):
        raise ValueError("Book Map V4 must assign every normalized source block exactly once")
    for node in nodes.values():
        if node.resolution_status == "resolved" and not node.validation_warnings:
            node.resolution_status = "validated"

    tree = BookMapV4(
        document_id=document_id, source_sha256=source_sha256, root_id=root.node_id,
        resolver_version=RESOLVER_VERSION, chunking_version=CHUNKING_VERSION,
        nodes=list(nodes.values()), evidence=list(evidence.values()),
        quality=BookMapQuality(
            node_count=len(nodes), source_block_count=len(blocks), owned_block_count=len(owned_block_ids),
            unresolved_node_ids=[node.id for node in nodes.values() if node.resolution_status == "unresolved"],
            uncertain_node_ids=[node.id for node in nodes.values() if node.resolution_status == "uncertain"],
            extracted_page_count=sum(item.coverage == "extracted" for item in ownership.page_accounting),
            unknown_page_count=sum(item.coverage == "unknown" for item in ownership.page_accounting),
            coverage=(len(owned_block_ids) / len(blocks)) if blocks else 1.0,
            section_count=sum(node.node_type in {"part", "chapter", "section", "appendix"} for node in nodes.values()),
            block_count=len(blocks), mapped_block_count=len(owned_block_ids),
            unmapped_block_count=max(0, len(blocks) - len(owned_block_ids)),
            uncertain_section_ids=[node.id for node in nodes.values()
                if node.node_type in {"part", "chapter", "section", "appendix"}
                and node.resolution_status != "validated"],
            table_node_count=sum(node.node_type == "table" for node in nodes.values()),
            image_node_count=sum(node.node_type == "image" for node in nodes.values()),
            unparsed_page_count=len(ownership.unparsed_pages),
            validation_warning_count=sum(len(node.validation_warnings) for node in nodes.values()),
        ), index_alignment=index_alignment,
    )
    require_valid_book_map(tree)
    return tree
