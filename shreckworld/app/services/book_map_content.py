"""Bounded, generation-pinned source text reads for Book Map nodes."""
from __future__ import annotations

import base64
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (ShreckWorldDocument, ShreckWorldDocumentBlock,
                        ShreckWorldDocumentBookMap)
from app.schemas.book_map import BookMapNode, BookMapV4
from app.services.book_map_contract import read_book_map
from shreckdoc.structure_types import TableCell, TableStructure
from shreckdoc.table_text import serialize_table_text


class BookMapContentError(ValueError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


def _source_position(block: ShreckWorldDocumentBlock, node: BookMapNode) -> dict[str, int]:
    pages = [entry.get("page_no") for entry in block.provenance if entry.get("page_no")]
    return {"physical_page": min(pages, default=node.position.physical_page),
            "reading_order": block.reading_order}


def read_node_content(session: Session, *, document: ShreckWorldDocument,
                      node_id: str, limit: int = 50, cursor: str | None = None,
                      source_sha256: str | None = None) -> dict:
    if not 1 <= limit <= 100:
        raise BookMapContentError(422, "limit must be between 1 and 100")
    if source_sha256 is not None and source_sha256 != document.source_sha256:
        raise BookMapContentError(409, "Source revision changed")
    record = session.get(ShreckWorldDocumentBookMap, document.id)
    if record is None:
        raise BookMapContentError(404, "Book Map not found")
    try:
        parsed = read_book_map(record.payload)
    except Exception as exc:
        raise BookMapContentError(409, "Stored Book Map is invalid") from exc
    if not isinstance(parsed, BookMapV4):
        raise BookMapContentError(422, "Node content requires Book Map V4")
    nodes = {node.id: node for node in parsed.nodes}
    node = nodes.get(node_id)
    if node is None:
        raise BookMapContentError(404, "Book Map node not found")
    digest = hashlib.sha256(json.dumps(record.payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    offset = 0
    character_offset = 0
    child_offset = 0
    if cursor:
        try:
            token = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        except Exception as exc:
            raise BookMapContentError(422, "Invalid content cursor") from exc
        expected = (document.id, document.source_sha256, node_id, digest)
        observed = tuple(token.get(key) for key in ("document_id", "source_sha256", "node_id", "map_digest"))
        if observed != expected:
            raise BookMapContentError(409, "Content cursor belongs to a stale document or map revision")
        offset = token.get("offset", 0)
        character_offset = token.get("character_offset", 0)
        child_offset = token.get("child_offset", 0)
        if not isinstance(offset, int) or offset < 0:
            raise BookMapContentError(422, "Invalid content cursor")
        if not isinstance(character_offset, int) or character_offset < 0:
            raise BookMapContentError(422, "Invalid content cursor")
        if not isinstance(child_offset, int) or child_offset < 0:
            raise BookMapContentError(422, "Invalid content cursor")

    all_child_summaries = [{"id": nodes[child].id, "node_type": nodes[child].node_type,
        "title": nodes[child].title, "pages": nodes[child].pages.model_dump() if nodes[child].pages else None,
        "position": nodes[child].position.model_dump()}
        for child in node.child_ids]
    child_summaries = all_child_summaries[child_offset:child_offset + limit]
    block_ids = node.source_block_ids
    rows = list(session.scalars(select(ShreckWorldDocumentBlock).where(
        ShreckWorldDocumentBlock.document_id == document.id,
        ShreckWorldDocumentBlock.id.in_(block_ids)).order_by(ShreckWorldDocumentBlock.reading_order))) if block_ids else []
    items = []
    for block in rows:
        table = block.block_metadata.get("table_structure") if isinstance(block.block_metadata, dict) else None
        if block.block_type == "table" and isinstance(table, dict) and table.get("cells"):
            try:
                structure = TableStructure(**{**table, "cells": [TableCell(**cell) for cell in table["cells"]]})
                serialized = serialize_table_text(structure, fallback_text=block.display_text)
                lines = [line for line in serialized.text.splitlines() if line.strip()]
                header = lines[:2] if len(lines) > 1 and lines[1].lstrip().startswith("|") else lines[:1]
                for row_group in serialized.row_groups:
                    items.append({"block_id": block.id, "content_type": "table_row_group",
                        "page_numbers": [entry.get("page_no") for entry in block.provenance if entry.get("page_no")],
                        "position": _source_position(block, node),
                        "bounding_boxes": [entry.get("bbox") for entry in block.provenance if entry.get("bbox")],
                        "media_ref": None,
                        "text": "\n".join([*header, *row_group])})
                if serialized.row_groups:
                    continue
            except (TypeError, ValueError):
                pass
        items.append({"block_id": block.id, "content_type": block.block_type,
                      "page_numbers": [entry.get("page_no") for entry in block.provenance if entry.get("page_no")],
                      "position": _source_position(block, node),
                      "bounding_boxes": [entry.get("bbox") for entry in block.provenance if entry.get("bbox")],
                      "media_ref": (f"p{next((entry.get('page_no') for entry in block.provenance if entry.get('page_no')), node.position.physical_page)}-thumbnail"
                                    if block.block_type in {"picture", "image"} else None),
                      "text": block.display_text})
    page = items[offset:offset + limit]
    if page and character_offset:
        page[0]["text"] = page[0]["text"][character_offset:]
    body = {"document_id": document.id, "source_sha256": document.source_sha256,
            "node_id": node_id, "items": page, "children": child_summaries,
            "next_cursor": None}
    continuation_offset = offset + len(page)
    continuation_text_offset = 0
    continuation_child_offset = child_offset + len(child_summaries)
    encoded_size = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    if encoded_size > 256 * 1024:
        # Bound text while keeping a stable character offset for a continuation.
        for item_index, item in reversed(list(enumerate(page))):
            text = item["text"]
            while text and len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > 256 * 1024:
                text = text[: max(0, int(len(text) * 0.75))]
                item["text"] = text
            if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) <= 256 * 1024:
                original_offset = character_offset if item_index == 0 else 0
                item["text_offset"] = original_offset
                item["text_truncated"] = True
                continuation_offset = offset + item_index
                continuation_text_offset = original_offset + len(text)
                break
    if continuation_offset < len(items) or continuation_child_offset < len(all_child_summaries):
        payload = {"document_id": document.id, "source_sha256": document.source_sha256,
                   "node_id": node_id, "map_digest": digest, "offset": continuation_offset,
                   "character_offset": continuation_text_offset,
                   "child_offset": continuation_child_offset}
        body["next_cursor"] = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > 256 * 1024:
        raise BookMapContentError(422, "Node metadata exceeds the content response limit")
    return body
