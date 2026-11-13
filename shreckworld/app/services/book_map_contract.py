"""Version-aware Book Map readers and the compatibility-only V4 to V3 view."""
from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from app.schemas.book_map import BookMapV4
from app.schemas.shreckworld import BookMapRead
from shreckdoc.structure_validation import (
    StructureValidationError, descendants, require_valid_book_map,
)


class UnsupportedBookMapVersion(ValueError):
    pass


def read_book_map(payload: Mapping[str, object]) -> BookMapRead | BookMapV4:
    """Validate a stored V3 or V4 payload without changing its representation."""
    version = payload.get("map_version")
    if version == 3:
        return BookMapRead.model_validate(payload)
    if version == 4:
        tree = BookMapV4.model_validate(payload)
        require_valid_book_map(tree)
        return tree
    raise UnsupportedBookMapVersion(f"unsupported Book Map version: {version!r}")


def project_v4_to_v3(tree: BookMapV4) -> dict[str, object]:
    """Project V4 into the legacy navigation contract without mutating ownership.

    V3 cannot express confidence provenance. Its scalar is derived only from the
    resolution enum, and `verified` is reserved for V4 `validated` nodes.
    """
    require_valid_book_map(tree)
    by_id = {node.id: node for node in tree.nodes}
    visible_types = {"part", "chapter", "section", "appendix", "table", "image"}
    visible = [node for node in tree.nodes if node.node_type in visible_types]
    v3_id: dict[str, str] = {node.id: node.id for node in visible}
    node_sections: list[dict[str, object]] = []
    all_block_ids = {block_id for node in tree.nodes for block_id in node.source_block_ids}
    aggregates: dict[str, tuple[list[str], list[str], list[str]]] = {}
    structural_types = {"document", "part", "chapter", "section", "appendix", "contents"}
    for node_id in reversed(descendants(tree, tree.root_id, include_self=True)):
        node = by_id[node_id]
        blocks = list(node.source_block_ids)
        chunks = list(node.chunk_ids)
        content_types = [] if node.node_type in structural_types else [node.node_type]
        for child_id in node.child_ids:
            child_blocks, child_chunks, child_types = aggregates[child_id]
            blocks.extend(child_blocks)
            chunks.extend(child_chunks)
            content_types.extend(child_types)
        aggregates[node_id] = (
            list(dict.fromkeys(blocks)),
            list(dict.fromkeys(chunks)),
            list(dict.fromkeys(content_types)),
        )

    for node in sorted(visible, key=lambda value: (
        value.position.physical_page, value.position.reading_order, value.level, value.id,
    )):
        blocks, chunks, content_types = aggregates[node.id]
        parent_id = node.parent_id
        while parent_id is not None and parent_id not in v3_id:
            parent = by_id.get(parent_id)
            parent_id = parent.parent_id if parent else None
        captions = [block_id for caption_id in node.caption_node_ids
                    if caption_id in by_id for block_id in by_id[caption_id].source_block_ids]
        status = node.resolution_status
        legacy_status = "verified" if status == "validated" else "unresolved" if status == "unresolved" else "tentative"
        legacy_confidence = {"validated": 1.0, "resolved": 0.75, "detected": 0.5,
                             "uncertain": 0.25, "unresolved": 0.0}[status]
        start = node.pages.start if node.pages else node.position.physical_page
        end = node.pages.end if node.pages else node.position.physical_page
        start_locator = next((locator for locator in node.page_locators if locator.physical_page == start), None)
        end_locator = next((locator for locator in reversed(node.page_locators) if locator.physical_page == end), None)
        node_sections.append({
            "id": node.id,
            "title": node.title or ("Untitled section" if node.node_type in {"part", "chapter", "section", "appendix", "contents"} else node.node_type.title()),
            "node_type": "chapter" if node.node_type in {"part", "chapter", "appendix"} else node.node_type,
            "confidence": legacy_confidence,
            "mapping_status": legacy_status,
            "level": node.level,
            "parent_id": v3_id.get(parent_id) if parent_id else None,
            "heading_path": node.heading_path,
            "pages": {"start": start, "end": end,
                      "start_label": start_locator.printed_label if start_locator else None,
                      "end_label": end_locator.printed_label if end_locator else None},
            "block_ids": blocks,
            "source_block_ids": blocks,
            "caption_block_ids": captions,
            "chunk_ids": chunks,
            "content_types": sorted(set(content_types)),
        })

    projected_block_ids = {block_id for node in visible for block_id in aggregates[node.id][0]}
    source_count = tree.quality.source_block_count
    owned_count = tree.quality.owned_block_count
    return {
        "document_id": tree.document_id,
        "map_version": 3,
        "sections": node_sections,
        "unmapped_block_ids": sorted(all_block_ids - projected_block_ids),
        "quality": {
            "section_count": len(node_sections),
            "block_count": source_count,
            "mapped_block_count": min(owned_count, source_count),
            "coverage": min(owned_count / source_count, 1.0) if source_count else 1.0,
            "unmapped_block_count": max(source_count - owned_count, 0),
            "uncertain_section_ids": [section["id"] for section in node_sections
                                       if section["mapping_status"] != "verified"],
        },
    }


def try_read_book_map(payload: Mapping[str, object]) -> tuple[BookMapRead | BookMapV4 | None, str | None]:
    """Return a typed representation or a stable validation diagnostic."""
    try:
        return read_book_map(payload), None
    except (ValidationError, StructureValidationError, UnsupportedBookMapVersion) as exc:
        return None, str(exc)
