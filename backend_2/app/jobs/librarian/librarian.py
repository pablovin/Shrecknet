"""Librarian orchestrator for PDF-based question answering."""

import logging
from typing import Any

from app.integrations.llm.openai_client import OpenAIClient
from app.jobs.librarian.prompts import ANSWER_PROMPT, STYLE_PROMPT, SYNTHESIS_PROMPT
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
    ) -> LibrarianQueryResponse:
        """
        Execute the Librarian pipeline for a query.

        Args:
            agent: Agent instance with configuration
            request: Query request

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
            )
            all_chunks.extend(chunks)

        # Sort by score and take top_k
        all_chunks.sort(key=lambda x: x["score"], reverse=True)
        all_chunks = all_chunks[:top_k]

        # Convert to schema objects
        retrieved_chunks = [
            RetrievedChunk(
                library_item_id=chunk["library_item_id"],
                page_number=chunk["page_number"],
                text=chunk["text"],
                score=chunk["score"],
            )
            for chunk in all_chunks
        ]

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

                # Step 3: Apply writing style if configured
                if agent.writing_style:
                    answer = await self._apply_style(answer, agent.writing_style, trace)

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
            score_threshold=0.3,  # Lower threshold for book content
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

        response = await self.llm_client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=self.answer_model,
            temperature=0.3,  # Lower temperature for factual accuracy
        )

        answer = response["choices"][0]["message"]["content"]

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

        response = await self.llm_client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model=self.style_model,
            temperature=0.5,
        )

        styled_answer = response["choices"][0]["message"]["content"]

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
