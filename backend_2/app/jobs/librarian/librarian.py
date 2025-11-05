"""Librarian orchestrator for PDF-based question answering."""

import asyncio
import json
import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.llm.openai_client import OpenAIClient
from app.jobs.librarian.prompts import (
    ANSWER_PROMPT,
    FAST_SINGLE_PASS_PROMPT,
    STYLE_PROMPT,
    COMBINED_ANSWER_STYLE_PROMPT,
    SUBQUERY_GENERATION_PROMPT,
)
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

    async def _generate_subqueries(
        self,
        query: str,
        library_metadata: dict[int, dict[str, str | None]],
        trace: list[dict[str, Any]],
    ) -> list[str]:
        """
        Generate up to 4 focused subqueries to help answer the main question.

        Args:
            query: Main user query
            library_metadata: Metadata about available books
            trace: Trace list to append to

        Returns:
            List of subqueries (0-4 items)
        """
        logger.info("Generating subqueries for query: %s", query[:50])

        # Build book context from available library items
        book_titles = [
            meta.get("title", "Unknown")
            for meta in library_metadata.values()
            if meta.get("title")
        ]
        book_context = ", ".join(book_titles[:5]) if book_titles else "various game books"

        prompt = SUBQUERY_GENERATION_PROMPT.format(
            query=query,
            book_context=book_context,
        )

        system_msg = (
            "You are a librarian assistant helping to decompose complex questions "
            "into focused subqueries for better information retrieval."
        )

        try:
            response = await self.llm_client.chat(
                model=self.answer_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )

            # Parse JSON response
            try:
                subqueries = json.loads(response)
                if not isinstance(subqueries, list):
                    logger.warning(
                        "Subquery response was not a list: %s",
                        type(subqueries).__name__
                    )
                    subqueries = []
            except json.JSONDecodeError as e:
                logger.warning(
                    "Failed to parse subqueries JSON: %s. Response: %s",
                    e,
                    response[:200] if response else "<empty>"
                )
                subqueries = []
            
            # Limit to 4 subqueries
            subqueries = [str(sq) for sq in subqueries[:4]]

            logger.info("Generated %d subqueries", len(subqueries))
            if trace is not None:
                trace.append(
                    {
                        "step": "generate_subqueries",
                        "data": {
                            "subqueries": subqueries,
                        },
                    }
                )

            return subqueries

        except Exception as e:
            logger.warning("Failed to generate subqueries: %s", e)
            if trace is not None:
                trace.append(
                    {
                        "step": "generate_subqueries",
                        "data": {"error": str(e)},
                    }
                )
            return []

    async def _retrieve_chunks_for_queries(
        self,
        queries: list[str],
        ontology_ids: list[int],
        library_item_ids: list[int] | None,
        top_k_per_query: int,
        score_threshold: float,
        trace: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Retrieve chunks for multiple queries in parallel.

        Args:
            queries: List of queries (main + subqueries)
            ontology_ids: Ontology IDs to search within
            library_item_ids: Optional list of library items to filter
            top_k_per_query: Number of chunks per query
            score_threshold: Minimum similarity score
            trace: Trace list to append to

        Returns:
            Dictionary mapping query to list of chunks
        """
        logger.info("Retrieving chunks for %d queries in parallel", len(queries))

        # Create retrieval tasks for all query-ontology combinations
        tasks = []
        task_info = []

        for query in queries:
            for ontology_id in ontology_ids:
                tasks.append(
                    self._retrieve_chunks(
                        query=query,
                        ontology_id=ontology_id,
                        library_item_ids=library_item_ids,
                        top_k=top_k_per_query,
                        trace=[],  # Don't pollute trace with each individual retrieval
                        score_threshold=score_threshold,
                    )
                )
                task_info.append({"query": query, "ontology_id": ontology_id})

        # Execute all retrievals in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate results by query
        query_chunks: dict[str, list[dict[str, Any]]] = {q: [] for q in queries}

        for task_meta, result in zip(task_info, results):
            query = task_meta["query"]
            if isinstance(result, Exception):
                logger.warning(
                    "Retrieval failed for query '%s': %s", query[:50], result
                )
            elif isinstance(result, list):
                query_chunks[query].extend(result)

        # Sort and limit chunks for each query
        for query in queries:
            chunks = query_chunks[query]
            chunks.sort(key=lambda x: x.get("score", 0.0), reverse=True)
            query_chunks[query] = chunks[:top_k_per_query]

        if trace is not None:
            trace.append(
                {
                    "step": "parallel_retrieval",
                    "data": {
                        "num_queries": len(queries),
                        "chunks_per_query": {
                            q: len(chunks) for q, chunks in query_chunks.items()
                        },
                    },
                }
            )

        logger.info("Parallel retrieval completed")
        return query_chunks

    async def execute(
        self,
        agent: Agent,
        request: LibrarianQueryRequest,
        db_session: AsyncSession,
    ) -> LibrarianQueryResponse:
        """
        Execute the Librarian pipeline for a query.

        New Pipeline:
        1. Fetch library metadata to understand available books
        2. Generate up to 4 subqueries
        3. Retrieve chunks for main query + subqueries in parallel
        4. Generate answer with proper citations
        5. Track which sources were actually used

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

        # Step 1: Fetch library metadata early to understand what books we have
        # This helps with subquery generation
        if request.library_item_ids:
            library_item_ids = request.library_item_ids
        else:
            # Get all library items for the ontologies
            library_item_ids = await self._get_library_items_for_ontologies(
                db_session, ontology_ids
            )

        library_metadata = await self._fetch_library_metadata(
            db_session, library_item_ids
        )

        # Step 2: Generate subqueries (up to 4)
        subqueries = await self._generate_subqueries(
            request.query, library_metadata, trace
        )

        # Step 3: Retrieve chunks for main query + all subqueries in parallel
        all_queries = [request.query] + subqueries
        top_k_per_query = max(3, top_k // len(all_queries))  # Distribute top_k

        query_chunks = await self._retrieve_chunks_for_queries(
            queries=all_queries,
            ontology_ids=ontology_ids,
            library_item_ids=request.library_item_ids,
            top_k_per_query=top_k_per_query,
            score_threshold=request.score_threshold if request.score_threshold is not None else 0.3,
            trace=trace,
        )

        # Combine and deduplicate chunks by (library_item_id, page_number)
        seen_chunks = set()
        all_chunks = []
        for query in all_queries:
            for chunk in query_chunks.get(query, []):
                chunk_key = (chunk["library_item_id"], chunk["page_number"])
                if chunk_key not in seen_chunks:
                    seen_chunks.add(chunk_key)
                    all_chunks.append(chunk)

        # Sort by score and take top_k
        all_chunks.sort(key=lambda x: x["score"], reverse=True)
        limit = top_k
        if self.fast_mode:
            limit = min(self.max_fast_chunks, top_k)
        all_chunks = all_chunks[:limit]

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

        # Log the sources we will use
        logger.info("librarian_sources_available: %d", len(retrieved_chunks))

        # Step 4: Generate answer if mode includes 'nl'
        answer = None
        sources_used = []

        if request.mode in ("nl", "both"):
            if not retrieved_chunks:
                answer = (
                    "I couldn't find any relevant information in the available "
                    "books to answer your question."
                )
            else:
                # Generate answer with all queries context
                answer = await self._generate_answer_with_subqueries(
                    main_query=request.query,
                    subqueries=subqueries,
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

        # Return response based on mode
        return LibrarianQueryResponse(
            agent_id=agent.id,
            mode=request.mode,
            query=request.query,
            subqueries=subqueries,
            answer=answer if request.mode in ("nl", "both") else None,
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
        logger.info(f"Retrieving chunks for query: {query[:50]}...")

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

        logger.info(f"Retrieved {len(chunks)} chunks")
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

    async def _get_library_items_for_ontologies(
        self,
        db_session: AsyncSession,
        ontology_ids: list[int],
    ) -> list[int]:
        """
        Get all library item IDs for given ontologies.

        Args:
            db_session: Database session
            ontology_ids: List of ontology IDs

        Returns:
            List of library item IDs
        """
        from sqlalchemy import select

        from app.models.library import LibraryItem

        query = select(LibraryItem.id).where(LibraryItem.ontology_id.in_(ontology_ids))
        result = await db_session.execute(query)
        return [row[0] for row in result.all()]

    async def _generate_answer_with_subqueries(
        self,
        main_query: str,
        subqueries: list[str],
        chunks: list[RetrievedChunk],
        writing_style: str | None,
        trace: list[dict[str, Any]],
    ) -> str:
        """
        Generate answer with subqueries context and proper citations.

        Args:
            main_query: Main user query
            subqueries: Generated subqueries
            chunks: Retrieved chunks
            writing_style: Writing style description
            trace: Trace list to append to

        Returns:
            Generated answer with <sub> citations
        """
        logger.info("Generating answer with subqueries context")

        # Format chunks for prompt with book title and page
        chunks_text = ""
        for i, chunk in enumerate(chunks, 1):
            book_title = chunk.book_title or f"Book #{chunk.library_item_id}"
            chunks_text += (
                f"\n--- Source {i}: {book_title}, Page {chunk.page_number} "
                f"(library_item_id={chunk.library_item_id}) ---\n"
                f"{chunk.text}\n"
            )

        # Build subqueries section if any
        subqueries_section = ""
        if subqueries:
            subqueries_list = "\n".join(f"- {sq}" for sq in subqueries)
            subqueries_section = f"""**Sub-questions to help answer the main question:**
{subqueries_list}

"""

        # Use combined prompt
        style_text = writing_style or "Use a clear, direct tone suitable for game masters."
        prompt = COMBINED_ANSWER_STYLE_PROMPT.format(
            query=main_query,
            subqueries_section=subqueries_section,
            chunks=chunks_text,
            writing_style=style_text,
        )

        system_msg = (
            "You are a knowledgeable librarian. Answer using ONLY the provided excerpts. "
            "For EVERY piece of information, cite it using <sub library_item_id=\"ID\" "
            'library_item_name="TITLE" page="PAGE"> tags. Apply the writing style while '
            "preserving all facts."
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
                    "step": "answer_with_subqueries",
                    "data": {
                        "model": self.answer_model,
                        "subqueries_count": len(subqueries),
                        "chunks_used": len(chunks),
                        "preview": (
                            answer[:200] + "…" if len(answer or "") > 200 else answer
                        ),
                    },
                }
            )

        logger.info("Answer with subqueries generated")
        return answer

    def _extract_sources_from_answer(
        self, answer: str, all_chunks: list[RetrievedChunk]
    ) -> list[RetrievedChunk]:
        """
        Extract sources that were actually cited in the answer.

        Args:
            answer: Generated answer with <sub> tags
            all_chunks: All available chunks

        Returns:
            List of chunks that were actually cited
        """
        if not answer:
            return []

        # Extract all library_item_id and page combinations from <sub> tags
        # Pattern handles attributes in any order
        pattern = r'<sub\s+(?:[^>]*\s+)?library_item_id="(\d+)"(?:[^>]*\s+)?page="(\d+)"'
        matches = re.findall(pattern, answer)

        # Also try the reverse order
        pattern_reverse = r'<sub\s+(?:[^>]*\s+)?page="(\d+)"(?:[^>]*\s+)?library_item_id="(\d+)"'
        matches_reverse = re.findall(pattern_reverse, answer)
        
        # Combine matches (reverse the tuple order for reverse pattern)
        cited_sources = {(int(item_id), int(page)) for item_id, page in matches}
        cited_sources.update({(int(item_id), int(page)) for page, item_id in matches_reverse})

        # Filter chunks to only those that were cited
        sources_used = []
        for chunk in all_chunks:
            if (chunk.library_item_id, chunk.page_number) in cited_sources:
                sources_used.append(chunk)

        logger.info(
            "Extracted %d sources from answer (from %d available)",
            len(sources_used),
            len(all_chunks),
        )

        return sources_used

    async def _generate_combined_answer(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        writing_style: str | None,
        trace: list[dict[str, Any]],
    ) -> str:
        """
        Generate answer with style applied in a single LLM call for better performance.

        Args:
            query: User query
            chunks: Retrieved chunks
            writing_style: Writing style description
            trace: Trace list to append to

        Returns:
            Generated styled answer
        """
        logger.info("Generating combined answer with style from chunks")

        # Format chunks for prompt
        chunks_text = ""
        for i, chunk in enumerate(chunks, 1):
            chunks_text += (
                f"\n--- Excerpt {i} (Page {chunk.page_number}, "
                f"Book ID: {chunk.library_item_id}) ---\n"
                f"{chunk.text}\n"
            )

        # Use combined prompt
        style_text = writing_style or "Use a clear, direct tone suitable for game masters."
        prompt = COMBINED_ANSWER_STYLE_PROMPT.format(
            query=query,
            chunks=chunks_text,
            writing_style=style_text,
        )

        system_msg = (
            "You are a knowledgeable librarian. Answer using ONLY the provided excerpts, "
            "applying the specified writing style while preserving all facts and citations."
        )

        # Log prompt for debugging
        try:
            logger.info("librarian_combined_answer_prompt:\n%s", prompt[:2000])
        except Exception:
            pass

        answer = await self.llm_client.chat(
            model=self.answer_model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )

        if trace is not None:
            try:
                trace.append(
                    {
                        "step": "answer_combined",
                        "data": {
                            "model": self.answer_model,
                            "chunks_used": len(chunks),
                            "preview": (
                                answer[:200] + "…" if len(answer or "") > 200 else answer
                            ),
                        },
                    }
                )
            except Exception:
                pass

        logger.info("Combined answer with style generated")
        return answer

    async def _generate_answer(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        trace: list[dict[str, Any]],
    ) -> str:
        """
        Generate answer from retrieved chunks.

        Args:
            query: User query
            chunks: Retrieved chunks
            trace: Trace list to append to

        Returns:
            Generated answer
        """
        logger.info("Generating answer from chunks")

        # Format chunks for prompt
        chunks_text = ""
        for i, chunk in enumerate(chunks, 1):
            chunks_text += (
                f"\n--- Excerpt {i} (Page {chunk.page_number}, "
                f"Book ID: {chunk.library_item_id}) ---\n"
                f"{chunk.text}\n"
            )

        # Generate answer
        prompt = ANSWER_PROMPT.format(query=query, chunks=chunks_text)

        system_msg = (
            "You must answer ONLY using the provided book excerpts. "
            "If the excerpts are insufficient, reply exactly: 'Insufficient context.' "
            "Always be factual and include page numbers from the sources."
        )

        # Log prompt for debugging
        try:
            logger.info("librarian_answer_prompt:\n%s", prompt[:2000])
        except Exception:
            pass

        answer = await self.llm_client.chat(
            model=self.answer_model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,  # Very low for grounded answers
        )

        # Simple guardrail: if model returns ungrounded fluff, fall back to excerpt summary
        normalized = (answer or "").lower()
        if not answer or (
            "nothing" in normalized and "context" not in normalized and len(chunks) > 0
        ):
            logger.info(
                "librarian_guardrail_fallback: regenerating from chunks summary"
            )
            parts = []
            for i, ch in enumerate(chunks[:5], 1):
                ref = ch.page_url or (
                    ch.pdf_url + f"#page={ch.page_number}"
                    if ch.pdf_url
                    else f"page {ch.page_number}"
                )
                parts.append(f"- (p.{ch.page_number}) {ch.text[:220]}… [source: {ref}]")
            answer = (
                "Based on the retrieved sources, here is a concise summary relevant to your question:\n\n"
                + "\n".join(parts)
            )

        return answer

    async def _generate_single_pass_answer(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        writing_style: str | None,
        trace: list[dict[str, Any]],
    ) -> str:
        """Generate the final answer in a single LLM call for speed."""
        logger.info("Generating single-pass answer from chunks")

        if not chunks:
            return "Insufficient context."

        style_block = (
            writing_style.strip()
            if writing_style and writing_style.strip()
            else "Use a clear, direct tone suitable for game masters."
        )

        # Format chunks compactly to minimize prompt size
        snippet_lines: list[str] = []
        for idx, chunk in enumerate(chunks, 1):
            title = chunk.book_title or "Unknown Source"
            snippet = chunk.text.strip().replace("\n", " ")
            if len(snippet) > 700:
                snippet = snippet[:700] + "…"
            snippet_lines.append(
                f"Excerpt {idx} | {title} | Page {chunk.page_number}\n{snippet}"
            )
        chunks_block = "\n\n".join(snippet_lines)

        user_prompt = FAST_SINGLE_PASS_PROMPT.format(
            query=query,
            writing_style=style_block,
            chunks=chunks_block,
        )

        system_msg = (
            "You are a veteran RPG rules librarian. Answer only from the supplied"
            " excerpts, keeping responses concise, practical, and cite-ready."
        )

        answer = await self.llm_client.chat(
            model=self.answer_model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.6,
        )

        if trace is not None:
            try:
                trace.append(
                    {
                        "step": "answer_single_pass",
                        "data": {
                            "model": self.answer_model,
                            "chunks_used": len(chunks),
                            "preview": (
                                answer[:200] + "…" if len(answer or "") > 200 else answer
                            ),
                        },
                    }
                )
            except Exception:
                pass

        return answer

    def _decorate_answer_with_sources(
        self, answer: str, chunks: list[RetrievedChunk]
    ) -> str:
        """Embed page sources inline by adding <a> links near sentence ends.

        Strategy: split answer into sentences; append an anchor for the first
        N sentences corresponding to the top-N chunks.
        """
        if not chunks or not answer:
            return answer
        # Split sentences conservatively
        import re

        sentences = re.split(r"(\S[^.!?]*[.!?])", answer)
        # Reconstruct while injecting anchors after some sentences
        out: list[str] = []
        ci = 0
        for part in sentences:
            if not part:
                continue
            if ci < len(chunks) and re.search(r"[.!?]$", part.strip()):
                ch = chunks[ci]
                label = f"page {ch.page_number}"
                href = ch.page_url or (
                    ch.pdf_url + f"#page={ch.page_number}" if ch.pdf_url else None
                )
                # Lightweight footnote marker; frontend will render actual link/footnote UI
                if href:
                    marker = (
                        f'<sup class="src" data-item="{ch.library_item_id}" '
                        f'data-page="{ch.page_number}" data-url="{href}">[{label}]</sup>'
                    )
                else:
                    marker = (
                        f'<sup class="src" data-item="{ch.library_item_id}" '
                        f'data-page="{ch.page_number}">[{label}]</sup>'
                    )
                out.append(part + " " + marker)
                ci += 1
            else:
                out.append(part)
        return "".join(out)

    @staticmethod
    def _looks_grounded(text: str | None) -> bool:
        """Heuristic: ensure answer references pages/links to consider grounded."""
        if not text:
            return False
        lower = text.lower()
        has_page = "page" in lower or "#page=" in lower or "sources:" in lower
        return has_page

        if trace is not None:
            trace.append(
                {
                    "step": "answer",
                    "data": {
                        "model": self.answer_model,
                        "chunks_used": len(chunks),
                        "answer_preview": (
                            answer[:200] + "..." if len(answer) > 200 else answer
                        ),
                    },
                }
            )

        logger.info("Answer generated")
        return answer

    async def _apply_style(
        self,
        answer: str,
        writing_style: str,
        trace: list[dict[str, Any]],
    ) -> str:
        """
        Apply writing style to answer.

        Args:
            answer: Original answer
            writing_style: Writing style description
            trace: Trace list to append to

        Returns:
            Styled answer
        """
        logger.info("Applying writing style")

        prompt = STYLE_PROMPT.format(answer=answer, writing_style=writing_style)

        system_msg = (
            "Rewrite the answer to match the style, but PRESERVE the original meaning, structure,"
            " citations, and page references exactly. Do NOT add or remove facts."
            " Do NOT change citations or remove the Sources section. Do NOT replace the answer with"
            " 'Insufficient context' if the original answer contained content."
        )

        styled_answer = await self.llm_client.chat(
            model=self.style_model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )

        if trace is not None:
            trace.append(
                {
                    "step": "style",
                    "data": {
                        "model": self.style_model,
                        "writing_style": (
                            writing_style[:100] + "..."
                            if len(writing_style) > 100
                            else writing_style
                        ),
                        "styled_preview": (
                            styled_answer[:200] + "..."
                            if len(styled_answer) > 200
                            else styled_answer
                        ),
                    },
                }
            )

        logger.info("Style applied")
        return styled_answer
