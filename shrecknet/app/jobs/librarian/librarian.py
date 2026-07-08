"""Librarian orchestrator for PDF-based question answering."""

import logging
import re
from typing import Any
import httpx
import time
import asyncio
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config_store import LLMModelTarget
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.librarian.prompts import SIMPLIFIED_ANSWER_STYLE_PROMPT
from app.jobs.librarian.schemas import (
    LibrarianQueryRequest,
    LibrarianQueryResponse,
    RetrievedChunk,
)
from app.models.agent import Agent
from app.services.pdf_embedding_service import PdfEmbeddingService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LibrarianRetrievalPlan:
    """Internal query plan for rulebook retrieval."""

    query: str
    subqueries: list[str]
    exhaustive: bool = False
    table_like: bool = False
    named_terms: list[str] = field(default_factory=list)
    page_anchors: list[dict[str, Any]] = field(default_factory=list)
    neighbor_radius: int = 0


class LibrarianOrchestrator:
    """
    Orchestrates the Librarian pipeline:
    Retrieve → Answer → Style (if configured)
    """

    def __init__(
        self,
        llm_client: ShreckLLMClient,
        pdf_embedding_service: PdfEmbeddingService,
        default_top_k: int = 10,
        answer_model: LLMModelTarget | str = "gpt-4o",
        style_model: LLMModelTarget | str = "gpt-5-nano",
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
        self.max_answer_chunks = 6
        self.max_planned_answer_chunks = 14
        self.max_chars_per_chunk_for_answer = 1200
        self._prewarm_cache_ttl_s = 300.0
        self._last_model_prewarm_at: dict[str, float] = {}

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
            allowed_item_ids = await self._list_vectorized_item_ids(
                db_session=db_session,
                ontology_id=ontology_id,
                requested_item_ids=request.library_item_ids,
            )
            plan = self._plan_retrieval(
                query=request.query,
                rpg_system=self._format_rpg_system_context(agent),
            )
            if trace is not None:
                trace.append(
                    {
                        "step": "plan_retrieval",
                        "data": {
                            "subqueries": plan.subqueries,
                            "exhaustive": plan.exhaustive,
                            "table_like": plan.table_like,
                            "named_terms": plan.named_terms,
                            "page_anchors": plan.page_anchors,
                            "neighbor_radius": plan.neighbor_radius,
                        },
                    }
                )
            chunks = await self._retrieve_planned_chunks(
                plan=plan,
                ontology_id=ontology_id,
                library_item_ids=request.library_item_ids,
                active_library_item_ids=allowed_item_ids,
                top_k=top_k,
                trace=trace,
                score_threshold=request.score_threshold if request.score_threshold is not None else 0.3,
                candidate_limit=request.candidate_limit,
                hybrid_rerank=request.hybrid_rerank,
                max_chunks_per_item=request.max_chunks_per_item,
                dynamic_score_floor=request.dynamic_score_floor,
            )
            all_chunks.extend(chunks)

        # Sort by score and take top_k across all ontologies
        # Note: For typical use (1-3 ontologies, ~30 total chunks), sorting is efficient.
        # If scaling to many ontologies, consider using heapq.nlargest for better performance.
        all_chunks = self._dedupe_chunk_dicts(all_chunks)
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
            if not metadata_for_item.get("vectorized", False):
                continue
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
                answer_chunk_limit = self.max_planned_answer_chunks if (
                    locals().get("plan") and (plan.exhaustive or plan.table_like or len(plan.subqueries) > 1)
                ) else self.max_answer_chunks
                if trace is not None:
                    trace.append(
                        {
                            "step": "synthesis_context",
                            "data": {
                                "retrieved_chunks": len(retrieved_chunks),
                                "answer_chunk_limit": answer_chunk_limit,
                                "chunks_sent": min(len(retrieved_chunks), answer_chunk_limit),
                            },
                        }
                    )
                raw_answer = await self._generate_answer_with_style(
                    query=request.query,
                    chunks=retrieved_chunks[:answer_chunk_limit],
                    writing_style=agent.writing_style,
                    rpg_system=self._format_rpg_system_context(agent),
                    trace=trace,
                )

                # Extract sources that were actually used in the answer
                sources_used = self._extract_sources_from_answer(raw_answer, retrieved_chunks)
                answer = self._render_inline_book_citations(raw_answer, retrieved_chunks)

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
        usage_summary_getter = getattr(self.llm_client, "get_usage_summary", None)
        if callable(usage_summary_getter):
            usage_summary = usage_summary_getter()
            logger.info(
                "librarian_llm_usage query=%s totals=%s by_model=%s",
                request.query[:120],
                usage_summary.get("totals"),
                usage_summary.get("by_model"),
            )

        # Return response based on mode
        return LibrarianQueryResponse(
            agent_id=agent.id,
            mode=request.mode,
            query=request.query,
            subqueries=locals().get("plan").subqueries if locals().get("plan") else [],
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
        active_library_item_ids: list[int] | None,
        top_k: int,
        trace: list[dict[str, Any]],
        score_threshold: float | None = None,
        candidate_limit: int | None = None,
        hybrid_rerank: bool = True,
        max_chunks_per_item: int | None = None,
        dynamic_score_floor: bool = False,
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
            active_library_item_ids=active_library_item_ids,
            top_k=top_k,
            score_threshold=score_threshold if score_threshold is not None else 0.3,
            candidate_limit=candidate_limit,
            hybrid_rerank=hybrid_rerank,
            max_chunks_per_item=max_chunks_per_item,
            dynamic_score_floor=dynamic_score_floor,
        )

        # Enrich chunks with neighbor pages to improve context quality
        if not self.fast_mode:
            chunks = await self.pdf_embedding_service.enrich_chunks_with_neighbors(
                chunks
            )

        if trace is not None:
            trace.append(
                {
                    "step": "hybrid_retrieve",
                    "data": {
                        "query": query,
                        "ontology_id": ontology_id,
                        "library_item_ids": library_item_ids,
                        "active_library_item_ids": active_library_item_ids,
                        "chunks_found": len(chunks),
                        "top_k": top_k,
                        "candidate_count": max(50, top_k * 8) if candidate_limit is None else candidate_limit,
                        "post_filter_count": len(chunks),
                        "post_rerank_count": len(chunks),
                        "best_vector_score": max((float(ch.get("vector_score", 0.0)) for ch in chunks), default=0.0),
                        "best_lexical_score": max((float(ch.get("lexical_score", 0.0)) for ch in chunks), default=0.0),
                        "final_score_range": {
                            "min": min((float(ch.get("score", 0.0)) for ch in chunks), default=0.0),
                            "max": max((float(ch.get("score", 0.0)) for ch in chunks), default=0.0),
                        },
                        "items_covered": len({int(ch.get("library_item_id", 0)) for ch in chunks}),
                    },
                }
            )

        return chunks

    async def _list_vectorized_item_ids(
        self,
        db_session: AsyncSession,
        ontology_id: int,
        requested_item_ids: list[int] | None,
    ) -> list[int]:
        from sqlalchemy import select

        from app.models.library import LibraryItem

        query = select(LibraryItem.id).where(
            LibraryItem.ontology_id == ontology_id,
            LibraryItem.vectorized.is_(True),
        )
        if requested_item_ids:
            query = query.where(LibraryItem.id.in_(requested_item_ids))

        rows = (await db_session.execute(query)).all()
        return [int(row[0]) for row in rows]

    async def _fetch_library_metadata(
        self,
        db_session: AsyncSession,
        library_item_ids: list[int],
    ) -> dict[int, dict[str, str | bool | None]]:
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
                "vectorized": bool(getattr(item, "vectorized", False)),
            }

        # Add None entries for any missing items
        for item_id in library_item_ids:
            if item_id not in metadata:
                logger.warning(f"Library item {item_id} not found in database")
                metadata[item_id] = {
                    "title": None,
                    "authors": None,
                    "vectorized": False,
                }

        return metadata

    async def _generate_answer_with_style(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        writing_style: str | None,
        rpg_system: str,
        trace: list[dict[str, Any]],
    ) -> str:
        """
        Generate answer with proper citations and applied writing style.

        Args:
            query: User query
            chunks: Retrieved chunks
            writing_style: Writing style description
            rpg_system: RPG system context from the agent's linked ontologies
            trace: Trace list to append to

        Returns:
            Generated answer with Markdown cite wrappers
        """
        # Format chunks for prompt with book title and page
        chunks_text = ""
        for i, chunk in enumerate(chunks, 1):
            book_title = chunk.book_title or f"Book #{chunk.library_item_id}"
            chunk_text = (chunk.text or "").strip()
            if len(chunk_text) > self.max_chars_per_chunk_for_answer:
                chunk_text = chunk_text[: self.max_chars_per_chunk_for_answer].rstrip() + " ..."
            chunks_text += (
                f"\n--- Source {i}: {book_title}, Page {chunk.page_number} "
                f"(library_item_id={chunk.library_item_id}) ---\n"
                f"{chunk_text}\n"
            )

        # Use simplified prompt without subqueries section
        style_text = writing_style or "Use a clear, direct tone suitable for game masters."
        prompt = SIMPLIFIED_ANSWER_STYLE_PROMPT.format(
            query=query,
            rpg_system=rpg_system,
            chunks=chunks_text,
            writing_style=style_text,
        )

        system_msg = (
            f"You are a knowledgeable librarian expert on the {rpg_system} RPG system. "
            "Answer using ONLY the provided excerpts. "
            "For EVERY piece of information, wrap the cited text like "
            '[text]{cite library_item_id=ID library_item_name="TITLE" page=PAGE}. '
            "Apply the writing style while preserving all facts."
        )
        await self._maybe_prewarm_model(self.answer_model)

        try:
            answer = await self.llm_client.chat(
                model=self.answer_model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                usage_tag="librarian_answer",
            )
        except Exception as exc:
            if isinstance(exc, httpx.TimeoutException) or "504" in str(exc):
                logger.warning("librarian_answer_timeout_fallback: %s", exc)
                return (
                    "I found relevant excerpts, but answer generation timed out. "
                    "Please try again with a narrower question or lower top_k."
                )
            raise

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

    def _plan_retrieval(self, query: str, rpg_system: str) -> LibrarianRetrievalPlan:
        normalized = query.strip()
        lower = normalized.lower()
        exhaustive = bool(re.search(r"\b(all|complete|full|entire|every|list|table|catalog|catalogue)\b", lower))
        table_like = bool(re.search(r"\b(table|list|chart|occupation|occupations|skills?|weapons?|spells?|items?|equipment|character creation|creation)\b", lower))
        neighbor_radius = 2 if exhaustive or table_like or "character creation" in lower else 0
        named_terms = self._extract_named_terms(normalized)
        page_anchors = self._extract_page_anchors(normalized)

        seeds = [normalized]
        topic_terms = self._topic_terms(normalized)
        for term in topic_terms:
            variants = [term]
            if term.endswith("s") and len(term) > 4:
                variants.append(term[:-1])
            variants = self._unique_preserve_order(variants)
            if table_like:
                for variant in variants:
                    seeds.append(f"{variant} table {rpg_system}")
                    seeds.append(f"{variant} list {rpg_system}")
            else:
                for variant in variants:
                    seeds.append(f"{variant} {rpg_system}")
        for term in named_terms:
            seeds.append(f"{term} {rpg_system}")
            if topic_terms:
                seeds.append(f"{term} {topic_terms[0]} {rpg_system}")
        if page_anchors:
            seeds.append(f"page {' '.join(str(anchor['page']) for anchor in page_anchors)} {rpg_system}")

        subqueries = self._unique_preserve_order(seeds)[:8]
        return LibrarianRetrievalPlan(
            query=normalized,
            subqueries=subqueries,
            exhaustive=exhaustive,
            table_like=table_like,
            named_terms=named_terms,
            page_anchors=page_anchors,
            neighbor_radius=neighbor_radius,
        )

    def _extract_named_terms(self, query: str) -> list[str]:
        stop = {
            "Give", "Explain", "What", "Which", "When", "Where", "How", "Can",
            "Could", "Please", "The", "A", "An", "For", "Core", "Rulebook",
            "Investigator", "Handbook",
        }
        terms: list[str] = []
        for match in re.finditer(r"\b[A-Z][A-Za-z][A-Za-z'\-]*(?:\s+[A-Z][A-Za-z][A-Za-z'\-]*){0,3}\b", query):
            value = match.group(0).strip()
            if value.split()[0] in stop:
                continue
            if re.search(r"\bp\.?\s*\d+\b", value, re.I):
                continue
            terms.append(value)
        return self._unique_preserve_order(terms)[:6]

    def _extract_page_anchors(self, query: str) -> list[dict[str, Any]]:
        anchors: list[dict[str, Any]] = []
        pattern = re.compile(
            r"(?:(?P<title>[A-Z][A-Za-z0-9 '&:\-]{1,60}?)\s*,?\s*)?\b(?:p|page)\.?\s*(?P<page>\d{1,4})\b",
            re.I,
        )
        for match in pattern.finditer(query):
            title = (match.group("title") or "").strip(" ,")
            title = re.sub(r"^(what about|what is|tell me about|explain|see|on)\s+", "", title, flags=re.I).strip(" ,")
            anchors.append({"title": title or None, "page": int(match.group("page"))})
        return anchors

    def _topic_terms(self, query: str) -> list[str]:
        tokens = [
            token
            for token in re.findall(r"[A-Za-z][A-Za-z'\-]+", query.lower())
            if token not in {
                "give", "list", "the", "all", "full", "complete", "rulebook", "rules",
                "explain", "what", "about", "please", "can", "you", "for", "example",
                "from", "with", "that", "this", "them", "see", "show", "me",
            }
        ]
        prioritized = [tok for tok in tokens if len(tok) > 3]
        return self._unique_preserve_order(prioritized)[:4]

    def _unique_preserve_order(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        unique: list[str] = []
        for value in values:
            cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
            if not cleaned:
                continue
            key = cleaned.casefold()
            if key in seen:
                continue
            seen.add(key)
            unique.append(cleaned)
        return unique

    async def _retrieve_planned_chunks(
        self,
        plan: LibrarianRetrievalPlan,
        ontology_id: int,
        library_item_ids: list[int] | None,
        active_library_item_ids: list[int] | None,
        top_k: int,
        trace: list[dict[str, Any]],
        score_threshold: float | None = None,
        candidate_limit: int | None = None,
        hybrid_rerank: bool = True,
        max_chunks_per_item: int | None = None,
        dynamic_score_floor: bool = False,
    ) -> list[dict[str, Any]]:
        per_query_k = max(top_k, 12 if plan.exhaustive or plan.table_like else top_k)
        # Neo4j async sessions are not safe for concurrent run() calls from gather().
        # Retrieve subqueries sequentially because PdfEmbeddingService is request-scoped
        # and holds a single session instance.
        results: list[list[dict[str, Any]]] = []
        for subquery in plan.subqueries:
            result = await self._retrieve_chunks(
                query=subquery,
                ontology_id=ontology_id,
                library_item_ids=library_item_ids,
                active_library_item_ids=active_library_item_ids,
                top_k=per_query_k,
                trace=trace,
                score_threshold=score_threshold,
                candidate_limit=candidate_limit,
                hybrid_rerank=hybrid_rerank,
                max_chunks_per_item=max_chunks_per_item,
                dynamic_score_floor=dynamic_score_floor,
            )
            results.append(result)
        chunks = self._dedupe_chunk_dicts([chunk for group in results for chunk in group])

        if plan.page_anchors:
            page_chunks = await self.pdf_embedding_service.fetch_chunks_by_page_anchors(
                ontology_id=ontology_id,
                page_anchors=plan.page_anchors,
                library_item_ids=library_item_ids,
                active_library_item_ids=active_library_item_ids,
                radius=plan.neighbor_radius,
            )
            if trace is not None:
                trace.append(
                    {
                        "step": "page_anchor_retrieve",
                        "data": {"anchors": plan.page_anchors, "chunks_found": len(page_chunks)},
                    }
                )
            chunks = self._dedupe_chunk_dicts(chunks + page_chunks)

        if plan.neighbor_radius and chunks:
            before = len(chunks)
            chunks = await self.pdf_embedding_service.expand_chunks_by_page_neighbors(
                chunks,
                radius=plan.neighbor_radius,
                ontology_id=ontology_id,
                library_item_ids=library_item_ids,
                active_library_item_ids=active_library_item_ids,
            )
            if trace is not None:
                trace.append(
                    {
                        "step": "neighbor_expansion",
                        "data": {
                            "radius": plan.neighbor_radius,
                            "before": before,
                            "after": len(chunks),
                        },
                    }
                )

        chunks = self._dedupe_chunk_dicts(chunks)
        chunks.sort(key=lambda ch: float(ch.get("score", 0.0)), reverse=True)
        return chunks[: max(top_k, self.max_planned_answer_chunks if plan.exhaustive or plan.table_like else top_k)]

    def _dedupe_chunk_dicts(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[tuple[int, int], dict[str, Any]] = {}
        for chunk in chunks:
            key = (int(chunk.get("library_item_id", 0)), int(chunk.get("chunk_index", chunk.get("page_number", 0))))
            existing = merged.get(key)
            if existing is None or float(chunk.get("score", 0.0)) > float(existing.get("score", 0.0)):
                merged[key] = dict(chunk)
        return list(merged.values())

    def _format_rpg_system_context(self, agent: Agent) -> str:
        systems = []
        seen = set()
        for ontology in agent.ontologies:
            system = (getattr(ontology, "rpg_system", None) or "").strip()
            if not system:
                continue
            normalized = system.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            systems.append(system)

        if not systems:
            return "relevant"
        if len(systems) == 1:
            return systems[0]
        return ", ".join(systems[:-1]) + f", and {systems[-1]}"

    async def _maybe_prewarm_model(self, model: LLMModelTarget | str) -> None:
        target = model
        if isinstance(model, LLMModelTarget):
            cache_key = f"{model.provider}:{model.name}"
            provider = model.provider
            model_name = model.name
        else:
            cache_key = str(model)
            provider = "openai"
            model_name = str(model)
        if provider != "ollama":
            return
        now = time.monotonic()
        last_at = self._last_model_prewarm_at.get(cache_key, 0.0)
        if now - last_at < self._prewarm_cache_ttl_s:
            return
        try:
            await asyncio.wait_for(
                self.llm_client.chat(
                    model=target,
                    messages=[{"role": "user", "content": "ping"}],
                    temperature=0.0,
                    usage_tag="librarian_model_prewarm",
                ),
                timeout=8.0,
            )
            self._last_model_prewarm_at[cache_key] = time.monotonic()
            logger.info("librarian_model_prewarm_done model=%s", cache_key)
        except Exception as exc:
            logger.warning("librarian_model_prewarm_failed model=%s error=%s", cache_key, exc)

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

    def _render_inline_book_citations(
        self,
        answer: str,
        all_chunks: list[RetrievedChunk],
    ) -> str:
        """Replace cite wrappers with natural inline book/page references and links."""
        if not answer:
            return answer

        chunk_index: dict[tuple[int, int], RetrievedChunk] = {}
        for chunk in all_chunks:
            chunk_index[(int(chunk.library_item_id), int(chunk.page_number))] = chunk

        pattern = re.compile(r"\[(?P<text>.*?)\]\{cite(?P<attrs>[^}]*)\}", re.DOTALL)

        def _extract_int(attrs: str, key: str) -> int | None:
            match = re.search(rf"\b{re.escape(key)}\s*=\s*(\d+)", attrs)
            if not match:
                return None
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                return None

        def _extract_str(attrs: str, key: str) -> str | None:
            match = re.search(rf'{re.escape(key)}\s*=\s*"([^"]+)"', attrs)
            if match:
                return match.group(1).strip()
            match = re.search(rf"\b{re.escape(key)}\s*=\s*([^\s]+)", attrs)
            if match:
                return match.group(1).strip().strip('"')
            return None

        def _replace(match: re.Match[str]) -> str:
            text = (match.group("text") or "").strip()
            attrs = match.group("attrs") or ""

            item_id = _extract_int(attrs, "library_item_id")
            page = _extract_int(attrs, "page")
            title = _extract_str(attrs, "library_item_name")

            chunk = chunk_index.get((item_id, page)) if item_id is not None and page is not None else None
            resolved_title = title or (chunk.book_title if chunk else None) or (
                f"Book #{item_id}" if item_id is not None else "Book"
            )
            page_label = page if page is not None else (chunk.page_number if chunk else None)
            page_url = chunk.page_url if chunk else None

            if page_url and page_label is not None:
                return f"{text} (according to [{resolved_title}, p.{page_label}]({page_url}))"
            if page_label is not None:
                return f"{text} (according to {resolved_title}, p.{page_label})"
            return f"{text} (according to {resolved_title})"

        rendered = pattern.sub(_replace, answer)
        rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip()
        return rendered

