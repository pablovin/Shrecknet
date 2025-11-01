"""Elder orchestrator using LangGraph for pipeline execution."""

import asyncio
import logging
import time
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
        self.max_subqueries = 3
        self.max_concurrency = 3

    async def execute(
        self,
        agent: Agent,
        request: ElderQueryRequest,
        chat_history: Optional[list[dict[str, str]]] = None,
    ) -> ElderQueryResponse:
        """
        Execute the Elder pipeline for a query.

        Args:
            agent: Agent instance with configuration
            request: Query request
            chat_history: Optional chat history for context

        Returns:
            Query response with answer and/or context
        """
        trace: list[TraceStep] = [] if request.include_trace else []
        timings: dict[str, float] = {}
        overall_start = time.monotonic()
        top_k = request.top_k or self.default_top_k

        # Get ontology IDs from agent
        ontology_ids = [ont.id for ont in agent.ontologies]

        # Fast path: single retrieval + single generation
        if request.fast:
            top_k = min(top_k, 5)
            model = self.model_policy.get_model(LLMTask.SYNTHESIS)
            # Retrieve once across ontologies
            t_retr_start = time.monotonic()
            chunks = await self.graph_retriever.search(
                query=request.query, ontology_ids=ontology_ids, top_k=top_k
            )
            timings["retrieve"] = time.monotonic() - t_retr_start
            if not chunks:
                response = ElderQueryResponse(
                    agent_id=agent.id,
                    mode=request.mode,
                    query=request.query,
                    subanswers=[],
                    important_nodes=[],
                    context=[],
                    trace=trace if request.include_trace else None,
                    answer=None,
                )
                timings["overall"] = time.monotonic() - overall_start
                logger.info(
                    "elder_fast_timing: no_context retrieve=%.3fs overall=%.3fs",
                    timings.get("retrieve", 0.0),
                    timings.get("overall", 0.0),
                )
                return response
            # Build a compact context
            context_snippets = []
            for c in chunks[:top_k]:
                text = c.text.strip().replace("\n", " ")
                if len(text) > 500:
                    text = text[:500] + "…"
                context_snippets.append(f"- {text}")
            compact_context = "\n".join(context_snippets)

            prompt = (
                "You are a concise assistant. Using the context, answer the question briefly (<=120 words).\n"
                f"Question: {request.query}\n"
                f"Context:\n{compact_context}"
            )
            t_llm_start = time.monotonic()
            answer = await self.llm_client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            timings["llm_synthesis"] = time.monotonic() - t_llm_start

            # Build response
            subanswers: list[SubAnswer] = []
            important_nodes = [c.node_id for c in chunks]
            response = ElderQueryResponse(
                agent_id=agent.id,
                mode=request.mode,
                query=request.query,
                subanswers=subanswers,
                important_nodes=(
                    important_nodes if request.mode in ("context", "both") else []
                ),
                context=chunks if request.mode in ("context", "both") else [],
                trace=trace if request.include_trace else None,
                answer=answer if request.mode in ("nl", "both") else None,
            )
            timings["overall"] = time.monotonic() - overall_start
            logger.info(
                "elder_fast_timing: retrieve=%.3fs, llm=%.3fs, overall=%.3fs",
                timings.get("retrieve", 0.0),
                timings.get("llm_synthesis", 0.0),
                timings.get("overall", 0.0),
            )
            return response

        # Step 1: Decompose query into sub-queries
        t_decomp = time.monotonic()
        subqueries = await self._decompose(
            request.query, ontology_ids, chat_history, trace, request.entities_hint
        )
        timings["decompose"] = time.monotonic() - t_decomp
        logger.info("elder_decompose: query='%s' subqueries=%s", request.query, subqueries)

        # Step 2: Retrieve context for each sub-query (parallel)
        t_retrieve = time.monotonic()
        retrieval_results = await self._retrieve(subqueries, ontology_ids, top_k, trace)
        timings["retrieve"] = time.monotonic() - t_retrieve
        # Build debug summary of retrieval names per subquery
        def extract_name(chunk: RetrievedChunk) -> str:
            txt = (chunk.text or "").splitlines()
            for line in txt[:3]:
                if line.lower().startswith("name:"):
                    return line.split(":", 1)[-1].strip()
            return "(unknown)"
        retrieval_summary = [
            {
                "subquery": sq,
                "names": [extract_name(c) for c in chunks[:5]],
                "context_preview": [
                    (c.text[:200] + "…") if len(c.text) > 200 else c.text
                    for c in chunks[:2]
                ],
            }
            for (sq, chunks) in retrieval_results
        ]

        # If no context retrieved, return without generating an answer
        total_chunks = sum(len(chunks) for _, chunks in retrieval_results)
        if total_chunks == 0:
            important_nodes: list[str] = []
            context: list[RetrievedChunk] = []
            # Surface retrieval errors to client via trace even if include_trace=false
            retrieval_errors = getattr(self.graph_retriever, "last_errors", [])
            resp_trace = trace if request.include_trace else []
            if retrieval_errors:
                resp_trace = resp_trace or []
                resp_trace.append(
                    TraceStep(step="retrieval_error", data={"errors": retrieval_errors})
                )
            response = ElderQueryResponse(
                agent_id=agent.id,
                mode=request.mode,
                query=request.query,
                subanswers=[],
                important_nodes=[],
                context=[],
                trace=resp_trace or None,
            )
            timings["overall"] = time.monotonic() - overall_start
            logger.info(
                "elder_timing: no_context decompose=%.3fs retrieve=%.3fs overall=%.3fs",
                timings.get("decompose", 0.0),
                timings.get("retrieve", 0.0),
                timings.get("overall", 0.0),
            )
            return response

        # Step 3: Generate sub-answers (parallel)
        t_subans = time.monotonic()
        subanswers = await self._subanswer(retrieval_results, trace)
        timings["subanswer"] = time.monotonic() - t_subans

        # Step 4: Synthesize final answer if mode includes 'nl'
        answer = None
        if request.mode in ("nl", "both"):
            t_synth = time.monotonic()
            answer = await self._synthesize(request.query, subanswers, trace)
            timings["synthesis"] = time.monotonic() - t_synth

            # Step 5: Validate answer
            t_valid = time.monotonic()
            is_valid = await self._validate(request.query, answer, trace)
            timings["validation"] = time.monotonic() - t_valid

            # If validation fails, refine once
            if not is_valid:
                t_refine = time.monotonic()
                answer = await self._synthesize(
                    request.query, subanswers, trace, refine=True
                )
                timings["refine"] = time.monotonic() - t_refine

            # Step 6: Apply writing style if configured
            if agent.writing_style:
                t_style = time.monotonic()
                answer = await self._style(answer, agent.writing_style, trace)
                timings["style"] = time.monotonic() - t_style

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

        timings["overall"] = time.monotonic() - overall_start
        logger.info(
            "elder_timing: decompose=%.3fs, retrieve=%.3fs, subanswer=%.3fs, synthesis=%.3fs, validation=%.3fs, refine=%.3fs, style=%.3fs, overall=%.3fs",
            timings.get("decompose", 0.0),
            timings.get("retrieve", 0.0),
            timings.get("subanswer", 0.0),
            timings.get("synthesis", 0.0),
            timings.get("validation", 0.0),
            timings.get("refine", 0.0),
            timings.get("style", 0.0),
            timings.get("overall", 0.0),
        )
        # Final verbose debug block
        try:
            logger.info("elder_summary_original_query: %s", request.query)
            logger.info("elder_summary_decompose_prompt:\n%s", prompt)
            logger.info("elder_summary_subqueries: %s", subqueries)
            for entry in retrieval_summary:
                logger.info(
                    "elder_summary_retrieval subquery='%s' names=%s context_preview=%s",
                    entry["subquery"],
                    entry["names"],
                    entry["context_preview"],
                )
            if response.answer:
                logger.info("elder_summary_synthesis: %s", response.answer)
            # Validation and style responses are logged earlier as well
        except Exception:
            pass

        return response

    async def _decompose(
        self,
        query: str,
        ontology_ids: list[int],
        chat_history: Optional[list[dict[str, str]]],
        trace: list[TraceStep],
        entities_hint: Optional[str] = None,
    ) -> list[str]:
        """Decompose query into 1-5 sub-queries."""
        model = self.model_policy.get_model(LLMTask.DECOMPOSE)

        # Build conversation context if chat history exists
        messages = []
        if chat_history:
            # Add recent history for context
            for msg in chat_history[-10:]:  # Use last 10 messages for context
                messages.append({"role": msg["role"], "content": msg["content"]})

        # Add the decomposition prompt
        # Prefer SQL entities (names+descriptions) if provided by API layer
        if entities_hint:
            instances_text = entities_hint
        else:
            # Fallback to graph instance summaries
            try:
                summaries = await self.graph_retriever.instance_summaries(ontology_ids)
            except Exception:
                summaries = []
            if summaries:
                instances_text = "\n".join(
                    [f"- {s['name']}: {s['hint']}" for s in summaries]
                )
            else:
                instances_text = "(no instance summaries available)"

        prompt = DECOMPOSE_PROMPT.format(
            query=query,
            ontology_instances=instances_text,
        )
        messages.append({"role": "user", "content": prompt})

        try:
            # Log prompt
            logger.info("elder_llm_decompose_prompt(model=%s):\n%s", model, "\n".join([m.get("content","") for m in messages]))
            response = await self.llm_client.chat(
                model=model,
                messages=messages,
                temperature=0.7,
            )
            logger.info("elder_llm_decompose_response: %s", response)

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
                        data={
                            "subqueries": subqueries,
                            "model": model,
                            "used_chat_history": bool(chat_history),
                        },
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
            # Use a fresh Neo4j session per subquery to allow safe parallel retrieval
            try:
                from app.graph.neo4j import get_driver
                from app.core.config import get_settings as _get_settings
                from app.integrations.retrieval.neo4j_retriever import (
                    Neo4jGraphRetriever,
                )

                driver = get_driver()
                settings = _get_settings()
                async with driver.session(database=settings.neo4j_database) as session:
                    retr = Neo4jGraphRetriever(session)
                    chunks = await retr.search(
                        query=subquery,
                        ontology_ids=ontology_ids,
                        top_k=top_k,
                    )
                    return (subquery, chunks)
            except Exception as e:
                logger.error(f"Retrieval failed for '{subquery}': {e}")
                return (subquery, [])

        # Bounded parallel retrieval using separate sessions
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _run_with_limit(sq: str):
            async with semaphore:
                return await retrieve_one(sq)

        results = await asyncio.gather(*[_run_with_limit(sq) for sq in subqueries])

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
                logger.info("elder_llm_subanswer_prompt(model=%s, subquery='%s'):\n%s", model, subquery, prompt)
                answer_text = await self.llm_client.chat(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                )
                logger.info("elder_llm_subanswer_response(subquery='%s'): %s", subquery, answer_text)

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
            logger.info("elder_llm_synthesis_prompt(model=%s, refine=%s):\n%s", model, str(refine), prompt)
            answer = await self.llm_client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
            )
            logger.info("elder_llm_synthesis_response(refine=%s): %s", str(refine), answer)

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
            logger.info("elder_llm_validation_prompt(model=%s):\n%s", model, prompt)
            validation = await self.llm_client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            logger.info("elder_llm_validation_response: %s", validation)

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
            logger.info("elder_llm_style_prompt(model=%s):\n%s", model, prompt)
            styled = await self.llm_client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
            )
            logger.info("elder_llm_style_response: %s", styled)

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
