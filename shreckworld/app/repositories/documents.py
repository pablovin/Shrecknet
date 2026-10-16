"""Persist and activate structured document generations within caller transactions."""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session
from shreckdoc.pipeline import NormalizedDocument

from app.models import (
    ShreckWorldDocument, ShreckWorldDocumentPage, ShreckWorldDocumentSection,
    ShreckWorldDocumentBlock, ShreckWorldDocumentChunk, ShreckWorldLibraryItem,
)


def active_documents(session: Session, world_id: str, fingerprint: str, library_item_id: str | None = None) -> list[ShreckWorldDocument]:
    statement = select(ShreckWorldDocument).join(ShreckWorldLibraryItem).where(
        ShreckWorldLibraryItem.shreckworld_id == world_id, ShreckWorldDocument.is_active.is_(True),
        ShreckWorldDocument.embedding_fingerprint == fingerprint)
    if library_item_id is not None:
        statement = statement.where(ShreckWorldLibraryItem.id == library_item_id)
    return list(session.scalars(statement))


def activate_document(session: Session, *, item: ShreckWorldLibraryItem, document: ShreckWorldDocument,
                      normalized: NormalizedDocument, chunks: list[ShreckWorldDocumentChunk]) -> list[str]:
    previous = list(session.scalars(select(ShreckWorldDocument.id).where(
        ShreckWorldDocument.library_item_id == item.id, ShreckWorldDocument.is_active.is_(True))))
    session.execute(update(ShreckWorldDocument).where(ShreckWorldDocument.library_item_id == item.id).values(is_active=False))
    session.execute(delete(ShreckWorldDocumentChunk).where(ShreckWorldDocumentChunk.library_item_id == item.id))
    if session.bind.dialect.name == "sqlite":
        for prior_id in previous:
            session.execute(text("DELETE FROM shreckworld_chunk_fts WHERE document_id = :id"), {"id": prior_id})
    document.is_active = True
    session.add(document)
    session.flush()
    session.add_all([ShreckWorldDocumentPage(id=page.page_id, document_id=document.id,
        page_number=page.physical_page_number, page_label=page.displayed_page_label,
        width=page.width, height=page.height) for page in normalized.pages])
    session.add_all([ShreckWorldDocumentSection(id=section.section_id, document_id=document.id,
        parent_section_id=section.parent_section_id, heading=section.heading,
        heading_level=section.heading_level, heading_path=section.heading_path) for section in normalized.sections])
    session.add_all([ShreckWorldDocumentBlock(id=block.block_id, document_id=document.id,
        section_id=block.section_id, block_type=block.block_type, display_text=block.display_text,
        reading_order=block.reading_order, docling_reference=block.docling_reference,
        provenance=block.metadata.get("provenance", []), block_metadata=block.metadata) for block in normalized.blocks])
    session.add_all(chunks)
    if session.bind.dialect.name == "sqlite":
        rows = [{"chunk_id": chunk.id, "document_id": document.id, "title": item.title,
                 "heading_path": " > ".join(chunk.heading_path), "text": chunk.text}
                for chunk in chunks if chunk.chunk_role == "child"]
        if rows:
            session.execute(text("INSERT INTO shreckworld_chunk_fts(chunk_id, document_id, title, heading_path, text) VALUES (:chunk_id,:document_id,:title,:heading_path,:text)"), rows)
    session.flush()
    return previous


def child_rows(session: Session, document_ids: list[str]):
    return session.execute(select(ShreckWorldDocumentChunk, ShreckWorldLibraryItem)
        .join(ShreckWorldLibraryItem).where(ShreckWorldDocumentChunk.document_id.in_(document_ids),
                                          ShreckWorldDocumentChunk.chunk_role == "child")).all()


def lexical_candidates(session: Session, query: str, document_ids: list[str], limit: int = 40) -> list[str]:
    terms = re.findall(r"\w+", query, flags=re.UNICODE)[:40]
    if not terms or not document_ids:
        return []
    if session.bind.dialect.name == "sqlite":
        # Only tokens produced by this tokenizer enter FTS syntax. Values and
        # document IDs remain bound parameters, including quoted user phrases.
        expression = " OR ".join('"' + term + '"' for term in terms)
        from sqlalchemy import bindparam
        statement = text("SELECT chunk_id FROM shreckworld_chunk_fts WHERE shreckworld_chunk_fts MATCH :query AND document_id IN :ids ORDER BY bm25(shreckworld_chunk_fts), chunk_id LIMIT :limit").bindparams(bindparam("ids", expanding=True))
        return list(session.scalars(statement, {"query": expression, "ids": document_ids, "limit": limit}))
    rows = child_rows(session, document_ids)
    ranked = sorted(rows, key=lambda row: (-sum(term.casefold() in (row[0].text + " " + " ".join(row[0].heading_path)).casefold() for term in terms), row[0].id))
    return [chunk.id for chunk, _ in ranked[:limit]]


def citation_provenance(session: Session, chunk: ShreckWorldDocumentChunk) -> list[dict[str, Any]]:
    pages = {page.page_number: page for page in session.scalars(select(ShreckWorldDocumentPage).where(ShreckWorldDocumentPage.document_id == chunk.document_id))}
    blocks = list(session.scalars(select(ShreckWorldDocumentBlock).where(
        ShreckWorldDocumentBlock.document_id == chunk.document_id,
        ShreckWorldDocumentBlock.id.in_(chunk.source_block_ids))))
    spans = []
    for block in sorted(blocks, key=lambda value: value.reading_order):
        for provenance in block.provenance:
            number = int(provenance.get("page_no") or 1)
            page = pages.get(number)
            spans.append({"block_id": block.id, "page_number": number,
                          "page_label": page.page_label if page else None,
                          "page_width": page.width if page else None,
                          "page_height": page.height if page else None,
                          "bounding_box": provenance.get("bbox") or None,
                          "charspan": provenance.get("charspan")})
    return spans
