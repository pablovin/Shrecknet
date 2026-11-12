"""Deterministic section maps built only from persisted parser/index structure."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models import (
    ShreckWorldDocument, ShreckWorldDocumentBlock, ShreckWorldDocumentBookMap,
    ShreckWorldDocumentChunk, ShreckWorldDocumentPage, ShreckWorldDocumentSection,
)

logger = logging.getLogger(__name__)
BOOK_MAP_VERSION = 3


def build_book_map(*, document_id: str, sections: list[Any], blocks: list[Any],
                   pages: list[Any], chunks: list[Any]) -> dict[str, Any]:
    """Map sections to extant blocks/chunks without semantic inference or text copies."""
    block_by_id = {block.id: block for block in blocks}
    blocks_by_section: dict[str, list[Any]] = defaultdict(list)
    for block in blocks:
        blocks_by_section[block.section_id].append(block)
    page_by_number = {page.page_number: page for page in pages}
    valid_page_numbers = set(page_by_number)
    section_ids = {section.id for section in sections}
    section_ranges: dict[str, tuple[int, int]] = {}
    for section_id, section_blocks in blocks_by_section.items():
        page_numbers = [int(entry["page_no"]) for block in section_blocks for entry in (block.provenance or [])
                        if str(entry.get("page_no", "")).isdigit() and int(entry["page_no"]) in valid_page_numbers]
        if page_numbers:
            section_ranges[section_id] = min(page_numbers), max(page_numbers)
    chunks_by_section: dict[str, list[Any]] = defaultdict(list)
    for chunk in chunks:
        if chunk.chunk_role != "child":
            continue
        target_sections = [chunk.section_id] if chunk.section_id in section_ids else []
        if not target_sections:
            source_ids = set(chunk.source_block_ids or [])
            target_sections = [section_id for section_id, section_blocks in blocks_by_section.items()
                               if source_ids.intersection(block.id for block in section_blocks)]
        if not target_sections and chunk.heading_path:
            target_sections = [section.id for section in sections if section.heading_path == chunk.heading_path]
        if not target_sections:
            page_number = chunk.page_number
            containing = [(end - start, section_id) for section_id, (start, end) in section_ranges.items()
                          if start <= page_number <= end]
            if containing:
                target_sections = [min(containing)[1]]
        for section_id in target_sections:
            chunks_by_section[section_id].append(chunk)
    mapped_sections = []
    mapped_block_ids: set[str] = set()
    def section_order(section: Any) -> tuple[int, int, str]:
        reading_orders = [block.reading_order for block in blocks_by_section.get(section.id, [])]
        return (section.heading_level, min(reading_orders) if reading_orders else 2**31, section.id)

    for section in sorted(sections, key=section_order):
        section_blocks = sorted(blocks_by_section.get(section.id, []), key=lambda value: value.reading_order)
        block_ids = [block.id for block in section_blocks]
        mapped_block_ids.update(block_ids)
        page_numbers = sorted({int(entry.get("page_no")) for block in section_blocks
                               for entry in (block.provenance or [])
                               if str(entry.get("page_no", "")).isdigit() and int(entry["page_no"]) > 0})
        invalid_pages = [number for number in page_numbers if number not in valid_page_numbers]
        if invalid_pages:
            raise ValueError("Book Map contains page references outside its document generation")
        mapped_chunks = sorted(chunks_by_section.get(section.id, []), key=lambda value: (value.ordinal, value.id))
        page_labels = {number: page_by_number[number].page_label for number in page_numbers if number in page_by_number}
        heading = (section.heading or "").strip()
        # Parser hierarchy is evidence, not ground truth. Keep weak sections
        # visible, but mark them unresolved instead of presenting a guess.
        has_heading_evidence = bool(heading and section.heading_path and section.heading_path[-1] == heading)
        has_page_evidence = bool(page_numbers)
        heading_block = next((block for block in section_blocks if block.block_type == "heading"
                              and block.display_text.strip() == heading), None)
        has_layout_evidence = bool(heading_block and any((entry.get("bbox") or {}) for entry in (heading_block.provenance or [])))
        parent_reference_valid = section.parent_section_id is None or section.parent_section_id in section_ids
        confidence = ((0.55 if has_heading_evidence else 0.0)
                      + (0.15 if has_page_evidence else 0.0)
                      + (0.15 if block_ids else 0.0)
                      + (0.15 if has_layout_evidence else 0.0))
        if not parent_reference_valid:
            confidence = min(confidence, 0.49)
        mapping_status = "verified" if confidence >= 0.9 else "tentative" if confidence >= 0.6 else "unresolved"
        section_node_id = section.id
        mapped_sections.append({
            "id": section_node_id, "title": heading or "Unresolved section",
            "node_type": "chapter" if section.heading_level <= 1 else "section",
            "confidence": round(confidence, 2), "mapping_status": mapping_status,
            "level": section.heading_level, "parent_id": section.parent_section_id if section.parent_section_id in section_ids else None,
            "heading_path": section.heading_path or [],
            "pages": {"start": min(page_numbers) if page_numbers else None,
                      "end": max(page_numbers) if page_numbers else None,
                      "start_label": page_labels.get(min(page_numbers)) if page_numbers else None,
                      "end_label": page_labels.get(max(page_numbers)) if page_numbers else None},
            "block_ids": block_ids, "source_block_ids": block_ids, "caption_block_ids": [],
            "chunk_ids": [chunk.id for chunk in mapped_chunks],
            "content_types": sorted({block.block_type for block in section_blocks}),
        })
        # Tables and images are first-class nodes. Captions remain normal text
        # blocks/chunks and are linked to images by unique page/order proximity.
        pictures = [block for block in section_blocks if block.block_type == "picture"]
        captions = [block for block in section_blocks if block.block_type == "caption" and block.display_text.strip()]
        captions_by_picture: dict[str, list[Any]] = defaultdict(list)
        for caption in captions:
            caption_pages = {int(entry["page_no"]) for entry in (caption.provenance or [])
                             if str(entry.get("page_no", "")).isdigit()}
            candidates = []
            for picture in pictures:
                picture_pages = {int(entry["page_no"]) for entry in (picture.provenance or [])
                                 if str(entry.get("page_no", "")).isdigit()}
                distance = abs(caption.reading_order - picture.reading_order)
                if caption_pages.intersection(picture_pages) and distance <= 3:
                    candidates.append((distance, picture))
            if candidates:
                nearest_distance = min(distance for distance, _ in candidates)
                nearest = [picture for distance, picture in candidates if distance == nearest_distance]
                if len(nearest) == 1:
                    captions_by_picture[nearest[0].id].append(caption)

        for block in section_blocks:
            if block.block_type not in {"table", "picture"}:
                continue
            content_node = block.block_type == "table"
            node_type = "table" if content_node else "image"
            node_id = str(uuid5(NAMESPACE_URL, f"shreckworld:book-map-{node_type}:{document_id}:{block.id}"))
            node_blocks = [block, *captions_by_picture.get(block.id, [])] if not content_node else [block]
            node_block_ids = [value.id for value in node_blocks]
            node_pages = sorted({int(entry["page_no"]) for value in node_blocks for entry in (value.provenance or [])
                                 if str(entry.get("page_no", "")).isdigit() and int(entry["page_no"]) > 0})
            node_page = node_pages[0] if node_pages else None
            node_labels = [page_by_number[number].page_label for number in node_pages if number in page_by_number]
            node_chunks = sorted((chunk for chunk in chunks if chunk.chunk_role == "child"
                                  and set(getattr(chunk, "source_block_ids", []) or []).intersection(node_block_ids)),
                                 key=lambda value: (value.ordinal, value.id))
            caption_text = " ".join(value.display_text.strip() for value in node_blocks[1:]).strip()
            title = ((block.display_text or "Table").splitlines()[0][:120] or "Table") if content_node else (caption_text[:120] or "Image")
            mapped_sections.append({
                "id": node_id, "title": title,
                "node_type": node_type, "confidence": 0.95 if node_page else 0.7,
                "mapping_status": "verified" if node_page else "tentative",
                "level": section.heading_level + 1, "parent_id": section_node_id,
                "heading_path": [*(section.heading_path or []), "Figure" if node_type == "image" else "Table"],
                "pages": {"start": node_page, "end": node_pages[-1] if node_pages else None,
                          "start_label": page_by_number[node_page].page_label if node_page in page_by_number else None,
                          "end_label": page_by_number[node_pages[-1]].page_label if node_pages and node_pages[-1] in page_by_number else None},
                "block_ids": node_block_ids, "source_block_ids": node_block_ids,
                "caption_block_ids": [value.id for value in node_blocks[1:]],
                "chunk_ids": [chunk.id for chunk in node_chunks],
                "content_types": ["table"] if content_node else ["picture", *( ["caption"] if len(node_blocks) > 1 else [])],
            })
    missing_blocks = sorted(block.id for block in blocks if block.id not in mapped_block_ids)
    mapped_count = len(mapped_block_ids)
    payload = {
        "document_id": document_id, "map_version": BOOK_MAP_VERSION, "sections": mapped_sections,
        "unmapped_block_ids": missing_blocks,
        "quality": {"section_count": len(mapped_sections), "block_count": len(blocks),
                    "mapped_block_count": mapped_count,
                    "coverage": mapped_count / len(blocks) if blocks else 1.0,
                    "unmapped_block_count": len(missing_blocks),
                    "uncertain_section_ids": [section["id"] for section in mapped_sections
                                               if section["mapping_status"] != "verified"]},
    }
    # Validate all references against this generation before a map is publishable.
    known_chunk_ids = {chunk.id for chunk in chunks}
    if any(block_id not in block_by_id for section in mapped_sections for block_id in section["block_ids"]):
        raise ValueError("Book Map contains a block reference outside its document generation")
    if any(chunk_id not in known_chunk_ids for section in mapped_sections for chunk_id in section["chunk_ids"]):
        raise ValueError("Book Map contains a chunk reference outside its document generation")
    return payload


def build_persisted_book_map(session: Session, document: ShreckWorldDocument) -> ShreckWorldDocumentBookMap:
    sections = list(session.scalars(select(ShreckWorldDocumentSection).where(
        ShreckWorldDocumentSection.document_id == document.id)))
    blocks = list(session.scalars(select(ShreckWorldDocumentBlock).where(
        ShreckWorldDocumentBlock.document_id == document.id)))
    pages = list(session.scalars(select(ShreckWorldDocumentPage).where(
        ShreckWorldDocumentPage.document_id == document.id)))
    chunk_query = select(ShreckWorldDocumentChunk).where(ShreckWorldDocumentChunk.document_id == document.id)
    if document.is_active:
        # Legacy indexed chunks predate the document_id column. They belong to
        # the active generation only when no generation assignment was stored.
        from sqlalchemy import or_
        chunk_query = select(ShreckWorldDocumentChunk).where(or_(
            ShreckWorldDocumentChunk.document_id == document.id,
            (ShreckWorldDocumentChunk.document_id.is_(None) &
             (ShreckWorldDocumentChunk.library_item_id == document.library_item_id)),
        ))
    chunks = list(session.scalars(chunk_query))
    payload = build_book_map(document_id=document.id, sections=sections, blocks=blocks, pages=pages, chunks=chunks)
    record = session.get(ShreckWorldDocumentBookMap, document.id)
    if record is None:
        record = ShreckWorldDocumentBookMap(document_id=document.id, map_version=payload["map_version"], payload=payload)
        session.add(record)
    else:
        record.map_version, record.payload = payload["map_version"], payload
    return record


def has_missing_book_maps(session: Session) -> bool:
    current = exists(select(ShreckWorldDocumentBookMap.document_id).where(
        ShreckWorldDocumentBookMap.document_id == ShreckWorldDocument.id,
        ShreckWorldDocumentBookMap.map_version >= BOOK_MAP_VERSION))
    return bool(session.scalar(select(exists(select(ShreckWorldDocument.id).where(~current)))))


def backfill_missing_book_maps(*, batch_size: int = 100, dry_run: bool = False) -> dict[str, int]:
    """Idempotently map persisted generations, committing each item independently."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    from app.db.session import session_factory
    session = session_factory()()
    created = skipped = failed = 0
    try:
        ids = list(session.scalars(select(ShreckWorldDocument.id).outerjoin(
            ShreckWorldDocumentBookMap,
            ShreckWorldDocumentBookMap.document_id == ShreckWorldDocument.id,
        ).where((ShreckWorldDocumentBookMap.document_id.is_(None)) |
                (ShreckWorldDocumentBookMap.map_version < BOOK_MAP_VERSION))
            .order_by(ShreckWorldDocument.created_at)))
        for offset in range(0, len(ids), batch_size):
            for document_id in ids[offset:offset + batch_size]:
                try:
                    document = session.get(ShreckWorldDocument, document_id)
                    existing_map = session.get(ShreckWorldDocumentBookMap, document_id)
                    if document is None or (existing_map and existing_map.map_version >= BOOK_MAP_VERSION):
                        skipped += 1
                        continue
                    build_persisted_book_map(session, document)
                    if dry_run:
                        session.rollback()
                    else:
                        session.commit()
                    created += 1
                except Exception as exc:
                    session.rollback()
                    failed += 1
                    logger.warning("Book Map backfill failed for document %s (%s)",
                                   document_id, type(exc).__name__)
            if not dry_run:
                session.commit()
        return {"created": created, "skipped": skipped, "failed": failed}
    finally:
        session.close()
