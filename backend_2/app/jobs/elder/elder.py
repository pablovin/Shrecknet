"""Elder orchestrator using LangGraph for pipeline execution."""

import asyncio
import logging
from typing import Any, Optional

from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.integrations.retrieval.neo4j_retriever import GraphRetriever
from app.jobs.elder.prompts import (
    DECOMPOSE_PROMPT,
    SUBANSWER_PROMPT,
    SYNTHESIS_PROMPT,
    VALIDATION_PROMPT,
    STYLE_PROMPT,
)
from app.jobs.elder.schemas import (
    ElderQueryRequest,
    ElderQueryResponse,
    RetrievedChunk,
    SubAnswer,
    TraceStep,
)
from app.models.agent import Agent

logger = logging.getLogger(__name__)


class ElderOrchestrator:
    """
    Orchestrates the Elder pipeline:
    Decompose → Retrieve → Sub-answer → Synthesize → Validate → Style
    """

    def __init__(
        self,
        llm_client: OpenAIClient,
        model_policy: ModelPolicy,
        graph_retriever: GraphRetriever,
        default_top_k: int = 8,
    ):
        """
        Initialize orchestrator.

        Args:
            llm_client: OpenAI client for LLM calls
            model_policy: Policy for task-to-model mapping
            graph_retriever: Graph retrieval interface
            default_top_k: Default number of retrieval results per sub-query
        """
        self.llm_client = llm_client
        self.model_policy = model_policy
        self.graph_retriever = graph_retriever
        self.default_top_k = default_top_k
        self.max_subqueries = 5
        self.max_concurrency = 5

    async def execute(
        self,
        agent: Agent,
        request: ElderQueryRequest,
    ) -> ElderQueryResponse:
        """
        Execute the Elder pipeline for a query.

        Args:
            agent: Agent instance with configuration
            request: Query request

        Returns:
            Query response with answer and/or context
        """
        trace: list[TraceStep] = [] if request.include_trace else []
        top_k = request.top_k or self.default_top_k

        # Get ontology IDs from agent
        ontology_ids = [ont.id for ont in agent.ontologies]

        # Step 1: Decompose query into sub-queries
        subqueries = await self._decompose(request.query, ontology_ids, trace)

        # Step 2: Retrieve context for each sub-query (parallel)
        retrieval_results = await self._retrieve(subqueries, ontology_ids, top_k, trace)

        # Step 3: Generate sub-answers (parallel)
        subanswers = await self._subanswer(retrieval_results, trace)

        # Step 4: Synthesize final answer if mode includes 'nl'
        answer = None
        if request.mode in ("nl", "both"):
            answer = await self._synthesize(request.query, subanswers, trace)

            # Step 5: Validate answer
            is_valid = await self._validate(request.query, answer, trace)

            # If validation fails, refine once
            if not is_valid:
                answer = await self._synthesize(
                    request.query, subanswers, trace, refine=True
                )

            # Step 6: Apply writing style if configured
            if agent.writing_style:
                answer = await self._style(answer, agent.writing_style, trace)

        # Step 7: Build context and important nodes
        important_nodes, context = self._build_context(subanswers)

        # Build response based on mode
        response = ElderQueryResponse(
            agent_id=agent.id,
            mode=request.mode,
            query=request.query,
            subanswers=subanswers,
            important_nodes=(
                important_nodes if request.mode in ("context", "both") else []
            ),
            context=context if request.mode in ("context", "both") else [],
            trace=trace if request.include_trace else None,
        )

        if request.mode in ("nl", "both"):
            response.answer = answer

        return response

    async def _decompose(
        self, query: str, ontology_ids: list[int], trace: list[TraceStep]
    ) -> list[str]:
        """Decompose query into 1-5 sub-queries."""
        model = self.model_policy.get_model(LLMTask.DECOMPOSE)
        prompt = DECOMPOSE_PROMPT.format(
            query=query,
            ontology_ids=", ".join(map(str, ontology_ids)) if ontology_ids else "any",
        )

        try:
            response = await self.llm_client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )

            # Parse sub-queries from response (one per line)
            lines = [
                line.strip() for line in response.strip().split("\n") if line.strip()
            ]
            subqueries = [line.lstrip("0123456789.-) ") for line in lines if line][
                : self.max_subqueries
            ]

            # Fallback to original query if empty
            if not subqueries:
                subqueries = [query]

            if trace is not None:
                trace.append(
                    TraceStep(
                        step="decompose",
                        data={"subqueries": subqueries, "model": model},
                    )
                )

            logger.info(f"Decomposed into {len(subqueries)} sub-queries")
            return subqueries

        except Exception as e:
            logger.error(f"Decomposition failed: {e}")
            # Fallback to original query
            return [query]

    async def _retrieve(
        self,
        subqueries: list[str],
        ontology_ids: list[int],
        top_k: int,
        trace: list[TraceStep],
    ) -> list[tuple[str, list[RetrievedChunk]]]:
        """Retrieve context for each sub-query in parallel."""

        async def retrieve_one(subquery: str) -> tuple[str, list[RetrievedChunk]]:
            try:
                chunks = await self.graph_retriever.search(
                    query=subquery,
                    ontology_ids=ontology_ids,
                    top_k=top_k,
                )
                return (subquery, chunks)
            except Exception as e:
                logger.error(f"Retrieval failed for '{subquery}': {e}")
                return (subquery, [])

        # Use semaphore to limit concurrency
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def retrieve_with_limit(sq: str) -> tuple[str, list[RetrievedChunk]]:
            async with semaphore:
                return await retrieve_one(sq)

        results = await asyncio.gather(*[retrieve_with_limit(sq) for sq in subqueries])

        if trace is not None:
            trace.append(
                TraceStep(
                    step="retrieve",
                    data={
                        "retrieval": [
                            {"subquery": sq, "num_chunks": len(chunks)}
                            for sq, chunks in results
                        ]
                    },
                )
            )

        logger.info(f"Retrieved context for {len(results)} sub-queries")
        return results

    async def _subanswer(
        self,
        retrieval_results: list[tuple[str, list[RetrievedChunk]]],
        trace: list[TraceStep],
    ) -> list[SubAnswer]:
        """Generate sub-answers for each sub-query in parallel."""
        model = self.model_policy.get_model(LLMTask.SUBANSWER)

        async def answer_one(subquery: str, chunks: list[RetrievedChunk]) -> SubAnswer:
            # Build context from chunks
            if not chunks:
                context_text = "(No context retrieved)"
            else:
                context_parts = [
                    f"[Score: {chunk.score:.2f}] {chunk.text}" for chunk in chunks
                ]
                context_text = "\n\n".join(context_parts)

            prompt = SUBANSWER_PROMPT.format(
                subquery=subquery,
                context=context_text,
            )

            try:
                answer_text = await self.llm_client.chat(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                )

                return SubAnswer(
                    subquery=subquery,
                    answer=answer_text,
                    retrieval=chunks,
                )
            except Exception as e:
                logger.error(f"Sub-answer generation failed for '{subquery}': {e}")
                return SubAnswer(
                    subquery=subquery,
                    answer="Error generating answer.",
                    retrieval=chunks,
                )

        # Use semaphore to limit concurrency
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def answer_with_limit(sq: str, chunks: list[RetrievedChunk]) -> SubAnswer:
            async with semaphore:
                return await answer_one(sq, chunks)

        subanswers = await asyncio.gather(
            *[answer_with_limit(sq, chunks) for sq, chunks in retrieval_results]
        )

        if trace is not None:
            trace.append(
                TraceStep(
                    step="subanswer",
                    data={
                        "subanswers": [
                            {"subquery": sa.subquery, "answer_preview": sa.answer[:100]}
                            for sa in subanswers
                        ],
                        "model": model,
                    },
                )
            )

        logger.info(f"Generated {len(subanswers)} sub-answers")
        return subanswers

    async def _synthesize(
        self,
        query: str,
        subanswers: list[SubAnswer],
        trace: list[TraceStep],
        refine: bool = False,
    ) -> str:
        """Synthesize final answer from sub-answers."""
        model = self.model_policy.get_model(LLMTask.SYNTHESIS)

        # Format sub-answers
        subanswers_text = "\n\n".join(
            [f"Q: {sa.subquery}\nA: {sa.answer}" for sa in subanswers]
        )

        prompt = SYNTHESIS_PROMPT.format(
            query=query,
            subanswers=subanswers_text,
        )

        if refine:
            prompt += "\n\nNote: This is a refinement pass. Please provide a more comprehensive answer."

        try:
            answer = await self.llm_client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
            )

            if trace is not None:
                trace.append(
                    TraceStep(
                        step="synthesize" + ("_refine" if refine else ""),
                        data={"answer_preview": answer[:200], "model": model},
                    )
                )

            logger.info("Synthesized final answer")
            return answer

        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            return "Error generating final answer."

    async def _validate(self, query: str, answer: str, trace: list[TraceStep]) -> bool:
        """Validate that answer addresses the query."""
        model = self.model_policy.get_model(LLMTask.VALIDATION)

        prompt = VALIDATION_PROMPT.format(
            query=query,
            answer=answer,
        )

        try:
            validation = await self.llm_client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )

            is_ok = validation.strip().upper().startswith("OK")

            if trace is not None:
                trace.append(
                    TraceStep(
                        step="validate",
                        data={
                            "validation": validation,
                            "is_ok": is_ok,
                            "model": model,
                        },
                    )
                )

            logger.info(f"Validation: {'OK' if is_ok else 'needs refinement'}")
            return is_ok

        except Exception as e:
            logger.error(f"Validation failed: {e}")
            # Assume OK on error to avoid infinite loops
            return True

    async def _style(
        self, answer: str, writing_style: str, trace: list[TraceStep]
    ) -> str:
        """Apply writing style to answer."""
        model = self.model_policy.get_model(LLMTask.STYLE)

        prompt = STYLE_PROMPT.format(
            writing_style=writing_style,
            answer=answer,
        )

        try:
            styled = await self.llm_client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
            )

            if trace is not None:
                trace.append(
                    TraceStep(
                        step="style",
                        data={"styled_preview": styled[:200], "model": model},
                    )
                )

            logger.info("Applied writing style")
            return styled

        except Exception as e:
            logger.error(f"Style application failed: {e}")
            # Return original answer on error
            return answer

    def _build_context(
        self, subanswers: list[SubAnswer]
    ) -> tuple[list[str], list[RetrievedChunk]]:
        """Build important nodes and deduplicated context."""
        # Collect all chunks
        all_chunks: list[RetrievedChunk] = []
        for sa in subanswers:
            all_chunks.extend(sa.retrieval)

        # Sort by score
        all_chunks.sort(key=lambda c: c.score, reverse=True)

        # Deduplicate by node_id
        seen_nodes = set()
        unique_chunks = []
        for chunk in all_chunks:
            if chunk.node_id not in seen_nodes:
                seen_nodes.add(chunk.node_id)
                unique_chunks.append(chunk)

        # Take top 12 for important nodes
        important_nodes = [chunk.node_id for chunk in unique_chunks[:12]]

        logger.info(
            f"Built context: {len(important_nodes)} important nodes, {len(unique_chunks)} unique chunks"
        )
        return important_nodes, unique_chunks
