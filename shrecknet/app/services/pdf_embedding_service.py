"""Service for embedding PDF books into Neo4j for librarian queries."""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any

from neo4j import AsyncSession as AsyncNeo4jSession

from app.core.config_store import get_settings
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

        return {
            "index_name": index_name,
            "exists": exists,
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

        text = "\n".join(lines)
        text = text.replace("\x00", " ")
        text = re.sub(r"-\n(?=\w)", "", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _clean_lines(self, text: str) -> list[str]:
        return [line.strip() for line in text.splitlines() if line and line.strip()]

    def _is_meaningful_text(self, text: str) -> bool:
        if len(text) < 40:
            return False
        alpha_count = sum(1 for char in text if char.isalpha())
        return alpha_count >= 20

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
                    "chunk_type": "text",
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

    async def search_chunks(
        self,
        query_text: str,
        ontology_id: int,
        library_item_ids: list[int] | None = None,
        top_k: int = 10,
        score_threshold: float = 0.5,
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
        # Generate query embedding
        query_embedding = self.embedding_service.embed_text(query_text)

        # Build query
        if library_item_ids:
            item_filter = "AND c.library_item_id IN $library_item_ids"
        else:
            item_filter = ""

        query = f"""
        CALL db.index.vector.queryNodes(
            'pdf_chunk_text_vec_idx',
            $top_k,
            $query_embedding
        )
        YIELD node as c, score
        WHERE c.ontology_id = $ontology_id {item_filter}
            AND score >= $score_threshold
        RETURN c.library_item_id as library_item_id,
               c.chunk_index as chunk_index,
               coalesce(c.primary_page_number, c.page_number) as page_number,
               c.start_page_number as start_page_number,
               c.end_page_number as end_page_number,
               c.page_numbers as page_numbers,
               c.text as text,
               score
        ORDER BY score DESC
        """

        result = await self.graph_session.run(
            query,
            query_embedding=query_embedding,
            top_k=top_k * 2,  # Fetch more to account for filtering
            ontology_id=ontology_id,
            library_item_ids=library_item_ids,
            score_threshold=score_threshold,
        )

        chunks = []
        base_url = (
            self.settings.media_public_url.rstrip("/")
            if self.settings.media_public_url
            else self.settings.media_base_url.rstrip("/")
        )
        async for record in result:
            li = record["library_item_id"]
            page = record["page_number"]
            pdf_url = f"{base_url}/library/{ontology_id}/{li}/content.pdf"
            page_url = f"{pdf_url}#page={page}"
            chunks.append(
                {
                    "library_item_id": li,
                    "chunk_index": record["chunk_index"],
                    "page_number": page,
                    "start_page_number": record.get("start_page_number"),
                    "end_page_number": record.get("end_page_number"),
                    "page_numbers": record.get("page_numbers") or [page],
                    "text": record["text"],
                    "score": record["score"],
                    "pdf_url": pdf_url,
                    "page_url": page_url,
                }
            )

        return chunks[:top_k]

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
