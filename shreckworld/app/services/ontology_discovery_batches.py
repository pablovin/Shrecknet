"""Chapter-aware, non-lossy batches for combined ontology and schema learning.

A chapter is the semantic container; each model request is an adaptive window
inside that chapter. Bookmap subsections and block IDs survive into the request.
Content is never truncated or duplicated merely to fit the batching budget.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any
import math
import re

from app.services.ontology_compact import model_window

from app.schemas.book_map import BookMapV4
from app.schemas.shreckworld import BookMapRead

# Approximately 6K text tokens at four characters/token. The JSON headings,
# evidence IDs and instructions are additional input tokens, not part of this cap.
MAX_BATCH_CHARS = 8500
MAX_INPUT_TOKENS = 3000
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
    max_input_tokens: int = MAX_INPUT_TOKENS,
) -> list[dict]:
    """Return one LLM request per chapter, or several windows for long chapters.

    Each request contains one chapter title plus one or more subsection groups.
    Repeated source_block_ids in the Bookmap never duplicate the source text.
    Extremely large blocks receive fragment_index, so no text is truncated.
    """
    if max_chars < 256:
        raise ValueError("max_chars must be at least 256")
    if max_input_tokens < 256:
        raise ValueError("max_input_tokens must be at least 256")
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

    # Conservative estimate on the *actual compact serialized prompt*, rather
    # than source characters alone. English prose, Unicode, table punctuation
    # and JSON separators contribute differently. This is a soft estimate, not
    # provider-verified billing; model usage is captured separately.
    def estimate_tokens(payload: str) -> int:
        words = len(re.findall(r"\w+|[^\w\s]", payload))
        return max(math.ceil(len(payload.encode("utf-8")) / 3), words)

    def fits(batch: dict) -> bool:
        serialized, _ = model_window(batch, "Reference Source")
        return estimate_tokens(serialized) + 180 <= max_input_tokens

    def append_piece(owner: dict, block: Any, fragment: str, index: int) -> None:
        nonlocal current, size
        if current is None:
            current = {
                "chapter_id": owner["chapter_id"],
                "chapter_title": owner["chapter_title"], "sections": [],
            }
        if (not current["sections"] or
                current["sections"][-1]["section_id"] != owner["section_id"]):
            current["sections"].append({
                "section_id": owner["section_id"],
                "heading_path": list(owner["heading_path"]),
                "pages": dict(owner["pages"]),
                "section_type": owner["section_type"], "blocks": [],
            })
        current["sections"][-1]["blocks"].append({
            "id": block.id, "type": block.block_type, "text": fragment,
            "fragment_index": index,
        })
        size += len(fragment)

    for block in rows:
        body = block.display_text or ""
        if not body.strip():
            continue
        owner = owners.get(block.id, default)
        # Track fragment positions even if a long paragraph must be divided by
        # the token-aware budget. No source characters are removed or overlapped.
        pending = list(_fragments(body, min(max_chars, max_input_tokens * 2)))
        fragment_index = 0
        while pending:
            fragment = pending.pop(0)
            if current is not None and (current["chapter_id"] != owner["chapter_id"]
                                         or size + len(fragment) > max_chars):
                flush()
            append_piece(owner, block, fragment, fragment_index)
            if not fits(current):
                # First try a new window at the same chapter boundary.
                # If one fragment alone overflows, split it deterministically.
                current["sections"][-1]["blocks"].pop()
                size -= len(fragment)
                if not current["sections"][-1]["blocks"]:
                    current["sections"].pop()
                if current["sections"]:
                    flush()
                    pending.insert(0, fragment)
                    continue
                current = None
                size = 0
                if len(fragment) < 2:
                    raise ValueError("single_fragment_exceeds_input_budget")
                middle = len(fragment) // 2
                pending.insert(0, fragment[middle:])
                pending.insert(0, fragment[:middle])
                continue
            fragment_index += 1
    flush()
    return batches



# Only overflowing extraction windows are recursively subdivided. The original
# Bookmap chapter, section headings and block provenance remain unchanged.
MIN_ADAPTIVE_FRAGMENT_CHARS = 96


def split_discovery_batch(batch: dict, *, min_fragment_chars: int = MIN_ADAPTIVE_FRAGMENT_CHARS
                          ) -> tuple[dict, dict]:
    """Split one dense discovery batch without losing/duplicating source text.

    Prefer subsection boundaries; then persisted blocks; only split inside a
    single source fragment if no structural boundaries remain. A child is a
    normal discovery batch and can itself be split on a later overflow.
    """
    sections = batch["sections"]
    if not sections:
        raise ValueError("empty_discovery_batch")

    def chars(section: dict) -> int:
        return sum(len(block["text"]) for block in section["blocks"])

    def best_boundary(weights: list[int]) -> int:
        total, prefix = sum(weights), 0
        choices = []
        for index, weight in enumerate(weights[:-1], start=1):
            prefix += weight
            choices.append((abs(total - 2 * prefix), index))
        return min(choices)[1]

    def child(parts: list[dict]) -> dict:
        return {"chapter_id": batch["chapter_id"], "chapter_title": batch["chapter_title"],
                "sections": parts}

    if len(sections) > 1:
        cut = best_boundary([chars(section) for section in sections])
        return child(sections[:cut]), child(sections[cut:])

    section = sections[0]
    blocks = section["blocks"]
    if not blocks:
        raise ValueError("empty_discovery_section")
    if len(blocks) > 1:
        cut = best_boundary([len(block["text"]) for block in blocks])
        return child([{**section, "blocks": blocks[:cut]}]), child(
            [{**section, "blocks": blocks[cut:]}])

    block = blocks[0]
    text = block["text"]
    if len(text) < 2 * min_fragment_chars:
        raise ValueError("discovery_fragment_too_small_to_split")

    midpoint = len(text) // 2
    # Prefer nearby paragraph/word boundaries without making a very small child.
    lower, upper = min_fragment_chars, len(text) - min_fragment_chars
    candidates = [pos + 1 for pos, letter in enumerate(text)
                  if letter.isspace() and lower <= pos + 1 <= upper]
    cut = min(candidates, key=lambda pos: abs(pos - midpoint)) if candidates else midpoint
    if not (0 < cut < len(text)):
        raise ValueError("discovery_fragment_too_small_to_split")
    offset = block.get("fragment_char_offset", 0)
    left = {**block, "text": text[:cut], "fragment_char_offset": offset}
    right = {**block, "text": text[cut:], "fragment_char_offset": offset + cut}
    return child([{**section, "blocks": [left]}]), child([{**section, "blocks": [right]}])
