"""Service for embedding PDF books into Neo4j for librarian queries."""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from neo4j import AsyncSession as AsyncNeo4jSession

from app.core.config_store import get_settings
from app.graphrag.embedding_runtime import get_ready_embedding_runtime
from app.graphrag.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


class PdfEmbeddingService:
    """
    Service for embedding PDF book content into Neo4j.

    This service chunks PDF content and creates embeddings for
    semantic retrieval by the Librarian job.
    """

    def __init__(
        self,
        graph_session: AsyncNeo4jSession,
        embedding_service: EmbeddingService | None = None,
    ):
        """
        Initialize the PDF embedding service.

        Args:
            graph_session: Neo4j async session for graph operations
            embedding_service: Optional embedding service (creates if None)
        """
        self.graph_session = graph_session
        self.settings = get_settings()
        self.embedding_service = embedding_service or EmbeddingService(graph_session)

    async def ensure_vector_index(self) -> dict[str, Any]:
        """
        Ensure Neo4j vector index exists for PDF chunks.

        Creates index named 'pdf_chunk_text_vec_idx' if it doesn't exist.

        Returns:
            Dictionary with index name, exists status, model, and dimensions
        """
        index_name = "pdf_chunk_text_vec_idx"
        fulltext_index_name = "pdf_chunk_text_fulltext_idx"
        model = self.embedding_service.model_id
        dims = self.embedding_service.embed_dim

        # Check if index exists
        check_query = """
        SHOW INDEXES YIELD name, type
        WHERE name = $index_name AND type = 'VECTOR'
        RETURN count(*) as count
        """

        result = await self.graph_session.run(check_query, index_name=index_name)
        record = await result.single()
        exists = record["count"] > 0 if record else False

        if not exists:
            # Create vector index
            create_query = f"""
            CREATE VECTOR INDEX {index_name} IF NOT EXISTS
            FOR (c:PdfChunk)
            ON c.text_embedding
            OPTIONS {{
                indexConfig: {{
                    `vector.dimensions`: {dims},
                    `vector.similarity_function`: 'cosine'
                }}
            }}
            """
            await self.graph_session.run(create_query)
            logger.info(f"Created vector index {index_name}")

        fulltext_status = "present"
        fulltext_check = """
        SHOW INDEXES YIELD name, type
        WHERE name = $index_name AND type = 'FULLTEXT'
        RETURN count(*) as count
        """
        fulltext_result = await self.graph_session.run(
            fulltext_check, index_name=fulltext_index_name
        )
        fulltext_record = await fulltext_result.single()
        fulltext_exists = fulltext_record["count"] > 0 if fulltext_record else False
        if not fulltext_exists:
            create_fulltext = f"""
            CREATE FULLTEXT INDEX {fulltext_index_name} IF NOT EXISTS
            FOR (c:PdfChunk)
            ON EACH [c.text]
            """
            await self.graph_session.run(create_fulltext)
            fulltext_status = "created"
            logger.info("Created fulltext index %s", fulltext_index_name)

        return {
            "index_name": index_name,
            "fulltext_index_name": fulltext_index_name,
            "exists": exists,
            "fulltext_status": fulltext_status,
            "embedding_model": model,
            "embedding_dim": dims,
        }

    async def embed_pdf_book(
        self,
        library_item_id: int,
        ontology_id: int,
        pdf_path: Path,
        batch_size: int = 20,
    ) -> dict[str, Any]:
        """
        Embed a PDF book into Neo4j as chunks.

        Args:
            library_item_id: ID of the library item
            ontology_id: ID of the ontology this book belongs to
            pdf_path: Path to the PDF file
            batch_size: Number of chunks to process at once

        Returns:
            Dictionary with statistics about the embedding process
        """
        try:
            try:
                import fitz  # PyMuPDF
            except ImportError as exc:
                raise ImportError(
                    "PyMuPDF is required for PDF embedding. "
                    "Install with: pip install PyMuPDF"
                ) from exc

            await self.ensure_vector_index()

            logger.info(
                "pdf_embedding stage=open_pdf library_item_id=%s ontology_id=%s path=%s",
                library_item_id,
                ontology_id,
                pdf_path,
            )
            doc = fitz.open(str(pdf_path))
            try:
                total_pages = len(doc)
                logger.info(
                    "pdf_embedding stage=extract_text_start library_item_id=%s total_pages=%s skipped_cover_pages=%s",
                    library_item_id,
                    total_pages,
                    1 if total_pages > 0 else 0,
                )
                page_labels = self._extract_page_labels(doc, total_pages)
                page_records = self._extract_native_page_records(
                    doc=doc,
                    page_labels=page_labels,
                    skip_page_indices={0},
                )
                extracted_pages = len(page_records)
                pages_with_no_text = max(total_pages - 1 - extracted_pages, 0)
                total_chars = sum(len(page["text"]) for page in page_records)
                logger.info(
                    "pdf_embedding stage=extract_text_done library_item_id=%s extracted_pages=%s missing_pages=%s extracted_chars=%s",
                    library_item_id,
                    extracted_pages,
                    pages_with_no_text,
                    total_chars,
                )

                logger.info(
                    "pdf_embedding stage=chunking_start library_item_id=%s extracted_pages=%s",
                    library_item_id,
                    extracted_pages,
                )
                semantic_chunks = self._build_semantic_chunks(
                    library_item_id=library_item_id,
                    ontology_id=ontology_id,
                    page_records=page_records,
                )
                logger.info(
                    "pdf_embedding stage=chunking_done library_item_id=%s semantic_chunks=%s",
                    library_item_id,
                    len(semantic_chunks),
                )

                chunks_created = 0
                chunks_failed = 0
                total_batches = max((len(semantic_chunks) + batch_size - 1) // batch_size, 1)
                logger.info(
                    "pdf_embedding stage=embedding_start library_item_id=%s semantic_chunks=%s batch_size=%s total_batches=%s",
                    library_item_id,
                    len(semantic_chunks),
                    batch_size,
                    total_batches,
                )
                for batch_number, start_idx in enumerate(
                    range(0, len(semantic_chunks), batch_size),
                    start=1,
                ):
                    batch_chunks = semantic_chunks[start_idx : start_idx + batch_size]
                    if batch_chunks:
                        logger.info(
                            "pdf_embedding stage=embedding_batch_start library_item_id=%s batch=%s/%s chunk_range=%s-%s batch_chunks=%s pages=%s-%s",
                            library_item_id,
                            batch_number,
                            total_batches,
                            batch_chunks[0]["chunk_index"],
                            batch_chunks[-1]["chunk_index"],
                            len(batch_chunks),
                            batch_chunks[0]["start_page_number"],
                            batch_chunks[-1]["end_page_number"],
                        )
                    try:
                        await self._embed_chunks_batch(batch_chunks)
                        chunks_created += len(batch_chunks)
                        logger.info(
                            "pdf_embedding stage=embedding_batch_done library_item_id=%s batch=%s/%s chunks_created_total=%s",
                            library_item_id,
                            batch_number,
                            total_batches,
                            chunks_created,
                        )
                    except Exception as exc:
                        logger.error(
                            "pdf_embedding stage=embedding_batch_failed library_item_id=%s batch=%s/%s error=%s",
                            library_item_id,
                            batch_number,
                            total_batches,
                            exc,
                        )
                        chunks_failed += len(batch_chunks)
            finally:
                doc.close()

            needs_ocr = extracted_pages == 0 or total_chars < 400
            logger.info(
                "pdf_embedding stage=summary library_item_id=%s total_pages=%s embedded_pages=%s missing_pages=%s chunks_created=%s chunks_failed=%s status=%s",
                library_item_id,
                total_pages,
                extracted_pages,
                pages_with_no_text,
                chunks_created,
                chunks_failed,
                "needs_ocr" if needs_ocr and chunks_created == 0 else "success" if chunks_created > 0 else "failed",
            )
            if needs_ocr and chunks_created == 0:
                logger.warning(
                    "pdf_embedding stage=needs_ocr library_item_id=%s extracted_pages=%s extracted_chars=%s",
                    library_item_id,
                    extracted_pages,
                    total_chars,
                )

            return {
                "library_item_id": library_item_id,
                "ontology_id": ontology_id,
                "total_pages": total_pages,
                "chunks_created": chunks_created,
                "chunks_failed": chunks_failed,
                "pages_skipped_as_cover": 1 if total_pages > 0 else 0,
                "pages_extracted": extracted_pages,
                "pages_with_no_text": pages_with_no_text,
                "ocr_pages": 0,
                "status": "success" if chunks_created > 0 else "needs_ocr" if needs_ocr else "failed",
            }

        except Exception as e:
            logger.error(f"PDF embedding failed: {e}", exc_info=True)
            raise

    def _extract_page_labels(self, doc: Any, total_pages: int) -> list[str | None]:
        page_labels: list[str | None] = [None] * total_pages
        try:
            labels = doc.get_page_labels()
        except Exception as exc:
            logger.debug("Could not extract page labels from PyMuPDF: %s", exc)
            return page_labels

        if not isinstance(labels, list):
            return page_labels
        for idx in range(min(total_pages, len(labels))):
            label = labels[idx]
            if label:
                page_labels[idx] = str(label)
        return page_labels

    def _extract_native_page_records(
        self,
        *,
        doc: Any,
        page_labels: list[str | None],
        skip_page_indices: set[int],
    ) -> list[dict[str, Any]]:
        raw_pages: list[dict[str, Any]] = []
        total_pages = len(doc)
        for page_idx in range(total_pages):
            if page_idx in skip_page_indices:
                continue
            try:
                raw_text = doc[page_idx].get_text("text") or ""
            except Exception as exc:
                logger.warning("Failed to extract text from page %s: %s", page_idx + 1, exc)
                continue

            display_page = self._resolve_display_page_number(page_idx, page_labels)
            raw_pages.append(
                {
                    "page_index": page_idx,
                    "page_number": display_page,
                    "raw_text": raw_text,
                }
            )

        header_lines, footer_lines = self._detect_repeated_margin_lines(raw_pages)

        page_records: list[dict[str, Any]] = []
        for page in raw_pages:
            normalized = self._normalize_page_text(
                page["raw_text"],
                header_lines=header_lines,
                footer_lines=footer_lines,
            )
            if not self._is_meaningful_text(normalized):
                continue
            page_records.append(
                {
                    "page_index": page["page_index"],
                    "page_number": page["page_number"],
                    "text": normalized,
                }
            )
        return page_records

    def _resolve_display_page_number(
        self, page_idx: int, page_labels: list[str | None]
    ) -> int:
        display_page = page_idx + 1
        if page_idx < len(page_labels):
            label = page_labels[page_idx]
            if label:
                try:
                    return int(label)
                except (TypeError, ValueError):
                    logger.debug(
                        "Non-numeric page label '%s' for page %s, using fallback",
                        label,
                        page_idx + 1,
                    )
        return display_page

    def _detect_repeated_margin_lines(
        self, pages: list[dict[str, Any]]
    ) -> tuple[set[str], set[str]]:
        if not pages:
            return set(), set()

        top_lines = Counter()
        bottom_lines = Counter()
        for page in pages:
            lines = self._clean_lines(page.get("raw_text", ""))
            if not lines:
                continue
            top_lines[lines[0]] += 1
            bottom_lines[lines[-1]] += 1

        threshold = max(3, len(pages) // 5)
        headers = {line for line, count in top_lines.items() if count >= threshold}
        footers = {line for line, count in bottom_lines.items() if count >= threshold}
        return headers, footers

    def _normalize_page_text(
        self,
        raw_text: str,
        *,
        header_lines: set[str],
        footer_lines: set[str],
    ) -> str:
        lines = self._clean_lines(raw_text)
        while lines and lines[0] in header_lines:
            lines.pop(0)
        while lines and lines[-1] in footer_lines:
            lines.pop()

        preserve_lines = self._looks_table_like_lines(lines)
        if not preserve_lines:
            text = "\n".join(lines)
            text = text.replace("\x00", " ")
            text = re.sub(r"-\n(?=\w)", "", text)
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
            text = re.sub(r"\s+", " ", text)
        else:
            text = "\n".join(line.replace("\x00", " ").strip() for line in lines if line.strip())
            text = re.sub(r"-\n(?=\w)", "", text)
            text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _clean_lines(self, text: str) -> list[str]:
        return [line.strip() for line in text.splitlines() if line and line.strip()]

    def _is_meaningful_text(self, text: str) -> bool:
        if len(text) < 40:
            return False
        alpha_count = sum(1 for char in text if char.isalpha())
        return alpha_count >= 20

    def _looks_table_like_lines(self, lines: list[str]) -> bool:
        if len(lines) < 4:
            return False
        short_lines = sum(1 for line in lines if 3 <= len(line) <= 90)
        numeric_lines = sum(1 for line in lines if re.search(r"\b\d+%?\b", line))
        separator_lines = sum(1 for line in lines if re.search(r"\s{2,}|[|•·]", line))
        colon_lines = sum(1 for line in lines if ":" in line and len(line) <= 100)
        return (
            short_lines / max(1, len(lines)) >= 0.65
            and (numeric_lines + separator_lines + colon_lines) >= 2
        )

    def _looks_table_like_text(self, text: str) -> bool:
        return self._looks_table_like_lines(self._clean_lines(text))

    def _split_page_into_segments(self, text: str) -> list[str]:
        pieces = re.split(r"(?<=[.!?])\s+|\n\n+", text)
        segments: list[str] = []
        for piece in pieces:
            cleaned = piece.strip()
            if not cleaned:
                continue
            if len(cleaned) <= self.embedding_service.chunk_size:
                segments.append(cleaned)
                continue
            segments.extend(
                self.embedding_service._chunk_text(
                    cleaned,
                    size=self.embedding_service.chunk_size,
                    overlap=self.embedding_service.chunk_overlap,
                )
            )
        return segments

    def _build_semantic_chunks(
        self,
        *,
        library_item_id: int,
        ontology_id: int,
        page_records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        chunk_target = max(250, self.embedding_service.chunk_size)
        overlap_target = max(80, min(self.embedding_service.chunk_overlap, chunk_target // 3))

        units: list[dict[str, Any]] = []
        for page in page_records:
            for segment in self._split_page_into_segments(page["text"]):
                units.append(
                    {
                        "page_number": page["page_number"],
                        "text": segment,
                    }
                )

        chunks: list[dict[str, Any]] = []
        current_units: list[dict[str, Any]] = []
        current_len = 0

        def finalize_chunk() -> None:
            nonlocal current_units, current_len
            if not current_units:
                return
            page_numbers = sorted({int(unit["page_number"]) for unit in current_units})
            primary_page = page_numbers[0]
            text = "\n\n".join(unit["text"] for unit in current_units).strip()
            chunks.append(
                {
                    "library_item_id": library_item_id,
                    "ontology_id": ontology_id,
                    "page_number": primary_page,
                    "primary_page_number": primary_page,
                    "start_page_number": page_numbers[0],
                    "end_page_number": page_numbers[-1],
                    "page_numbers": page_numbers,
                    "chunk_type": "table_like" if self._looks_table_like_text(text) else "text",
                    "text": text,
                    "char_count": len(text),
                    "chunk_index": len(chunks),
                }
            )

            overlap_units: list[dict[str, Any]] = []
            overlap_len = 0
            for unit in reversed(current_units):
                unit_len = len(unit["text"])
                if overlap_units and overlap_len + unit_len > overlap_target:
                    break
                overlap_units.insert(0, unit)
                overlap_len += unit_len
            current_units = overlap_units
            current_len = sum(len(unit["text"]) for unit in current_units)

        for unit in units:
            unit_len = len(unit["text"])
            if current_units and current_len + unit_len > chunk_target:
                finalize_chunk()
            current_units.append(unit)
            current_len += unit_len

        finalize_chunk()
        return chunks

    async def _embed_chunks_batch(self, chunks: list[dict[str, Any]]) -> None:
        """
        Embed a batch of text chunks into Neo4j.

        Args:
            chunks: List of chunk dictionaries with text and metadata
        """
        # Generate embeddings for all texts
        texts = [chunk["text"] for chunk in chunks]
        embeddings = self.embedding_service.embed_texts(texts)

        # Create nodes in Neo4j
        for chunk, embedding in zip(chunks, embeddings):
            query = """
            MERGE (c:PdfChunk {
                library_item_id: $library_item_id,
                chunk_index: $chunk_index
            })
            SET c.ontology_id = $ontology_id,
                c.page_number = $page_number,
                c.primary_page_number = $primary_page_number,
                c.start_page_number = $start_page_number,
                c.end_page_number = $end_page_number,
                c.page_numbers = $page_numbers,
                c.chunk_type = $chunk_type,
                c.char_count = $char_count,
                c.text = $text,
                c.text_embedding = $embedding,
                c.text_embedding_model = $model,
                c.text_embedding_dim = $dim,
                c.last_embedded_date = datetime()
            """

            await self.graph_session.run(
                query,
                library_item_id=chunk["library_item_id"],
                chunk_index=chunk["chunk_index"],
                ontology_id=chunk["ontology_id"],
                page_number=chunk["page_number"],
                primary_page_number=chunk.get("primary_page_number", chunk["page_number"]),
                start_page_number=chunk.get("start_page_number", chunk["page_number"]),
                end_page_number=chunk.get("end_page_number", chunk["page_number"]),
                page_numbers=chunk.get("page_numbers", [chunk["page_number"]]),
                chunk_type=chunk.get("chunk_type", "text"),
                char_count=chunk.get("char_count", len(chunk["text"])),
                text=chunk["text"],
                embedding=embedding,
                model=self.embedding_service.model_id,
                dim=self.embedding_service.embed_dim,
            )

    async def get_embedding_stats(self, library_item_id: int) -> dict[str, int | bool]:
        """
        Get embedding statistics for a library item.

        Args:
            library_item_id: ID of the library item

        Returns:
            Dictionary with total chunks and embedded status
        """
        query = """
        MATCH (c:PdfChunk {library_item_id: $library_item_id})
        RETURN count(c) as total_chunks
        """

        result = await self.graph_session.run(query, library_item_id=library_item_id)
        record = await result.single()

        total_chunks = record["total_chunks"] if record else 0

        return {
            "library_item_id": library_item_id,
            "total_chunks": total_chunks,
            "is_embedded": total_chunks > 0,
        }

    async def delete_embeddings(self, library_item_id: int) -> int:
        """
        Delete all embeddings for a library item.

        Args:
            library_item_id: ID of the library item

        Returns:
            Number of chunks deleted
        """
        query = """
        MATCH (c:PdfChunk {library_item_id: $library_item_id})
        WITH collect(c) as chunks
        WITH chunks, size(chunks) as total
        UNWIND chunks as c
        DETACH DELETE c
        RETURN total
        """

        result = await self.graph_session.run(query, library_item_id=library_item_id)
        record = await result.single()

        deleted = record["total"] if record else 0
        logger.info(f"Deleted {deleted} PDF chunks for library item {library_item_id}")

        return deleted

    async def delete_embeddings_for_ontology(
        self,
        ontology_id: int,
        library_item_ids: list[int] | None = None,
    ) -> int:
        """
        Delete all embeddings for an ontology, including stale chunks whose ontology_id
        is missing/invalid but belong to the ontology's library items.

        Args:
            ontology_id: Ontology to clear
            library_item_ids: Optional known SQL item IDs for fallback stale cleanup

        Returns:
            Number of chunks deleted
        """
        if library_item_ids:
            query = """
            MATCH (c:PdfChunk)
            WHERE c.ontology_id = $ontology_id
               OR c.library_item_id IN $library_item_ids
            WITH collect(c) AS chunks
            WITH chunks, size(chunks) AS total
            UNWIND chunks AS c
            DETACH DELETE c
            RETURN total
            """
            params: dict[str, Any] = {
                "ontology_id": ontology_id,
                "library_item_ids": library_item_ids,
            }
        else:
            query = """
            MATCH (c:PdfChunk {ontology_id: $ontology_id})
            WITH collect(c) AS chunks
            WITH chunks, size(chunks) AS total
            UNWIND chunks AS c
            DETACH DELETE c
            RETURN total
            """
            params = {"ontology_id": ontology_id}

        result = await self.graph_session.run(query, **params)
        record = await result.single()
        deleted = int(record["total"]) if record else 0
        logger.info(
            "Deleted %s PDF chunks for ontology %s (fallback_ids=%s)",
            deleted,
            ontology_id,
            len(library_item_ids or []),
        )
        return deleted

    async def delete_all_embeddings(self) -> int:
        """
        Delete all PdfChunk nodes in Neo4j.

        Returns:
            Number of chunks deleted
        """
        query = """
        MATCH (c:PdfChunk)
        WITH collect(c) AS chunks
        WITH chunks, size(chunks) AS total
        UNWIND chunks AS c
        DETACH DELETE c
        RETURN total
        """
        result = await self.graph_session.run(query)
        record = await result.single()
        deleted = int(record["total"]) if record else 0
        logger.info("Deleted %s PDF chunks globally", deleted)
        return deleted

    async def delete_orphan_embeddings(
        self,
        valid_library_item_ids: list[int],
    ) -> int:
        """
        Delete chunks whose library_item_id is not present in SQL library_items.

        Args:
            valid_library_item_ids: Current SQL library item IDs

        Returns:
            Number of orphan chunks deleted
        """
        if not valid_library_item_ids:
            return 0

        query = """
        MATCH (c:PdfChunk)
        WHERE NOT c.library_item_id IN $valid_library_item_ids
        WITH collect(c) AS chunks
        WITH chunks, size(chunks) AS total
        UNWIND chunks AS c
        DETACH DELETE c
        RETURN total
        """
        result = await self.graph_session.run(
            query, valid_library_item_ids=valid_library_item_ids
        )
        record = await result.single()
        deleted = int(record["total"]) if record else 0
        logger.info("Deleted %s orphan PDF chunks", deleted)
        return deleted

    async def search_chunks(
        self,
        query_text: str,
        ontology_id: int,
        library_item_ids: list[int] | None = None,
        active_library_item_ids: list[int] | None = None,
        top_k: int = 10,
        score_threshold: float = 0.5,
        candidate_limit: int | None = None,
        hybrid_rerank: bool = True,
        max_chunks_per_item: int | None = None,
        dynamic_score_floor: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Search PDF chunks by semantic similarity.

        Args:
            query_text: Query text to search for
            ontology_id: ID of the ontology to search within
            library_item_ids: Optional list of library item IDs to filter by
            top_k: Maximum number of results to return
            score_threshold: Minimum similarity score (0-1)

        Returns:
            List of chunk dictionaries with text and metadata
        """
        # Generate query embedding through shared runtime for request-time stability.
        runtime = await get_ready_embedding_runtime()
        query_embedding = await runtime.embed_query(
            query_text,
            request_id=f"librarian:{ontology_id}:{abs(hash(query_text)) % 1000000}",
        )

        vector_query = f"""
        CALL db.index.vector.queryNodes(
            'pdf_chunk_text_vec_idx',
            $top_k,
            $query_embedding
        )
        YIELD node as c, score
        RETURN properties(c) as props, score
        ORDER BY score DESC
        """

        internal_candidate_limit = candidate_limit or max(50, top_k * 8)
        result = await self.graph_session.run(
            vector_query,
            query_embedding=query_embedding,
            top_k=internal_candidate_limit,
            ontology_id=ontology_id,
            library_item_ids=library_item_ids,
            active_library_item_ids=active_library_item_ids,
            score_threshold=0.0,
        )

        chunks = []
        requested_ids = set(library_item_ids or [])
        active_ids = set(active_library_item_ids or [])
        if active_library_item_ids is not None and not active_ids:
            logger.info(
                "librarian_retrieval_no_hits reason=no_active_vectorized_items ontology_id=%s",
                ontology_id,
            )
            return []
        base_url = (
            self.settings.media_public_url.rstrip("/")
            if self.settings.media_public_url
            else self.settings.media_base_url.rstrip("/")
        )
        async for record in result:
            props = record.get("props") or {}
            chunk = self._chunk_from_props(
                props=props,
                raw_score=float(record["score"]),
                score_key="vector_score",
                ontology_id=ontology_id,
                requested_ids=requested_ids,
                active_ids=active_ids,
                base_url=base_url,
            )
            if chunk:
                chunks.append(chunk)
        if hybrid_rerank:
            fulltext_chunks = await self._search_fulltext_chunks(
                query_text=query_text,
                ontology_id=ontology_id,
                library_item_ids=library_item_ids,
                active_library_item_ids=active_library_item_ids,
                limit=internal_candidate_limit,
                base_url=base_url,
            )
            chunks = self._merge_hybrid_candidates(chunks + fulltext_chunks)
        if not chunks:
            logger.info(
                "librarian_retrieval_no_hits reason=no_chunk_candidates_after_property_compat_filter ontology_id=%s requested_item_ids=%s active_item_ids_count=%s",
                ontology_id,
                sorted(requested_ids) if requested_ids else None,
                len(active_ids),
            )
        return self._rerank_and_select_chunks(
            query_text=query_text,
            chunks=chunks,
            top_k=top_k,
            score_threshold=score_threshold,
            hybrid_rerank=hybrid_rerank,
            max_chunks_per_item=max_chunks_per_item,
            dynamic_score_floor=dynamic_score_floor,
        )

    async def _search_fulltext_chunks(
        self,
        *,
        query_text: str,
        ontology_id: int,
        library_item_ids: list[int] | None,
        active_library_item_ids: list[int] | None,
        limit: int,
        base_url: str,
    ) -> list[dict[str, Any]]:
        query = """
        CALL db.index.fulltext.queryNodes('pdf_chunk_text_fulltext_idx', $fulltext_query)
        YIELD node as c, score
        RETURN properties(c) as props, score
        ORDER BY score DESC
        LIMIT $limit
        """
        requested_ids = set(library_item_ids or [])
        active_ids = set(active_library_item_ids or [])

        async def _collect_rows() -> list[tuple[dict[str, Any], float]]:
            result = await self.graph_session.run(
                query,
                fulltext_query=self._fulltext_query(query_text),
                limit=limit,
            )
            rows: list[tuple[dict[str, Any], float]] = []
            async for record in result:
                rows.append((record.get("props") or {}, float(record.get("score") or 0.0)))
            return rows

        raw_rows: list[tuple[dict[str, Any], float]] = []
        try:
            raw_rows = await _collect_rows()
        except Exception as exc:
            if "no such fulltext schema index" in str(exc).lower():
                logger.warning(
                    "librarian_fulltext_index_missing ontology_id=%s error=%s; attempting to create indexes",
                    ontology_id,
                    exc,
                )
                try:
                    await self.ensure_vector_index()
                    raw_rows = await _collect_rows()
                except Exception as retry_exc:
                    logger.warning(
                        "librarian_fulltext_search_failed_after_index_bootstrap ontology_id=%s error=%s",
                        ontology_id,
                        retry_exc,
                    )
                    return []
            else:
                logger.warning("librarian_fulltext_search_failed ontology_id=%s error=%s", ontology_id, exc)
                return []

        max_score = max((score for _, score in raw_rows), default=1.0)
        chunks: list[dict[str, Any]] = []
        for props, raw_score in raw_rows:
            normalized_score = min(1.0, raw_score / max(1.0, max_score))
            chunk = self._chunk_from_props(
                props=props,
                raw_score=normalized_score,
                score_key="fulltext_score",
                ontology_id=ontology_id,
                requested_ids=requested_ids,
                active_ids=active_ids,
                base_url=base_url,
            )
            if chunk:
                chunks.append(chunk)
        return chunks

    def _fulltext_query(self, query_text: str) -> str:
        tokens = self._tokenize(query_text)
        if not tokens:
            return query_text.strip()
        important = [tok for tok in tokens if len(tok) > 2][:12]
        if not important:
            return query_text.strip()
        escaped = [re.sub(r'([+\\&|!(){}\[\]^"~*?:/\-])', r"\\\1", tok) for tok in important]
        return " OR ".join(f"{tok}~" for tok in escaped)

    def _chunk_from_props(
        self,
        *,
        props: dict[str, Any],
        raw_score: float,
        score_key: str,
        ontology_id: int,
        requested_ids: set[int],
        active_ids: set[int],
        base_url: str,
    ) -> dict[str, Any] | None:
        li_raw = props.get("library_item_id")
        if li_raw is None:
            return None
        try:
            li = int(li_raw)
        except (TypeError, ValueError):
            return None
        if requested_ids and li not in requested_ids:
            return None
        if active_ids and li not in active_ids:
            return None
        node_ontology = props.get("ontology_id")
        if node_ontology is not None:
            try:
                if int(node_ontology) != int(ontology_id):
                    return None
            except (TypeError, ValueError):
                return None
        page = int(
            props.get("primary_page_number")
            or props.get("page_number")
            or props.get("start_page_number")
            or 1
        )
        page_numbers = props.get("page_numbers")
        if not isinstance(page_numbers, list) or not page_numbers:
            page_numbers = [page]
        chunk_index = int(props.get("chunk_index") or 0)
        pdf_url = f"{base_url}/library/{ontology_id}/{li}/content.pdf"
        page_url = f"{pdf_url}#page={page}"
        return {
            "library_item_id": li,
            "chunk_index": chunk_index,
            "page_number": page,
            "start_page_number": props.get("start_page_number") or page,
            "end_page_number": props.get("end_page_number") or page,
            "page_numbers": page_numbers,
            "chunk_type": props.get("chunk_type") or "text",
            "text": props.get("text") or "",
            "vector_score": raw_score if score_key == "vector_score" else 0.0,
            "fulltext_score": raw_score if score_key == "fulltext_score" else 0.0,
            "score": raw_score,
            "pdf_url": pdf_url,
            "page_url": page_url,
        }

    def _merge_hybrid_candidates(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[tuple[int, int], dict[str, Any]] = {}
        for chunk in chunks:
            key = (int(chunk.get("library_item_id", 0)), int(chunk.get("chunk_index", 0)))
            existing = merged.get(key)
            if existing is None:
                merged[key] = dict(chunk)
                continue
            existing["vector_score"] = max(float(existing.get("vector_score", 0.0)), float(chunk.get("vector_score", 0.0)))
            existing["fulltext_score"] = max(float(existing.get("fulltext_score", 0.0)), float(chunk.get("fulltext_score", 0.0)))
            existing["score"] = max(float(existing.get("score", 0.0)), float(chunk.get("score", 0.0)))
        return list(merged.values())

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"[A-Za-z0-9_]+", (text or "").lower())

    def _lexical_score(self, query_text: str, chunk_text: str) -> float:
        query_tokens = self._tokenize(query_text)
        if not query_tokens:
            return 0.0
        chunk_tokens = self._tokenize(chunk_text)
        if not chunk_tokens:
            return 0.0
        chunk_set = set(chunk_tokens)
        overlap = sum(1 for tok in query_tokens if tok in chunk_set)
        density = overlap / max(1, len(query_tokens))
        tf_bonus = min(1.0, sum(chunk_tokens.count(tok) for tok in set(query_tokens)) / 10.0)
        return min(1.0, 0.8 * density + 0.2 * tf_bonus)

    def _rerank_and_select_chunks(
        self,
        *,
        query_text: str,
        chunks: list[dict[str, Any]],
        top_k: int,
        score_threshold: float,
        hybrid_rerank: bool,
        max_chunks_per_item: int | None,
        dynamic_score_floor: bool,
    ) -> list[dict[str, Any]]:
        if not chunks:
            return []
        best_vector = max(float(ch.get("vector_score", 0.0)) for ch in chunks)
        threshold = float(score_threshold)
        if dynamic_score_floor:
            threshold = max(threshold, best_vector * 0.75)

        scored: list[dict[str, Any]] = []
        for chunk in chunks:
            vector_score = float(chunk.get("vector_score", chunk.get("score", 0.0)))
            lexical = self._lexical_score(query_text, chunk.get("text", "")) if hybrid_rerank else 0.0
            fulltext_score = float(chunk.get("fulltext_score", 0.0))
            page_span = max(
                1,
                int(chunk.get("end_page_number") or chunk.get("page_number") or 1)
                - int(chunk.get("start_page_number") or chunk.get("page_number") or 1)
                + 1,
            )
            span_penalty = min(0.10, max(0.0, (page_span - 2) * 0.02))
            if hybrid_rerank:
                exact_bonus = 0.15 if lexical >= 0.95 or fulltext_score >= 0.95 else 0.0
                final_score = (
                    0.35 * vector_score
                    + 0.35 * fulltext_score
                    + 0.30 * lexical
                    + exact_bonus
                ) - span_penalty
            else:
                final_score = vector_score - span_penalty
            if final_score >= threshold:
                scored_chunk = dict(chunk)
                scored_chunk["lexical_score"] = lexical
                scored_chunk["score"] = final_score
                scored.append(scored_chunk)

        scored.sort(
            key=lambda ch: (
                -float(ch.get("score", 0.0)),
                int(ch.get("library_item_id", 0)),
                int(ch.get("chunk_index", 0)),
            )
        )
        if max_chunks_per_item is None:
            return scored[:top_k]

        selected: list[dict[str, Any]] = []
        per_item: dict[int, int] = {}
        for ch in scored:
            item_id = int(ch.get("library_item_id", 0))
            if per_item.get(item_id, 0) >= max_chunks_per_item:
                continue
            per_item[item_id] = per_item.get(item_id, 0) + 1
            selected.append(ch)
            if len(selected) >= top_k:
                break
        return selected

    async def fetch_neighbor_text(
        self, library_item_id: int, page_number: int
    ) -> dict[int, str]:
        """Fetch text for neighboring pages (page-1 and page+1) for context."""
        query = """
        MATCH (c:PdfChunk {library_item_id: $item_id})
        WHERE c.start_page_number <= $p2
          AND c.end_page_number >= $p1
        RETURN c.page_number AS page_number, c.text AS text
        """
        p1 = page_number - 1
        p2 = page_number + 1
        result = await self.graph_session.run(
            query, item_id=library_item_id, p1=p1, p2=p2
        )
        rows = await result.data()
        out: dict[int, str] = {}
        for r in rows:
            pn = r.get("page_number")
            txt = r.get("text") or ""
            if pn:
                out[int(pn)] = txt
        return out

    async def fetch_chunks_by_page_anchors(
        self,
        *,
        ontology_id: int,
        page_anchors: list[dict[str, Any]],
        library_item_ids: list[int] | None = None,
        active_library_item_ids: list[int] | None = None,
        radius: int = 0,
    ) -> list[dict[str, Any]]:
        if not page_anchors:
            return []
        if active_library_item_ids is not None and not active_library_item_ids:
            return []
        pages = sorted({
            page
            for anchor in page_anchors
            for page in range(
                max(1, int(anchor.get("page") or 1) - max(0, radius)),
                int(anchor.get("page") or 1) + max(0, radius) + 1,
            )
        })
        query = """
        MATCH (c:PdfChunk)
        WHERE c.ontology_id = $ontology_id
          AND ANY(page IN $pages WHERE c.start_page_number <= page AND c.end_page_number >= page)
        RETURN properties(c) as props, 1.0 as score
        ORDER BY c.library_item_id ASC, c.chunk_index ASC
        """
        base_url = (
            self.settings.media_public_url.rstrip("/")
            if self.settings.media_public_url
            else self.settings.media_base_url.rstrip("/")
        )
        requested_ids = set(library_item_ids or [])
        active_ids = set(active_library_item_ids or [])
        result = await self.graph_session.run(
            query,
            ontology_id=ontology_id,
            pages=pages,
        )
        chunks: list[dict[str, Any]] = []
        async for record in result:
            chunk = self._chunk_from_props(
                props=record.get("props") or {},
                raw_score=float(record.get("score") or 1.0),
                score_key="fulltext_score",
                ontology_id=ontology_id,
                requested_ids=requested_ids,
                active_ids=active_ids,
                base_url=base_url,
            )
            if chunk and any(
                int(chunk.get("start_page_number") or chunk["page_number"]) <= page <= int(chunk.get("end_page_number") or chunk["page_number"])
                for page in pages
            ):
                chunk["score"] = max(float(chunk.get("score", 0.0)), 1.0)
                chunks.append(chunk)
        return self._merge_hybrid_candidates(chunks)

    async def expand_chunks_by_page_neighbors(
        self,
        chunks: list[dict[str, Any]],
        *,
        radius: int,
        ontology_id: int,
        library_item_ids: list[int] | None = None,
        active_library_item_ids: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        if radius <= 0 or not chunks:
            return chunks
        anchors = [
            {"page": int(page)}
            for chunk in chunks
            for page in range(
                int(chunk.get("start_page_number") or chunk.get("page_number") or 1),
                int(chunk.get("end_page_number") or chunk.get("page_number") or 1) + 1,
            )
        ]
        expanded = await self.fetch_chunks_by_page_anchors(
            ontology_id=ontology_id,
            page_anchors=anchors,
            library_item_ids=library_item_ids,
            active_library_item_ids=active_library_item_ids,
            radius=radius,
        )
        return self._merge_hybrid_candidates(chunks + expanded)

    async def enrich_chunks_with_neighbors(
        self, chunks: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Append neighbor page text to each chunk's text for better context.

        Keeps the main page_number for citation. Adds separators before/after.
        """
        enriched: list[dict[str, Any]] = []
        for ch in chunks:
            try:
                neighbors = await self.fetch_neighbor_text(
                    ch["library_item_id"], ch["page_number"]
                )
                parts = [ch.get("text", "").strip()]
                if neighbors.get(ch["page_number"] - 1):
                    parts.insert(
                        0,
                        f"[Neighbor p.{ch['page_number']-1}] "
                        + neighbors[ch["page_number"] - 1].strip(),
                    )
                if neighbors.get(ch["page_number"] + 1):
                    parts.append(
                        f"[Neighbor p.{ch['page_number']+1}] "
                        + neighbors[ch["page_number"] + 1].strip()
                    )
                ch2 = dict(ch)
                ch2["text"] = "\n\n".join([p for p in parts if p])
                enriched.append(ch2)
            except Exception:
                enriched.append(ch)
        return enriched
