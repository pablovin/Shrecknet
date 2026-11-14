"""Bookmap-aware, non-lossy batches for combined ontology discovery and schema extraction.

Each persisted document block is assigned to the deepest available Bookmap subsection.
The outline is used for context; no model reads a block twice. Large subsections are
split by blocks, and an oversized single block is split into contiguous text fragments.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.schemas.book_map import BookMapV4
from app.schemas.shreckworld import BookMapRead

MAX_BATCH_CHARS = 6000
STRUCTURAL = {"document", "part", "chapter", "section", "appendix", "contents"}
POSITION_FALLBACK = 10**9


def _page_number(block: Any) -> int:
    """Use the parser provenance for V3 and unassigned blocks."""
    for item in (getattr(block, "provenance", None) or []):
        if isinstance(item, dict):
            for name in ("page_number", "physical_page", "page_no"):
                value = item.get(name)
                if isinstance(value, int) and value >= 1:
                    return value
    return POSITION_FALLBACK


def _fragments(text: str, maximum: int) -> Iterable[str]:
    """Split a single large block without discarding any characters."""
    pos = 0
    while pos < len(text):
        end = min(len(text), pos + maximum)
        if end < len(text):
            boundary = max(text.rfind("\n", pos + maximum // 2, end),
                           text.rfind(" ", pos + maximum // 2, end))
            if boundary > pos:
                end = boundary + 1
        yield text[pos:end]
        pos = end


def _structure_for_block(mapped: BookMapV4 | BookMapRead) -> tuple[dict, dict]:
    """Return best structural owner and source-position per block ID.

    V3 `source_block_ids` may include descendant blocks, so the deepest section
    wins. V4 content nodes own one source block and explicitly identify the
    structural owner; they take priority over structural aggregation.
    """
    owners: dict[str, tuple[tuple[int, int, int], dict]] = {}
    positions: dict[str, tuple[int, int]] = {}
    if isinstance(mapped, BookMapV4):
        nodes = {node.id: node for node in mapped.nodes}
        def structural_owner(node):
            parent = node if node.node_type in STRUCTURAL else nodes.get(
                node.structural_node_id or node.parent_id)
            visited = set()
            while parent and parent.node_type not in STRUCTURAL:
                if parent.id in visited:
                    return None
                visited.add(parent.id)
                parent = nodes.get(parent.parent_id)
            return parent

        for node in mapped.nodes:
            owner = structural_owner(node)
            if owner is None:
                continue
            quality = (2 if node.node_type not in STRUCTURAL else 1, owner.level, node.level)
            context = {"section_id": owner.id, "heading_path": owner.heading_path,
                       "pages": owner.pages.model_dump() if owner.pages else {},
                       "section_type": owner.node_type}
            for block_id in node.source_block_ids:
                current = owners.get(block_id)
                if current is None or quality > current[0]:
                    owners[block_id] = (quality, context)
                position = (node.position.physical_page, node.position.reading_order)
                if node.node_type not in STRUCTURAL or block_id not in positions:
                    positions[block_id] = position
    else:
        for node in mapped.sections:
            quality = (node.level, 1 if node.node_type == "section" else 0, 0)
            context = {"section_id": node.id, "heading_path": node.heading_path,
                       "pages": node.pages.model_dump(), "section_type": node.node_type}
            for block_id in node.source_block_ids:
                current = owners.get(block_id)
                if current is None or quality > current[0]:
                    owners[block_id] = (quality, context)
    return {block_id: entry[1] for block_id, entry in owners.items()}, positions


def build_discovery_batches(
    mapped: BookMapV4 | BookMapRead,
    blocks: dict[str, Any],
    *,
    max_chars: int = MAX_BATCH_CHARS,
) -> list[list[dict]]:
    """Emit context-preserving, non-overlapping passage batches.

    Each batch contains one section group to avoid mixing unrelated subsections.
    Tiny sections remain independently attributable. An unmapped block is retained
    under an explicit fallback heading instead of being silently omitted.
    """
    if max_chars < 256:
        raise ValueError("max_chars must be at least 256")
    owners, positions = _structure_for_block(mapped)
    default = {"section_id": None, "heading_path": ["Unmapped content"],
               "pages": {}, "section_type": "unmapped"}
    rows = sorted(blocks.values(), key=lambda block: (
        *(positions.get(block.id) or (_page_number(block), block.reading_order)),
        block.id))
    result: list[list[dict]] = []
    current: dict | None = None
    size = 0

    def flush():
        nonlocal current, size
        if current is not None and current["blocks"]:
            result.append([current])
        current = None
        size = 0

    for block in rows:
        text = block.display_text or ""
        if not text.strip():
            continue
        owner = owners.get(block.id, default)
        for index, fragment in enumerate(_fragments(text, max_chars)):
            if (current is not None and
                (current["section_id"] != owner["section_id"] or
                 size + len(fragment) > max_chars)):
                flush()
            if current is None:
                current = {"section_id": owner["section_id"],
                           "heading_path": list(owner["heading_path"]),
                           "pages": dict(owner["pages"]),
                           "section_type": owner["section_type"], "blocks": []}
            current["blocks"].append({
                "id": block.id, "text": fragment, "type": block.block_type,
                "fragment_index": index,
            })
            size += len(fragment)
    flush()
    return result
