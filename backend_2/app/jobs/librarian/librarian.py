"""Librarian orchestrator for PDF-based question answering."""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.llm.openai_client import OpenAIClient
from app.jobs.librarian.prompts import ANSWER_PROMPT, STYLE_PROMPT, SYNTHESIS_PROMPT
from app.jobs.librarian.schemas import (
    LibrarianQueryRequest,
    LibrarianQueryResponse,
    RetrievedChunk,
)
from app.models.agent import Agent
from app.repositories.library_repository import LibraryRepository
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

    async def execute(
        self,
        agent: Agent,
        request: LibrarianQueryRequest,
        db_session: AsyncSession,
    ) -> LibrarianQueryResponse:
        """
        Execute the Librarian pipeline for a query.

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

        # For now, we'll search across all ontologies the agent has access to
        # In the future, we could be smarter about which ontology to search

        # Step 1: Retrieve relevant chunks from PDFs
        all_chunks = []
        for ontology_id in ontology_ids:
            chunks = await self._retrieve_chunks(
                request.query,
                ontology_id,
                request.library_item_ids,
                top_k,
                trace,
                score_threshold=request.score_threshold,
            )
            try:
                logger.info(
                    "librarian_retrieval: ontology=%s chunks=%d",
                    str(ontology_id),
                    len(chunks),
                )
                for c in chunks[: min(5, len(chunks))]:
                    logger.info(
                        "chunk: item=%s page=%s score=%.3f preview=%s",
                        c.get("library_item_id"),
                        c.get("page_number"),
                        c.get("score", 0.0),
                        (
                            (c.get("text", "")[:120] + "…")
                            if len(c.get("text", "")) > 120
                            else c.get("text", "")
                        ),
                    )
            except Exception:
                pass
            all_chunks.extend(chunks)

        # Sort by score and take top_k
        all_chunks.sort(key=lambda x: x["score"], reverse=True)
        all_chunks = all_chunks[:top_k]

        # Fetch library item metadata for all unique library items
        unique_library_item_ids = list(
            {chunk["library_item_id"] for chunk in all_chunks}
        )
        library_metadata = await self._fetch_library_metadata(
            db_session, unique_library_item_ids
        )

        # Convert to schema objects
        retrieved_chunks = [
            RetrievedChunk(
                library_item_id=chunk["library_item_id"],
                page_number=chunk["page_number"],
                text=chunk["text"],
                score=chunk["score"],
                pdf_url=chunk.get("pdf_url"),
                page_url=chunk.get("page_url"),
                book_title=library_metadata.get(chunk["library_item_id"], {}).get(
                    "title"
                ),
                book_authors=library_metadata.get(chunk["library_item_id"], {}).get(
                    "authors"
                ),
            )
            for chunk in all_chunks
        ]

        # Log the sources we will use
        try:
            logger.info("librarian_sources_used: %d", len(retrieved_chunks))
            for ch in retrieved_chunks[: min(5, len(retrieved_chunks))]:
                logger.info(
                    "source: item=%s page=%s url=%s score=%.3f",
                    ch.library_item_id,
                    ch.page_number,
                    ch.page_url
                    or (ch.pdf_url + f"#page={ch.page_number}" if ch.pdf_url else None),
                    ch.score,
                )
        except Exception:
            pass

        # Get unique library items used
        library_items_used = list({chunk.library_item_id for chunk in retrieved_chunks})

        # Step 2: Generate answer if mode includes 'nl'
        answer = None
        if request.mode in ("nl", "both"):
            if not retrieved_chunks:
                answer = (
                    "I couldn't find any relevant information in the available "
                    "books to answer your question."
                )
            else:
                answer = await self._generate_answer(
                    request.query, retrieved_chunks, trace
                )
                raw_answer = answer
                try:
                    logger.info(
                        "librarian_answer_raw_preview: %s",
                        (
                            (answer[:400] + "…")
                            if (answer and len(answer) > 400)
                            else (answer or "<empty>")
                        ),
                    )
                except Exception:
                    pass
                if answer is not None:
                    # Decorate answer inline with page links for top chunks
                    try:
                        decorated = self._decorate_answer_with_sources(
                            answer, retrieved_chunks
                        )
                        answer = decorated
                        try:
                            logger.info(
                                "librarian_answer_decorated_preview: %s",
                                (
                                    (answer[:400] + "…")
                                    if (answer and len(answer) > 400)
                                    else (answer or "<empty>")
                                ),
                            )
                        except Exception:
                            pass
                    except Exception:
                        pass
                # Do not append a Sources footer; frontend will render footnotes from markers

                # Step 3: Apply writing style if configured
                if agent.writing_style and answer:
                    styled = await self._apply_style(answer, agent.writing_style, trace)
                    # Validate styled answer; if it loses citations/sources, keep original
                    styled_norm = (styled or "").lower()
                    raw_norm = (raw_answer or "").lower()
                    insufficient_in_styled = "insufficient context" in styled_norm
                    insufficient_in_raw = "insufficient context" in raw_norm
                    try:
                        logger.info(
                            "librarian_style_output_preview: %s",
                            (
                                (styled[:400] + "…")
                                if (styled and len(styled) > 400)
                                else (styled or "<empty>")
                            ),
                        )
                    except Exception:
                        pass
                    if self._looks_grounded(styled) and not (
                        insufficient_in_styled and not insufficient_in_raw
                    ):
                        answer = styled
                        logger.info(
                            "librarian_style_validation: accepted styled answer"
                        )
                    else:
                        logger.info(
                            "librarian_style_validation: reverted to unstyled answer (grounding=%s, styled_insufficient=%s, raw_insufficient=%s)",
                            str(self._looks_grounded(styled)),
                            str(insufficient_in_styled),
                            str(insufficient_in_raw),
                        )

        # Return response based on mode
        return LibrarianQueryResponse(
            agent_id=agent.id,
            mode=request.mode,
            query=request.query,
            answer=answer if request.mode in ("nl", "both") else None,
            chunks=retrieved_chunks if request.mode in ("context", "both") else [],
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
        chunks = await self.pdf_embedding_service.enrich_chunks_with_neighbors(chunks)

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

        library_repo = LibraryRepository(db_session)
        metadata = {}

        for item_id in library_item_ids:
            try:
                library_item = await library_repo.get_item_by_id(item_id)
                if library_item:
                    metadata[item_id] = {
                        "title": library_item.title,
                        "authors": library_item.authors,
                    }
            except Exception as e:
                logger.warning(
                    f"Failed to fetch metadata for library item {item_id}: {e}"
                )
                metadata[item_id] = {"title": None, "authors": None}

        return metadata

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
