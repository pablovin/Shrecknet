"""Structured, versioned PDF ingestion for the Librarian.

Docling objects are converted to the shared shreckdoc primitive records at
the adapter boundary.  Everything after that boundary is independent of Docling.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import shutil
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid4, uuid5

from neo4j import AsyncSession as AsyncNeo4jSession

from app.core.config_store import get_settings
from app.graphrag.embedding_service import EmbeddingService, get_embedding_model

logger = logging.getLogger(__name__)

LOCK_LEASE_MINUTES = 120


from shreckdoc.pipeline import (
    PARSER_NAME, EMBEDDING_VERSION, TARGET_EMBEDDING_TOKENS, MAX_EMBEDDING_TOKENS,
    NormalizedPage,
    NormalizedSection,
    NormalizedBlock,
    NormalizedChunk,
    NormalizedDocument,
    _stable_id,
    _atomic_json,
    _atomic_text,
    _sha256,
    _native_pdf_page_count,
    _cuda_heartbeat,
    _primitive,
    _label_name,
    _block_type,
    _bbox_dict,
    _page_label_map,
    DoclingAdapter,
    _content_type,
    _normalize_embedding_notation,
    build_embedding_text,
    _token_count,
    _token_window_split,
    _model_token_limit,
    _split_for_embedding,
    _split_table_for_embedding,
    _split_list_for_embedding,
    build_chunks,
    _parse_quality_summary,
    normalize_document as _normalize_document,
)


def normalize_document(*, ingestion_id: str, pdf_path: Path, canonical: dict[str, Any], items: list[dict[str, Any]]) -> NormalizedDocument:
    """Compatibility boundary for Librarian callers and page-label overrides."""
    return _normalize_document(ingestion_id=ingestion_id, pdf_path=pdf_path, canonical=canonical, items=items, page_labeler=_page_label_map)


class DoclingIngestionService:
    def __init__(
        self, graph_session: AsyncNeo4jSession,
        embedding_service: EmbeddingService | None = None,
        adapter: DoclingAdapter | None = None,
    ) -> None:
        self.graph_session = graph_session
        self.settings = get_settings()
        self.embedding_service = embedding_service or EmbeddingService(graph_session)
        self.adapter = adapter or DoclingAdapter()

    async def _run_batched(self, query: str, rows: list[dict[str, Any]], size: int = 100) -> None:
        for start in range(0, len(rows), size):
            await self.graph_session.run(query, rows=rows[start : start + size])

    async def ensure_schema(self) -> None:
        statements = [
            "CREATE CONSTRAINT pdf_document_ingestion_id IF NOT EXISTS FOR (n:PdfDocument) REQUIRE n.ingestion_id IS UNIQUE",
            "CREATE CONSTRAINT pdf_page_id IF NOT EXISTS FOR (n:PdfPage) REQUIRE n.page_id IS UNIQUE",
            "CREATE CONSTRAINT pdf_section_id IF NOT EXISTS FOR (n:PdfSection) REQUIRE n.section_id IS UNIQUE",
            "CREATE CONSTRAINT pdf_block_id IF NOT EXISTS FOR (n:PdfBlock) REQUIRE n.block_id IS UNIQUE",
            "CREATE CONSTRAINT pdf_chunk_record_id IF NOT EXISTS FOR (n:PdfChunkRecord) REQUIRE n.chunk_id IS UNIQUE",
            "CREATE INDEX pdf_document_library_item IF NOT EXISTS FOR (n:PdfDocument) ON (n.library_item_id)",
        ]
        for statement in statements:
            await self.graph_session.run(statement)

    async def acquire_lock(self, library_item_id: int, ingestion_id: str) -> bool:
        query = """
        MERGE (lock:PdfIngestionLock {library_item_id: $library_item_id})
        ON CREATE SET lock.ingestion_id = $ingestion_id, lock.expires_at = datetime($expires_at)
        WITH lock
        WHERE lock.ingestion_id = $ingestion_id OR lock.expires_at < datetime()
        SET lock.ingestion_id = $ingestion_id, lock.expires_at = datetime($expires_at)
        RETURN lock.ingestion_id AS ingestion_id
        """
        expires = datetime.now(timezone.utc) + timedelta(minutes=LOCK_LEASE_MINUTES)
        result = await self.graph_session.run(
            query, library_item_id=library_item_id, ingestion_id=ingestion_id,
            expires_at=expires.isoformat(),
        )
        record = await result.single()
        return bool(record and record["ingestion_id"] == ingestion_id)

    async def release_lock(self, library_item_id: int, ingestion_id: str) -> None:
        await self.graph_session.run(
            "MATCH (lock:PdfIngestionLock {library_item_id: $library_item_id, ingestion_id: $ingestion_id}) DELETE lock",
            library_item_id=library_item_id, ingestion_id=ingestion_id,
        )

    async def matching_active_ingestion(self, library_item_id: int, source_sha256: str) -> str | None:
        result = await self.graph_session.run(
            """
            MATCH (d)
            WHERE 'PdfDocument' IN labels(d)
              AND d[$library_item_id_key] = $library_item_id
              AND d[$is_active_key] = true
              AND d[$source_hash_key] = $source_sha256
              AND d[$parser_name_key] = $parser_name
              AND d[$embedding_version_key] = $embedding_version
            RETURN d[$ingestion_id_key] AS ingestion_id
            """,
            library_item_id=library_item_id, source_sha256=source_sha256,
            parser_name=PARSER_NAME, embedding_version=EMBEDDING_VERSION,
            library_item_id_key="library_item_id", is_active_key="is_active",
            source_hash_key="source_sha256", parser_name_key="parser_name",
            embedding_version_key="embedding_version",
            ingestion_id_key="ingestion_id",
        )
        record = await result.single()
        return str(record["ingestion_id"]) if record and record["ingestion_id"] else None

    async def ingest(
        self, *, library_item_id: int, ontology_id: int, pdf_path: Path,
        book_title: str, rpg_system: str,
        progress_callback: Callable[[float, str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        ingestion_id = str(uuid4())
        source_hash = _sha256(pdf_path)
        logger.info(
            "docling_ingestion_received library_item_id=%s ingestion_id=%s book_title=%r pdf_path=%s",
            library_item_id, ingestion_id, book_title, pdf_path,
        )
        print(
            f"[LIBRARIAN_EMBED] step=received book={book_title!r} library_item_id={library_item_id} "
            f"ingestion_id={ingestion_id}"
        )
        if not await self.acquire_lock(library_item_id, ingestion_id):
            raise RuntimeError(f"An ingestion is already running for library item {library_item_id}")
        output_dir = pdf_path.parent / "parsed" / ingestion_id
        manifest_path = output_dir / "ingestion_manifest.json"
        created_at = datetime.now(timezone.utc).isoformat()
        manifest: dict[str, Any] = {
            "library_item_id": library_item_id, "ontology_id": ontology_id,
            "source_sha256": source_hash, "parser_name": PARSER_NAME,
            "parser_version": self._parser_version(), "embedding_version": EMBEDDING_VERSION,
            "embedding_model": self.embedding_service.model_id,
            "embedding_dimension": self.embedding_service.embed_dim,
            "ingestion_id": ingestion_id, "created_at": created_at,
            "status": "started", "warnings": [], "statistics": {},
            "artifact_paths": {
                "source_pdf": "content.pdf",
                "docling_json": f"parsed/{ingestion_id}/docling_document.json",
                "debug_markdown": f"parsed/{ingestion_id}/document.md",
                "manifest": f"parsed/{ingestion_id}/ingestion_manifest.json",
            },
        }
        _atomic_json(manifest_path, manifest)

        async def report(percent: float, status: str, **details: Any) -> None:
            if progress_callback is not None:
                await progress_callback(percent, status, details)
        activated = False
        previous_id: str | None = None
        try:
            active_ingestion_id = await self.matching_active_ingestion(library_item_id, source_hash)
            if active_ingestion_id:
                # This attempt did not parse or stage anything. Avoid leaving a misleading
                # parsed version directory while returning the actual active version ID.
                shutil.rmtree(output_dir, ignore_errors=True)
                return {
                    "status": "already_active",
                    "ingestion_id": active_ingestion_id,
                    "source_sha256": source_hash,
                    "chunks_created": 0,
                }

            print(
                f"[LIBRARIAN_EMBED] step=parse_start book={book_title!r} percent=0 "
                f"source_sha256={source_hash}"
            )
            await report(0.12, "Parsing native PDF text with Docling", phase="parse")
            native_page_count = _native_pdf_page_count(pdf_path)
            parse_started = datetime.now(timezone.utc)
            parse_started_monotonic = time.monotonic()
            parse_task = asyncio.create_task(
                asyncio.to_thread(self.adapter.parse, pdf_path, output_dir)
            )
            while not parse_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(parse_task), timeout=5.0)
                except asyncio.TimeoutError:
                    elapsed_s = time.monotonic() - parse_started_monotonic
                    # Parsing is intentionally unbounded rather than estimated:
                    # Docling does not expose a reliable per-page callback.
                    print(
                        f"[LIBRARIAN_EMBED] step=parse_heartbeat book={book_title!r} "
                        f"elapsed_s={elapsed_s:.0f} pages_total={native_page_count or 'unknown'} "
                        f"progress=indeterminate phase=parse_native_text cuda={_cuda_heartbeat()}"
                    )
                    await report(
                        0.12,
                        "Parsing native PDF text with Docling (page progress is not exposed by Docling)",
                        phase="parse",
                        elapsed_seconds=round(elapsed_s, 1),
                        pages_total=native_page_count,
                        progress_mode="indeterminate",
                        cuda=_cuda_heartbeat(),
                    )
            _, canonical, markdown, primitive_items, parser_diagnostics = await parse_task
            _atomic_json(output_dir / "docling_document.json", canonical)
            _atomic_text(output_dir / "document.md", markdown)
            parse_quality = _parse_quality_summary(
                native_page_count=native_page_count,
                canonical=canonical,
                items=primitive_items,
                diagnostics=parser_diagnostics,
            )
            manifest["warnings"] = parser_diagnostics
            manifest["statistics"].update(parse_quality)
            manifest["status"] = "parsed"
            _atomic_json(manifest_path, manifest)

            parsed_page_count = len(canonical.get("pages") or {})
            parse_elapsed_s = (datetime.now(timezone.utc) - parse_started).total_seconds()
            print(
                f"[LIBRARIAN_EMBED] step=parse_complete book={book_title!r} pages={parsed_page_count} "
                f"percent=35 elapsed_s={parse_elapsed_s:.2f}"
            )
            print(
                f"[LIBRARIAN_EMBED] step=parse_quality_summary book={book_title!r} "
                f"native_pages={parse_quality['native_pdf_pages'] or 'unknown'} docling_pages={parse_quality['docling_pages']} "
                f"pages_without_blocks={parse_quality['pages_without_structured_blocks']} "
                f"tables={parse_quality['tables_detected']} tables_without_text={parse_quality['tables_without_text']} "
                f"lists={parse_quality['lists_detected']} lists_without_text={parse_quality['lists_without_text']} "
                f"pictures={parse_quality['pictures_detected']} diagnostics={parse_quality['explicit_parser_diagnostics']}"
            )
            for diagnostic in parser_diagnostics:
                logger.warning("docling_parse_diagnostic book=%r %s", book_title, diagnostic)
            await report(0.35, "Docling parsing complete", phase="parse", pages=parsed_page_count)

            print(
                f"[LIBRARIAN_EMBED] step=normalize_start book={book_title!r} pages={parsed_page_count} percent=35"
            )
            normalized = normalize_document(
                ingestion_id=ingestion_id, pdf_path=pdf_path,
                canonical=canonical, items=primitive_items,
            )
            total_pages = max(1, len(normalized.pages))
            for page_index, page in enumerate(normalized.pages, start=1):
                percent = 35 + (page_index / total_pages) * 15
                print(
                    f"[LIBRARIAN_EMBED] step=page_normalized book={book_title!r} "
                    f"page={page.physical_page_number}/{total_pages} displayed_label={page.displayed_page_label!r} "
                    f"percent={percent:.1f}"
                )
                await report(
                    0.35 + (page_index / total_pages) * 0.15,
                    "Normalizing structured PDF pages",
                    phase="normalize",
                    page=page.physical_page_number,
                    total_pages=total_pages,
                )
            model = get_embedding_model(f"docling:{ingestion_id}")
            model_limit = _model_token_limit(model)
            chunks = build_chunks(
                document=normalized, ingestion_id=ingestion_id,
                library_item_id=library_item_id, ontology_id=ontology_id,
                book_title=book_title, rpg_system=rpg_system, tokenizer=model.tokenizer,
                max_embedding_tokens=model_limit,
            )
            children = [chunk for chunk in chunks if chunk.chunk_role == "child"]
            print(
                f"[LIBRARIAN_EMBED] step=normalize_complete book={book_title!r} pages={len(normalized.pages)} "
                f"parents={len(chunks) - len(children)} children={len(children)} model_token_limit={model_limit} percent=50"
            )
            await report(0.50, "Building and validating embedding chunks", phase="chunk")
            if not children:
                raise ValueError("Docling ingestion produced no embedding-eligible child chunks")
            valid_children: list[NormalizedChunk] = []
            for child_index, chunk in enumerate(children, start=1):
                token_count = _token_count(model.tokenizer, chunk.embedding_text)
                if token_count > model_limit:
                    logger.warning(
                        "embedding_child_skipped reason=post_split_token_limit chunk_id=%s tokens=%s limit=%s",
                        chunk.chunk_id,
                        token_count,
                        model_limit,
                    )
                    continue
                if not chunk.embedding_text.startswith("passage: "):
                    logger.warning(
                        "embedding_child_skipped reason=missing_passage_prefix chunk_id=%s",
                        chunk.chunk_id,
                    )
                    continue
                valid_children.append(chunk)
                if child_index == 1 or child_index == len(children):
                    print(
                        f"[LIBRARIAN_EMBED] step=child_validated book={book_title!r} "
                        f"child={child_index}/{len(children)} tokens={token_count}"
                    )
            children = valid_children
            if not children:
                raise ValueError("Docling ingestion produced no valid embedding child chunks")

            embeddings: list[list[float]] = []
            embedded_children: list[NormalizedChunk] = []
            batch_size = 32
            for start in range(0, len(children), batch_size):
                batch = children[start : start + batch_size]
                batch_number = start // batch_size + 1
                total_batches = math.ceil(len(children) / batch_size)
                start_percent = 50 + (start / len(children)) * 35
                print(
                    f"[LIBRARIAN_EMBED] step=embedding_batch_start book={book_title!r} "
                    f"batch={batch_number}/{total_batches} children={start + 1}-{start + len(batch)}/{len(children)} "
                    f"percent={start_percent:.1f} pages={sorted({page for chunk in batch for page in chunk.physical_page_numbers})}"
                )
                # Final guard immediately before inference. SentenceTransformers
                # otherwise truncates by default, which is unacceptable here.
                final_batch: list[NormalizedChunk] = []
                for chunk in batch:
                    token_count = _token_count(model.tokenizer, chunk.embedding_text)
                    if token_count > model_limit:
                        # This should be unreachable after the recursive splitter,
                        # but do not sacrifice a complete book for one bad unit.
                        # It is not sent to SentenceTransformers, so it can never
                        # be silently truncated.
                        logger.warning(
                            "embedding_child_skipped reason=pre_inference_token_limit chunk_id=%s tokens=%s limit=%s",
                            chunk.chunk_id,
                            token_count,
                            model_limit,
                        )
                        continue
                    final_batch.append(chunk)
                if not final_batch:
                    continue
                embeddings.extend(self.embedding_service.embed_texts([chunk.embedding_text for chunk in final_batch]))
                embedded_children.extend(final_batch)
                end_percent = 50 + ((start + len(batch)) / len(children)) * 35
                print(
                    f"[LIBRARIAN_EMBED] step=embedding_batch_complete book={book_title!r} "
                    f"batch={batch_number}/{total_batches} percent={end_percent:.1f}"
                )
                await report(
                    0.50 + ((start + len(batch)) / len(children)) * 0.35,
                    "Embedding document chunks",
                    phase="embed",
                    batch=batch_number,
                    total_batches=total_batches,
                    completed_children=start + len(batch),
                    total_children=len(children),
                )

            children = embedded_children
            if not children:
                raise ValueError("Docling ingestion produced no embeddable child chunks")
            # Retain every parent, but do not stage a skipped child: staging it
            # without a vector would violate the graph invariant and could make
            # an otherwise healthy ingestion fail validation.
            embedded_child_ids = {chunk.chunk_id for chunk in children}
            chunks = [
                chunk for chunk in chunks
                if chunk.chunk_role == "parent" or chunk.chunk_id in embedded_child_ids
            ]

            for chunk, vector in zip(children, embeddings):
                normalized_vector = [float(value) for value in vector]
                if len(normalized_vector) != self.embedding_service.embed_dim:
                    raise ValueError(
                        f"Embedding dimension mismatch: expected {self.embedding_service.embed_dim}, "
                        f"received {len(normalized_vector)}"
                    )
                if not all(math.isfinite(value) for value in normalized_vector):
                    raise ValueError("Embedding contains a non-finite value")
                chunk.text_embedding = normalized_vector

            manifest["statistics"] = {
                **parse_quality,
                "pages": len(normalized.pages), "sections": len(normalized.sections),
                "blocks": len(normalized.blocks), "paragraphs": sum(b.block_type == "paragraph" for b in normalized.blocks),
                "tables": sum(b.block_type == "table" for b in normalized.blocks),
                "lists": sum(b.block_type in {"list", "list_item"} for b in normalized.blocks),
                "pictures": sum(b.block_type == "picture" for b in normalized.blocks),
                "ocr_pages": self._ocr_page_count(canonical), "parent_chunks": len(chunks) - len(children),
                "child_chunks": len(children),
            }
            if not (output_dir / "docling_document.json").is_file():
                raise ValueError("Canonical Docling JSON was not persisted")
            if _sha256(pdf_path) != source_hash:
                raise ValueError("PDF source changed while ingestion was running")
            await self.ensure_schema()
            await self._stage_graph(
                normalized=normalized, chunks=chunks, manifest=manifest,
                book_title=book_title, rpg_system=rpg_system,
            )
            await self._validate_staging(manifest)
            manifest["status"] = "validated"
            _atomic_json(manifest_path, manifest)
            print(
                f"[LIBRARIAN_EMBED] step=activation_start book={book_title!r} "
                f"children={len(children)} percent=90"
            )
            await report(0.90, "Validating and activating document graph", phase="activate")
            previous_id = await self._activate(library_item_id, ingestion_id)
            activated = True
            manifest["status"] = "active"
            manifest["activated_at"] = datetime.now(timezone.utc).isoformat()
            manifest["previous_ingestion_id"] = previous_id
            _atomic_json(manifest_path, manifest)
            print(
                f"[LIBRARIAN_EMBED] step=activation_complete book={book_title!r} "
                f"ingestion_id={ingestion_id} percent=95"
            )
            await report(0.95, "Structured document graph activated", phase="activate")
            return {
                "status": "success", "ingestion_id": ingestion_id,
                "previous_ingestion_id": previous_id, "source_sha256": source_hash,
                "total_pages": len(normalized.pages), "pages_extracted": len(normalized.pages),
                "pages_with_no_text": 0, "chunks_created": len(children),
                "parent_chunks_created": len(chunks) - len(children), "chunks_failed": 0,
                "manifest_path": str(manifest_path),
            }
        except Exception as exc:
            if activated:
                await self.compensate_activation(library_item_id, ingestion_id, previous_id)
            manifest["status"] = "failed"
            manifest["error"] = f"{type(exc).__name__}: {exc}"
            _atomic_json(manifest_path, manifest)
            await self._delete_ingestion_graph(ingestion_id)
            raise
        finally:
            await self.release_lock(library_item_id, ingestion_id)

    def _parser_version(self) -> str:
        try:
            from importlib.metadata import version
            return version("docling")
        except Exception:
            return "unknown"

    def _ocr_page_count(self, canonical: dict[str, Any]) -> int:
        # Docling versions expose OCR provenance differently; keep the statistic conservative.
        return int((canonical.get("metadata") or {}).get("ocr_pages", 0) or 0)

    async def _stage_graph(
        self, *, normalized: NormalizedDocument, chunks: list[NormalizedChunk],
        manifest: dict[str, Any], book_title: str, rpg_system: str,
    ) -> None:
        ingestion_id = manifest["ingestion_id"]
        common = {
            "ingestion_id": ingestion_id, "library_item_id": manifest["library_item_id"],
            "ontology_id": manifest["ontology_id"], "source_sha256": manifest["source_sha256"],
            "rpg_system": rpg_system, "book_title": book_title,
        }
        await self.graph_session.run(
            """
            MERGE (li:LibraryItem {library_item_id: $library_item_id})
            SET li.ontology_id = $ontology_id
            CREATE (d:PdfDocument $document)
            CREATE (li)-[:HAS_DOCUMENT]->(d)
            """,
            library_item_id=common["library_item_id"], ontology_id=common["ontology_id"],
            document={**common, "parser_name": PARSER_NAME, "parser_version": manifest["parser_version"], "embedding_version": EMBEDDING_VERSION, "is_active": False, "created_at": manifest["created_at"]},
        )
        page_rows = [{**asdict(page), **common} for page in normalized.pages]
        await self._run_batched(
            """UNWIND $rows AS row MATCH (d:PdfDocument {ingestion_id: row.ingestion_id})
            CREATE (p:PdfPage) SET p = row CREATE (d)-[:HAS_PAGE]->(p)""", page_rows,
        )
        section_rows = [{**asdict(section), **common} for section in normalized.sections]
        await self._run_batched(
            """UNWIND $rows AS row MATCH (d:PdfDocument {ingestion_id: row.ingestion_id})
            CREATE (s:PdfSection) SET s = row CREATE (d)-[:HAS_SECTION]->(s)""", section_rows,
        )
        await self._run_batched(
            """UNWIND $rows AS row MATCH (s:PdfSection {section_id: row.section_id})
            MATCH (parent:PdfSection {section_id: row.parent_section_id})
            CREATE (parent)-[:HAS_SUBSECTION]->(s)""",
            [row for row in section_rows if row["parent_section_id"]],
        )
        block_rows = [{
            **asdict(block), **common,
            "bounding_boxes": json.dumps(block.bounding_boxes, ensure_ascii=False),
            "metadata": json.dumps(block.metadata, ensure_ascii=False),
        } for block in normalized.blocks]
        await self._run_batched(
            """UNWIND $rows AS row MATCH (s:PdfSection {section_id: row.section_id})
            CREATE (b:PdfBlock) SET b = row CREATE (s)-[:CONTAINS_BLOCK]->(b)""", block_rows,
        )
        block_pairs = [{"left": a.block_id, "right": b.block_id} for a, b in zip(normalized.blocks, normalized.blocks[1:])]
        await self._run_batched(
            """UNWIND $rows AS row MATCH (a:PdfBlock {block_id: row.left}), (b:PdfBlock {block_id: row.right}) CREATE (a)-[:NEXT_BLOCK]->(b)""",
            block_pairs,
        )
        block_page_links = [
            {"block_id": block.block_id, "ingestion_id": ingestion_id, "page": page}
            for block in normalized.blocks for page in block.page_numbers
        ]
        await self._run_batched(
            """UNWIND $rows AS row MATCH (b:PdfBlock {block_id: row.block_id}),
            (p:PdfPage {ingestion_id: row.ingestion_id, physical_page_number: row.page})
            CREATE (b)-[:ON_PAGE]->(p)""",
            block_page_links,
        )
        chunk_rows = [self._chunk_properties(chunk, manifest) for chunk in chunks]
        await self._run_batched(
            """UNWIND $rows AS row CREATE (c:PdfChunkRecord:PdfChunkCandidate) SET c = row""",
            chunk_rows,
        )
        await self._run_batched(
            """UNWIND $rows AS row MATCH (s:PdfSection {section_id: row.parent_section_id}), (c:PdfChunkRecord {chunk_id: row.chunk_id})
            FOREACH (_ IN CASE WHEN row.chunk_role = 'parent' THEN [1] ELSE [] END | CREATE (s)-[:HAS_PARENT_CHUNK]->(c))""",
            chunk_rows,
        )
        child_rows = [row for row in chunk_rows if row["chunk_role"] == "child"]
        await self._run_batched(
            """UNWIND $rows AS row MATCH (child:PdfChunkRecord {chunk_id: row.chunk_id}), (parent:PdfChunkRecord {chunk_id: row.parent_chunk_id}) CREATE (child)-[:CHILD_OF]->(parent)""",
            child_rows,
        )
        derivations = [{"chunk_id": row["chunk_id"], "block_id": block_id} for row in chunk_rows for block_id in row["source_block_ids"]]
        await self._run_batched(
            """UNWIND $rows AS row MATCH (c:PdfChunkRecord {chunk_id: row.chunk_id}), (b:PdfBlock {block_id: row.block_id}) CREATE (c)-[:DERIVED_FROM]->(b)""",
            derivations,
        )
        page_links = [{"chunk_id": row["chunk_id"], "ingestion_id": ingestion_id, "page": page} for row in chunk_rows for page in row["physical_page_numbers"]]
        await self._run_batched(
            """UNWIND $rows AS row MATCH (c:PdfChunkRecord {chunk_id: row.chunk_id}), (p:PdfPage {ingestion_id: row.ingestion_id, physical_page_number: row.page}) CREATE (c)-[:ON_PAGE]->(p)""",
            page_links,
        )

    def _chunk_properties(self, chunk: NormalizedChunk, manifest: dict[str, Any]) -> dict[str, Any]:
        primary_page = chunk.physical_page_numbers[0] if chunk.physical_page_numbers else 1
        primary_label = chunk.displayed_page_labels[0] if chunk.displayed_page_labels else None
        return {
            "chunk_id": chunk.chunk_id, "ingestion_id": chunk.ingestion_id,
            "library_item_id": chunk.library_item_id, "ontology_id": chunk.ontology_id,
            "chunk_role": chunk.chunk_role, "content_type": chunk.content_type,
            "display_text": chunk.display_text, "embedding_text": chunk.embedding_text,
            "text": chunk.display_text, "book_title": chunk.book_title,
            "rpg_system": chunk.rpg_system, "heading_path": chunk.heading_path,
            "heading_path_text": chunk.heading_path_text, "primary_heading": chunk.primary_heading,
            "block_types": chunk.block_types, "physical_page_numbers": chunk.physical_page_numbers,
            "displayed_page_labels": chunk.displayed_page_labels,
            "primary_physical_page_number": primary_page,
            "primary_displayed_page_label": primary_label,
            # Compatibility fields consumed by current retrieval.
            "page_number": primary_page, "primary_page_number": primary_page,
            "start_page_number": min(chunk.physical_page_numbers or [primary_page]),
            "end_page_number": max(chunk.physical_page_numbers or [primary_page]),
            "page_numbers": chunk.physical_page_numbers or [primary_page],
            "bounding_boxes": json.dumps(chunk.bounding_boxes, ensure_ascii=False),
            "parent_chunk_id": chunk.parent_chunk_id,
            "parent_section_id": chunk.parent_section_id,
            "source_block_ids": chunk.source_block_ids,
            "text_embedding": chunk.text_embedding,
            "text_embedding_model": self.embedding_service.model_id if chunk.text_embedding else None,
            "text_embedding_dim": self.embedding_service.embed_dim if chunk.text_embedding else None,
            "parser_name": PARSER_NAME, "parser_version": manifest["parser_version"],
            "embedding_version": EMBEDDING_VERSION, "source_sha256": manifest["source_sha256"],
            "chunk_index": chunk.chunk_index, "is_active": False,
            "embedding_eligible": chunk.embedding_eligible,
            "fulltext_eligible": chunk.fulltext_eligible,
            "created_at": manifest["created_at"],
        }

    async def _validate_staging(self, manifest: dict[str, Any]) -> None:
        result = await self.graph_session.run(
            """
            MATCH (d:PdfDocument {ingestion_id: $ingestion_id})
            OPTIONAL MATCH (d)-[:HAS_PAGE]->(page:PdfPage)
            WITH d, count(DISTINCT page) AS pages
            OPTIONAL MATCH (d)-[:HAS_SECTION]->(section:PdfSection)
            WITH d, pages, count(DISTINCT section) AS sections
            OPTIONAL MATCH (d)-[:HAS_SECTION]->(:PdfSection)-[:CONTAINS_BLOCK]->(block:PdfBlock)
            WITH d, pages, sections, count(DISTINCT block) AS blocks
            OPTIONAL MATCH (chunk:PdfChunkCandidate {ingestion_id: $ingestion_id})
            RETURN pages, sections, blocks, count(DISTINCT chunk) AS chunks,
              count(DISTINCT CASE WHEN chunk.chunk_role = 'child' THEN chunk END) AS children,
              count(DISTINCT CASE WHEN chunk.chunk_role = 'child' AND size(chunk.text_embedding) = $dim THEN chunk END) AS valid_vectors
            """,
            ingestion_id=manifest["ingestion_id"], dim=self.embedding_service.embed_dim,
        )
        record = await result.single()
        stats = manifest["statistics"]
        if not record or int(record["children"]) < 1:
            raise ValueError("Docling ingestion produced no retrievable child chunks")
        integrity_result = await self.graph_session.run(
            """
            MATCH (child:PdfChunkCandidate {ingestion_id: $ingestion_id, chunk_role: 'child'})
            OPTIONAL MATCH (child)-[:CHILD_OF]->(parent:PdfChunkCandidate {ingestion_id: $ingestion_id})
            OPTIONAL MATCH (child)-[:DERIVED_FROM]->(block:PdfBlock {ingestion_id: $ingestion_id})
            OPTIONAL MATCH (child)-[:ON_PAGE]->(page:PdfPage {ingestion_id: $ingestion_id})
            WITH child, count(DISTINCT parent) AS parents,
                 count(DISTINCT block) AS blocks, count(DISTINCT page) AS pages
            RETURN count(CASE WHEN parents <> 1 THEN 1 END) AS invalid_parent_count,
                   count(CASE WHEN size(child.source_block_ids) > 0 AND blocks = 0 THEN 1 END) AS missing_blocks,
                   count(CASE WHEN size(child.physical_page_numbers) > 0 AND pages = 0 THEN 1 END) AS missing_pages
            """,
            ingestion_id=manifest["ingestion_id"],
        )
        integrity = await integrity_result.single()
        expected_chunks = int(stats["parent_chunks"]) + int(stats["child_chunks"])
        checks = {
            "pages": (int(record["pages"]), int(stats["pages"])),
            "sections": (int(record["sections"]), int(stats["sections"])),
            "blocks": (int(record["blocks"]), int(stats["blocks"])),
            "chunks": (int(record["chunks"]), expected_chunks),
            "valid_vectors": (int(record["valid_vectors"]), int(record["children"])),
            "invalid_parent_count": (int(integrity["invalid_parent_count"] if integrity else -1), 0),
            "missing_blocks": (int(integrity["missing_blocks"] if integrity else -1), 0),
            "missing_pages": (int(integrity["missing_pages"] if integrity else -1), 0),
        }
        failures = {key: values for key, values in checks.items() if values[0] != values[1]}
        if failures:
            raise ValueError(f"Staged Docling graph validation failed: {failures}")

    async def _activate(self, library_item_id: int, ingestion_id: str) -> str | None:
        async def activate(tx: Any) -> str | None:
            result = await tx.run(
                """
                OPTIONAL MATCH (old:PdfDocument {library_item_id: $library_item_id, is_active: true})
                WITH old, old.ingestion_id AS previous_id
                FOREACH (_ IN CASE WHEN old IS NULL THEN [] ELSE [1] END | SET old.is_active = false, old.retired_at = datetime())
                WITH previous_id
                MATCH (new:PdfDocument {ingestion_id: $ingestion_id})
                SET new.is_active = true, new.activated_at = datetime()
                WITH previous_id
                OPTIONAL MATCH (prior:PdfChunk {library_item_id: $library_item_id})
                REMOVE prior:PdfChunk SET prior:PdfChunkRetired, prior.is_active = false
                WITH previous_id
                MATCH (candidate:PdfChunkCandidate {ingestion_id: $ingestion_id})
                REMOVE candidate:PdfChunkCandidate SET candidate:PdfChunk, candidate.is_active = true
                RETURN previous_id
                """,
                library_item_id=library_item_id, ingestion_id=ingestion_id,
            )
            record = await result.single()
            return record["previous_id"] if record else None

        return await self.graph_session.execute_write(activate)

    async def compensate_activation(self, library_item_id: int, ingestion_id: str, previous_id: str | None) -> None:
        async def compensate(tx: Any) -> None:
            await tx.run(
                """
                MATCH (new:PdfDocument {ingestion_id: $ingestion_id}) SET new.is_active = false
                WITH new OPTIONAL MATCH (c:PdfChunk {ingestion_id: $ingestion_id}) REMOVE c:PdfChunk SET c:PdfChunkCandidate, c.is_active = false
                WITH new OPTIONAL MATCH (old:PdfDocument {ingestion_id: $previous_id}) SET old.is_active = true REMOVE old.retired_at
                WITH new OPTIONAL MATCH (prior:PdfChunkRetired {library_item_id: $library_item_id})
                WHERE ($previous_id IS NULL AND prior.ingestion_id IS NULL) OR prior.ingestion_id = $previous_id
                REMOVE prior:PdfChunkRetired SET prior:PdfChunk, prior.is_active = true
                """,
                library_item_id=library_item_id, ingestion_id=ingestion_id, previous_id=previous_id,
            )
        await self.graph_session.execute_write(compensate)

    async def cleanup_retired(self, library_item_id: int, active_ingestion_id: str) -> int:
        result = await self.graph_session.run(
            """
            OPTIONAL MATCH (d:PdfDocument {library_item_id: $library_item_id})
            WHERE d.ingestion_id <> $active_ingestion_id
            WITH collect(d.ingestion_id) AS retired_ids
            MATCH (node)
            WHERE node.ingestion_id IN retired_ids
               OR (node:PdfChunkRetired AND node.library_item_id = $library_item_id)
            DETACH DELETE node RETURN count(node) AS deleted
            """,
            library_item_id=library_item_id, active_ingestion_id=active_ingestion_id,
        )
        record = await result.single()
        return int(record["deleted"] if record else 0)

    async def _delete_ingestion_graph(self, ingestion_id: str) -> None:
        await self.graph_session.run(
            """
            MATCH (node) WHERE node.ingestion_id = $ingestion_id
            DETACH DELETE node
            """, ingestion_id=ingestion_id,
        )

    @staticmethod
    def cleanup_parsed_directories(pdf_path: Path, active_ingestion_id: str) -> None:
        parsed = pdf_path.parent / "parsed"
        if not parsed.exists():
            return
        for child in parsed.iterdir():
            if child.is_dir() and child.name != active_ingestion_id:
                manifest = child / "ingestion_manifest.json"
                try:
                    payload = json.loads(manifest.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if payload.get("status") in {"active", "already_active"}:
                    shutil.rmtree(child, ignore_errors=True)

    @staticmethod
    def mark_manifest_compensated(manifest_path: str, error: Exception) -> None:
        path = Path(manifest_path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        payload["status"] = "failed"
        payload["activation_compensated"] = True
        payload["error"] = f"{type(error).__name__}: {error}"
        _atomic_json(path, payload)
