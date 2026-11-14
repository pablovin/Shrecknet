"""Chapter-aware, non-lossy batches for combined ontology and schema learning.

A chapter is the semantic container; each model request is an adaptive window
inside that chapter. Bookmap subsections and block IDs survive into the request.
Content is never truncated or duplicated merely to fit the batching budget.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.schemas.book_map import BookMapV4
from app.schemas.shreckworld import BookMapRead

# Approximately 6K text tokens at four characters/token. The JSON headings,
# evidence IDs and instructions are additional input tokens, not part of this cap.
MAX_BATCH_CHARS = 24000
STRUCTURAL = {"document", "part", "chapter", "section", "appendix", "contents"}
CHAPTER_BOUNDARIES = {"chapter", "appendix"}
POSITION_FALLBACK = 10**9


def _page_number(block: Any) -> int:
    for item in (getattr(block, "provenance", None) or []):
        if isinstance(item, dict):
            for name in ("page_number", "physical_page", "page_no"):
                value = item.get(name)
                if isinstance(value, int) and value >= 1:
                    return value
    return POSITION_FALLBACK


def _fragments(text: str, maximum: int) -> Iterable[str]:
    """Split oversized source blocks without dropping text or adding overlap."""
    pos = 0
    while pos < len(text):
        end = min(len(text), pos + maximum)
        if end < len(text):
            boundary = max(
                text.rfind("\n", pos + maximum // 2, end),
                text.rfind(" ", pos + maximum // 2, end),
            )
            if boundary > pos:
                end = boundary + 1
        yield text[pos:end]
        pos = end


def _structure_for_block(mapped: BookMapV4 | BookMapRead) -> tuple[dict, dict]:
    """Choose the deepest actual section for each block, plus its chapter ancestor.

    V3 sections may repeat descendants; prefer the deepest node. V4 structural
    and content nodes repeat block ownership; content nodes win where available.
    """
    owners: dict[str, tuple[tuple[int, int, int], dict]] = {}
    positions: dict[str, tuple[int, int]] = {}

    if isinstance(mapped, BookMapV4):
        nodes = {node.id: node for node in mapped.nodes}

        def structural_owner(node):
            parent = node if node.node_type in STRUCTURAL else nodes.get(
                node.structural_node_id or node.parent_id
            )
            visited = set()
            while parent is not None and parent.node_type not in STRUCTURAL:
                if parent.id in visited:
                    return None
                visited.add(parent.id)
                parent = nodes.get(parent.parent_id)
            return parent

        def chapter_owner(node):
            parent = node
            visited = set()
            fallback = node
            while parent is not None and parent.id not in visited:
                visited.add(parent.id)
                if parent.node_type in CHAPTER_BOUNDARIES:
                    return parent
                if parent.node_type == "part":
                    fallback = parent
                parent = nodes.get(parent.parent_id)
            return fallback

        for node in mapped.nodes:
            owner = structural_owner(node)
            if owner is None:
                continue
            chapter = chapter_owner(owner)
            quality = (2 if node.node_type not in STRUCTURAL else 1, owner.level, node.level)
            context = {
                "chapter_id": chapter.id,
                "chapter_title": chapter.title or chapter.node_type.title(),
                "section_id": owner.id,
                "heading_path": list(owner.heading_path),
                "pages": owner.pages.model_dump() if owner.pages else {},
                "section_type": owner.node_type,
            }
            for block_id in node.source_block_ids:
                current = owners.get(block_id)
                if current is None or quality > current[0]:
                    owners[block_id] = (quality, context)
                position = (node.position.physical_page, node.position.reading_order)
                if node.node_type not in STRUCTURAL or block_id not in positions:
                    positions[block_id] = position
    else:
        nodes = {node.id: node for node in mapped.sections}

        def chapter_owner(node):
            parent = node
            visited = set()
            while parent is not None and parent.id not in visited:
                visited.add(parent.id)
                if parent.node_type == "chapter":
                    return parent
                parent = nodes.get(parent.parent_id)
            # Fallback isolates an orphaned subsection rather than mixing it
            # with unrelated parts of the book.
            return node

        for node in mapped.sections:
            chapter = chapter_owner(node)
            quality = (node.level, 1 if node.node_type == "section" else 0, 0)
            context = {
                "chapter_id": chapter.id,
                "chapter_title": chapter.title,
                "section_id": node.id,
                "heading_path": list(node.heading_path),
                "pages": node.pages.model_dump(),
                "section_type": node.node_type,
            }
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
) -> list[dict]:
    """Return one LLM request per chapter, or several windows for long chapters.

    Each request contains one chapter title plus one or more subsection groups.
    Repeated source_block_ids in the Bookmap never duplicate the source text.
    Extremely large blocks receive fragment_index, so no text is truncated.
    """
    if max_chars < 256:
        raise ValueError("max_chars must be at least 256")
    owners, positions = _structure_for_block(mapped)
    default = {
        "chapter_id": "unmapped",
        "chapter_title": "Unmapped content",
        "section_id": None,
        "heading_path": ["Unmapped content"],
        "pages": {},
        "section_type": "unmapped",
    }
    rows = sorted(
        blocks.values(),
        key=lambda block: (
            *(positions.get(block.id) or (_page_number(block), block.reading_order)),
            block.id,
        ),
    )
    batches: list[dict] = []
    current: dict | None = None
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current is not None and current["sections"]:
            batches.append(current)
        current = None
        size = 0

    for block in rows:
        body = block.display_text or ""
        if not body.strip():
            continue
        owner = owners.get(block.id, default)
        for index, fragment in enumerate(_fragments(body, max_chars)):
            if current is not None and (
                current["chapter_id"] != owner["chapter_id"] or
                size + len(fragment) > max_chars
            ):
                flush()
            if current is None:
                current = {
                    "chapter_id": owner["chapter_id"],
                    "chapter_title": owner["chapter_title"],
                    "sections": [],
                }
            if (not current["sections"] or
                    current["sections"][-1]["section_id"] != owner["section_id"]):
                current["sections"].append({
                    "section_id": owner["section_id"],
                    "heading_path": list(owner["heading_path"]),
                    "pages": dict(owner["pages"]),
                    "section_type": owner["section_type"],
                    "blocks": [],
                })
            current["sections"][-1]["blocks"].append({
                "id": block.id,
                "type": block.block_type,
                "text": fragment,
                "fragment_index": index,
            })
            size += len(fragment)
    flush()
    return batches
