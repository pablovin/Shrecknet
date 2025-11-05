"""Service for embedding PDF books into Neo4j for librarian queries."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from neo4j import AsyncSession as AsyncNeo4jSession

from app.core.config import get_settings
from app.graphrag.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)

settings = get_settings()


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
            # Import PDF processing here to avoid dependency issues
            # if pypdf2 is not installed
            try:
                from PyPDF2 import PdfReader
            except ImportError:
                raise ImportError(
                    "PyPDF2 is required for PDF embedding. "
                    "Install with: pip install PyPDF2"
                )

            # Ensure vector index exists
            await self.ensure_vector_index()

            # Read PDF content (prefer PyMuPDF for better text extraction)
            logger.info(f"Reading PDF from {pdf_path}")
            use_fitz = False
            page_labels: list[str] | None = None

            try:
                import fitz  # PyMuPDF

                doc = fitz.open(str(pdf_path))
                total_pages = len(doc)
                use_fitz = True

                # Try to get page labels from PyMuPDF
                try:
                    page_labels_list = doc.get_page_labels()  # Get once and reuse
                    if page_labels_list:
                        page_labels_fitz = []
                        for page_idx in range(total_pages):
                            # Get the page label if it exists
                            label = (
                                page_labels_list[page_idx]
                                if page_idx < len(page_labels_list)
                                else None
                            )
                            if label:
                                page_labels_fitz.append(str(label))
                            else:
                                page_labels_fitz.append(None)
                        # Only use labels if we got at least some non-None values
                        if any(l is not None for l in page_labels_fitz):
                            page_labels = page_labels_fitz
                except Exception as e:
                    logger.debug(f"Could not extract page labels from PyMuPDF: {e}")
                    page_labels = None

            except Exception:
                logger.info("PyMuPDF not available; falling back to PyPDF2 for reading")
                doc = None
                reader = PdfReader(str(pdf_path))
                total_pages = len(reader.pages)

                # Try to get display page labels (may differ from index+1) when using PyPDF2
                try:
                    if hasattr(reader, "get_page_labels"):
                        labels = reader.get_page_labels()
                        if isinstance(labels, list) and labels:
                            page_labels = [str(l) for l in labels]
                except Exception:
                    page_labels = None

            chunks_created = 0
            chunks_failed = 0

            # Process in batches
            for start_page in range(0, total_pages, batch_size):
                end_page = min(start_page + batch_size, total_pages)
                batch_chunks = []

                # Extract text from pages in this batch
                for page_num in range(start_page, end_page):
                    try:
                        if use_fitz and doc is not None:
                            page = doc[page_num]
                            text = page.get_text("text")
                        else:
                            page = reader.pages[page_num]
                            text = page.extract_text()

                        if text and text.strip():
                            # Create chunk data
                            # Determine display page number using labels if available
                            display_page = page_num + 1  # Default: 1-indexed

                            if page_labels and page_num < len(page_labels):
                                label = page_labels[page_num]
                                if label:
                                    try:
                                        # Try to parse as integer
                                        display_page = int(label)
                                    except (ValueError, TypeError):
                                        # Keep default if label is not numeric
                                        logger.debug(
                                            f"Non-numeric page label '{label}' for page {page_num}, "
                                            f"using {display_page}"
                                        )

                            chunk = {
                                "library_item_id": library_item_id,
                                "ontology_id": ontology_id,
                                "page_number": display_page,
                                "text": text.strip(),
                                "chunk_index": page_num,
                            }
                            batch_chunks.append(chunk)
                    except Exception as e:
                        logger.warning(
                            f"Failed to extract text from page {page_num + 1}: {e}"
                        )
                        chunks_failed += 1

                # Create embeddings for batch
                if batch_chunks:
                    try:
                        await self._embed_chunks_batch(batch_chunks)
                        chunks_created += len(batch_chunks)
                        logger.info(
                            f"Embedded pages {start_page + 1}-{end_page} "
                            f"({len(batch_chunks)} chunks)"
                        )
                    except Exception as e:
                        logger.error(f"Failed to embed batch: {e}")
                        chunks_failed += len(batch_chunks)

            # Close PyMuPDF document if used
            try:
                if use_fitz and doc is not None:
                    doc.close()
            except Exception:
                pass

            return {
                "library_item_id": library_item_id,
                "ontology_id": ontology_id,
                "total_pages": total_pages,
                "chunks_created": chunks_created,
                "chunks_failed": chunks_failed,
                "status": "success" if chunks_created > 0 else "failed",
            }

        except Exception as e:
            logger.error(f"PDF embedding failed: {e}", exc_info=True)
            raise

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
               c.page_number as page_number,
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
        # Build base URL for media
        base_url = (
            settings.media_public_url.rstrip("/")
            if settings.media_public_url
            else settings.media_base_url.rstrip("/")
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
                    "text": record["text"],
                    "score": record["score"],
                    "pdf_url": pdf_url,
                    "page_url": page_url,
                }
            )

        # Return top_k results
        return chunks[:top_k]

    async def fetch_neighbor_text(
        self, library_item_id: int, page_number: int
    ) -> dict[int, str]:
        """Fetch text for neighboring pages (page-1 and page+1) for context."""
        query = """
        MATCH (c:PdfChunk {library_item_id: $item_id})
        WHERE c.page_number IN [$p1, $p2]
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
