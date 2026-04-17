"""Librarian orchestrator for PDF-based question answering."""

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.llm.openai_client import OpenAIClient
from app.jobs.librarian.prompts import SIMPLIFIED_ANSWER_STYLE_PROMPT
from app.jobs.librarian.schemas import (
    LibrarianQueryRequest,
    LibrarianQueryResponse,
    RetrievedChunk,
)
from app.models.agent import Agent
from app.services.pdf_embedding_service import PdfEmbeddingService

logger = logging.getLogger(__name__)


class LibrarianOrchestrator:
    """
    Orchestrates the Librarian pipeline:
    Retrieve → Answer → Style (if configured)
    """

    def __init__(
        self,
        llm_client: OpenAIClient,
        pdf_embedding_service: PdfEmbeddingService,
        default_top_k: int = 10,
        answer_model: str = "gpt-4o",
        style_model: str = "gpt-4o-mini",
        fast_mode: bool = True,
        max_fast_chunks: int = 6,
    ):
        """
        Initialize orchestrator.

        Args:
            llm_client: OpenAI client for LLM calls
            pdf_embedding_service: Service for PDF chunk retrieval
            default_top_k: Default number of chunks to retrieve
            answer_model: Model for answer generation
            style_model: Model for style application
        """
        self.llm_client = llm_client
        self.pdf_embedding_service = pdf_embedding_service
        self.default_top_k = default_top_k
        self.answer_model = answer_model
        self.style_model = style_model
        self.fast_mode = fast_mode
        self.max_fast_chunks = max_fast_chunks

    async def execute(
        self,
        agent: Agent,
        request: LibrarianQueryRequest,
        db_session: AsyncSession,
    ) -> LibrarianQueryResponse:
        """
        Execute the Librarian pipeline for a query.

        Simplified Pipeline:
        1. Retrieve top 10 chunks across all library items in the ontology
        2. Generate answer with proper citations and sources
        3. Track which sources were actually used

        Args:
            agent: Agent instance with configuration
            request: Query request
            db_session: Database session for fetching library metadata

        Returns:
            Query response with answer and/or chunks
        """
        trace: list[dict[str, Any]] = [] if request.include_trace else []
        top_k = request.top_k or self.default_top_k

        # Get ontology IDs from agent
        ontology_ids = [ont.id for ont in agent.ontologies]

        # Step 1: Retrieve top chunks across all library items in ontology
        all_chunks = []
        for ontology_id in ontology_ids:
            chunks = await self._retrieve_chunks(
                query=request.query,
                ontology_id=ontology_id,
                library_item_ids=request.library_item_ids,
                top_k=top_k,
                trace=trace,
                score_threshold=request.score_threshold if request.score_threshold is not None else 0.3,
            )
            all_chunks.extend(chunks)

        # Sort by score and take top_k across all ontologies
        # Note: For typical use (1-3 ontologies, ~30 total chunks), sorting is efficient.
        # If scaling to many ontologies, consider using heapq.nlargest for better performance.
        all_chunks.sort(key=lambda x: x["score"], reverse=True)
        all_chunks = all_chunks[:top_k]

        # Fetch library metadata for the chunks we retrieved
        library_item_ids = list({chunk["library_item_id"] for chunk in all_chunks})
        library_metadata = await self._fetch_library_metadata(
            db_session, library_item_ids
        )

        # Convert to schema objects
        retrieved_chunks = []
        for chunk in all_chunks:
            metadata_for_item = library_metadata.get(chunk["library_item_id"], {})
            retrieved_chunks.append(
                RetrievedChunk(
                    library_item_id=chunk["library_item_id"],
                    page_number=chunk["page_number"],
                    text=chunk["text"],
                    score=chunk["score"],
                    pdf_url=chunk.get("pdf_url"),
                    page_url=chunk.get("page_url"),
                    book_title=metadata_for_item.get("title"),
                    book_authors=metadata_for_item.get("authors"),
                )
            )

        # Step 2: Generate answer if mode includes 'nl'
        answer = None
        sources_used = []

        if request.mode in ("nl", "both"):
            if not retrieved_chunks:
                answer = (
                    "I couldn't find any relevant information in the available "
                    "books to answer your question."
                )
            else:
                # Generate answer with proper citations
                answer = await self._generate_answer_with_style(
                    query=request.query,
                    chunks=retrieved_chunks,
                    writing_style=agent.writing_style,
                    trace=trace,
                )

                # Extract sources that were actually used in the answer
                sources_used = self._extract_sources_from_answer(
                    answer, retrieved_chunks
                )

        # Get unique library items used
        library_items_used = list({chunk.library_item_id for chunk in sources_used})
        if not library_items_used:
            library_items_used = list({chunk.library_item_id for chunk in retrieved_chunks})

        # Log summarized result (query, source titles, final answer)
        source_chunks_for_log = sources_used or retrieved_chunks
        source_titles = []
        for chunk in source_chunks_for_log:
            title = chunk.book_title or f"Library item {chunk.library_item_id}"
            if title not in source_titles:
                source_titles.append(title)

        final_answer = answer if request.mode in ("nl", "both") else None
        logger.info(
            "librarian_query_result user_query=%s sources=%s response=%s",
            request.query,
            source_titles,
            final_answer,
        )

        # Return response based on mode
        return LibrarianQueryResponse(
            agent_id=agent.id,
            mode=request.mode,
            query=request.query,
            subqueries=[],  # No subqueries in simplified version
            answer=final_answer,
            chunks=retrieved_chunks if request.mode in ("context", "both") else [],
            sources_used=sources_used,
            library_items_used=library_items_used,
            trace=trace if request.include_trace else None,
        )

    async def _retrieve_chunks(
        self,
        query: str,
        ontology_id: int,
        library_item_ids: list[int] | None,
        top_k: int,
        trace: list[dict[str, Any]],
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieve relevant chunks from PDFs.

        Args:
            query: User query
            ontology_id: Ontology ID to search within
            library_item_ids: Optional list of library items to filter
            top_k: Number of chunks to retrieve
            trace: Trace list to append to

        Returns:
            List of retrieved chunk dictionaries
        """
        chunks = await self.pdf_embedding_service.search_chunks(
            query_text=query,
            ontology_id=ontology_id,
            library_item_ids=library_item_ids,
            top_k=top_k,
            score_threshold=score_threshold if score_threshold is not None else 0.3,
        )

        # Enrich chunks with neighbor pages to improve context quality
        if not self.fast_mode:
            chunks = await self.pdf_embedding_service.enrich_chunks_with_neighbors(
                chunks
            )

        if trace is not None:
            trace.append(
                {
                    "step": "retrieve",
                    "data": {
                        "ontology_id": ontology_id,
                        "library_item_ids": library_item_ids,
                        "chunks_found": len(chunks),
                        "top_k": top_k,
                    },
                }
            )

        return chunks

    async def _fetch_library_metadata(
        self,
        db_session: AsyncSession,
        library_item_ids: list[int],
    ) -> dict[int, dict[str, str | None]]:
        """
        Fetch library item metadata (title, authors) from the database.

        Args:
            db_session: Database session
            library_item_ids: List of library item IDs to fetch

        Returns:
            Dictionary mapping library_item_id to metadata dict with title and authors
        """
        if not library_item_ids:
            return {}

        from sqlalchemy import select

        from app.models.library import LibraryItem

        # Batch fetch all library items in a single query
        query = select(LibraryItem).where(LibraryItem.id.in_(library_item_ids))
        result = await db_session.execute(query)
        items = result.scalars().all()

        # Build metadata dictionary
        metadata = {}
        for item in items:
            metadata[item.id] = {
                "title": item.title,
                "authors": item.authors,
            }

        # Add None entries for any missing items
        for item_id in library_item_ids:
            if item_id not in metadata:
                logger.warning(f"Library item {item_id} not found in database")
                metadata[item_id] = {"title": None, "authors": None}

        return metadata

    async def _generate_answer_with_style(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        writing_style: str | None,
        trace: list[dict[str, Any]],
    ) -> str:
        """
        Generate answer with proper citations and applied writing style.

        Args:
            query: User query
            chunks: Retrieved chunks
            writing_style: Writing style description
            trace: Trace list to append to

        Returns:
            Generated answer with Markdown cite wrappers
        """
        # Format chunks for prompt with book title and page
        chunks_text = ""
        for i, chunk in enumerate(chunks, 1):
            book_title = chunk.book_title or f"Book #{chunk.library_item_id}"
            chunks_text += (
                f"\n--- Source {i}: {book_title}, Page {chunk.page_number} "
                f"(library_item_id={chunk.library_item_id}) ---\n"
                f"{chunk.text}\n"
            )

        # Use simplified prompt without subqueries section
        style_text = writing_style or "Use a clear, direct tone suitable for game masters."
        prompt = SIMPLIFIED_ANSWER_STYLE_PROMPT.format(
            query=query,
            chunks=chunks_text,
            writing_style=style_text,
        )

        system_msg = (
            "You are a knowledgeable librarian. Answer using ONLY the provided excerpts. "
            "For EVERY piece of information, wrap the cited text like "
            '[text]{cite library_item_id=ID library_item_name="TITLE" page=PAGE}. '
            "Apply the writing style while preserving all facts."
        )

        answer = await self.llm_client.chat(
            model=self.answer_model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )

        if trace is not None:
            trace.append(
                {
                    "step": "answer_with_style",
                    "data": {
                        "model": self.answer_model,
                        "chunks_used": len(chunks),
                        "preview": (
                            answer[:200] + "…" if len(answer or "") > 200 else answer
                        ),
                    },
                }
            )

        return answer

    def _extract_sources_from_answer(
        self, answer: str, all_chunks: list[RetrievedChunk]
    ) -> list[RetrievedChunk]:
        """
        Extract sources that were actually cited in the answer.

        Args:
            answer: Generated answer with cite wrappers
            all_chunks: All available chunks

        Returns:
            List of chunks that were actually cited
        """
        if not answer:
            return []

        # Extract all library_item_id and page combinations from cite wrappers
        # Pattern handles attributes in any order inside {cite ...}
        pattern = r'\{cite[^}]*library_item_id\s*=\s*(\d+)[^}]*page\s*=\s*(\d+)[^}]*\}'
        matches = re.findall(pattern, answer)

        # Also try the reverse order (page first)
        pattern_reverse = r'\{cite[^}]*page\s*=\s*(\d+)[^}]*library_item_id\s*=\s*(\d+)[^}]*\}'
        matches_reverse = re.findall(pattern_reverse, answer)

        # Combine matches (reverse the tuple order for reverse pattern)
        cited_sources = {(int(item_id), int(page)) for item_id, page in matches}
        cited_sources.update({(int(item_id), int(page)) for page, item_id in matches_reverse})

        # Filter chunks to only those that were cited
        sources_used = []
        for chunk in all_chunks:
            if (chunk.library_item_id, chunk.page_number) in cited_sources:
                sources_used.append(chunk)

        return sources_used

