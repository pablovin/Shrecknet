"""Elder orchestrator with layered retrieval pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import defaultdict
from typing import Any, Optional
from uuid import uuid4

from app.integrations.llm.model_policy import LLMTask, ModelPolicy
from app.integrations.llm.openai_client import OpenAIClient
from app.integrations.retrieval.neo4j_retriever import GraphRetriever
from app.jobs.elder.prompts import DECOMPOSE_PROMPT, SYNTHESIS_PROMPT
from app.jobs.elder.schemas import (
    DecomposedIntent,
    ElderQueryRequest,
    ElderQueryResponse,
    RetrievedChunk,
    SourceEvidenceChunk,
    SourceNode,
    TraceStep,
)
from app.models.agent import Agent

logger = logging.getLogger(__name__)


class ElderOrchestrator:
    """Layered Elder pipeline: query -> retrieve -> consolidate -> rerank -> synthesize."""

    def __init__(
        self,
        llm_client: OpenAIClient,
        model_policy: ModelPolicy,
        graph_retriever: GraphRetriever,
        default_top_k: int = 8,
    ):
        self.llm_client = llm_client
        self.model_policy = model_policy
        self.graph_retriever = graph_retriever
        self.default_top_k = default_top_k
        self.max_subqueries = 10
        self.max_concurrency = 10
        self.last_retrieval_debug: list[dict[str, Any]] = []

    def _query_model(self, fallback_task: LLMTask) -> str:
        model = getattr(self.model_policy, "model_elder_query", None)
        if isinstance(model, str) and model.strip():
            return model.strip()
        return self.model_policy.get_model(fallback_task)

    async def execute(
        self,
        agent: Agent,
        request: ElderQueryRequest,
        chat_history: Optional[list[dict[str, str]]] = None,
    ) -> ElderQueryResponse:
        trace: list[TraceStep] = [] if request.include_trace else []
        trace_id = str(uuid4())
        timings: dict[str, float] = {}
        overall_start = time.monotonic()
        top_k = request.top_k or self.default_top_k
        ontology_ids = [ont.id for ont in agent.ontologies]

        # Layer 1: query construction (decompose + memory summary)
        t0 = time.monotonic()
        if request.fast:
            intents = [
                DecomposedIntent(
                    subquery=request.query,
                    target_data_type="mixed",
                    reason="fast_mode",
                )
            ]
        else:
            intents = await self._decompose(
                request.query,
                ontology_ids,
                chat_history,
                trace,
                request.entities_hint,
            )
            if not intents:
                intents = [
                    DecomposedIntent(
                        subquery=request.query,
                        target_data_type="mixed",
                        reason="fallback",
                    )
                ]
        timings["decompose_ms"] = round((time.monotonic() - t0) * 1000, 2)

        t1 = time.monotonic()
        memory_summary = self._extract_memory_summary(chat_history)
        timings["memory_summary_ms"] = round((time.monotonic() - t1) * 1000, 2)

        # Layer 2: candidate generation
        t2 = time.monotonic()
        retrieval_results = await self._retrieve_intents(
            intents=intents,
            ontology_ids=ontology_ids,
            top_k=top_k,
            candidate_limit=request.candidate_limit,
            rerank_limit=request.rerank_limit,
        )
        intents = self._attach_intent_top_k(intents=intents, retrieval_results=retrieval_results)
        timings["retrieve_ms"] = round((time.monotonic() - t2) * 1000, 2)

        # Layer 3: candidate consolidation
        t3 = time.monotonic()
        consolidated_sources = self._consolidate_sources(retrieval_results)
        timings["consolidate_ms"] = round((time.monotonic() - t3) * 1000, 2)

        # Layer 4: reranking + memory priors
        t4 = time.monotonic()
        memory_priors_applied = self._apply_memory_priors(
            request_query=request.query,
            sources=consolidated_sources,
            memory_summary=memory_summary,
        )
        consolidated_sources.sort(key=lambda s: s.score, reverse=True)
        consolidated_sources = consolidated_sources[: max(top_k, 1)]
        timings["rerank_ms"] = round((time.monotonic() - t4) * 1000, 2)

        # Layer 5: grounded synthesis
        t5 = time.monotonic()
        if request.mode == "context":
            answer = ""
            timings["synthesize_ms"] = 0.0
        else:
            answer = await self._synthesize(
                agent=agent,
                query=request.query,
                sources=consolidated_sources,
            )
            timings["synthesize_ms"] = round((time.monotonic() - t5) * 1000, 2)

        timings["total_ms"] = round((time.monotonic() - overall_start) * 1000, 2)

        logger.info(
            "elder_query_timing trace_id=%s decompose_ms=%.2f memory_summary_ms=%.2f "
            "retrieve_ms=%.2f consolidate_ms=%.2f rerank_ms=%.2f synthesize_ms=%.2f total_ms=%.2f",
            trace_id,
            timings["decompose_ms"],
            timings["memory_summary_ms"],
            timings["retrieve_ms"],
            timings["consolidate_ms"],
            timings["rerank_ms"],
            timings["synthesize_ms"],
            timings["total_ms"],
        )

        if trace is not None:
            trace.append(
                TraceStep(
                    step="timings",
                    data={"trace_id": trace_id, "timings": timings},
                )
            )

        return ElderQueryResponse(
            agent_id=agent.id,
            query=request.query,
            answer=answer,
            timings=timings,
            intents=intents,
            sources=consolidated_sources,
            memory_priors_applied=memory_priors_applied,
            trace_id=trace_id,
            trace=trace if request.include_trace else None,
            retrieval_debug=self.last_retrieval_debug or None,
        )

    async def _decompose(
        self,
        query: str,
        ontology_ids: list[int],
        chat_history: Optional[list[dict[str, str]]],
        trace: list[TraceStep],
        entities_hint: Optional[str] = None,
    ) -> list[DecomposedIntent]:
        model = self._query_model(LLMTask.DECOMPOSE)

        messages: list[dict[str, str]] = []
        if chat_history:
            for msg in chat_history[-8:]:
                messages.append({"role": msg["role"], "content": msg["content"]})

        if entities_hint:
            instances_text = entities_hint
        else:
            try:
                summaries = await self.graph_retriever.instance_summaries(ontology_ids)
            except Exception:
                summaries = []
            instances_text = (
                "\n".join([f"- {s['name']}: {s['hint']}" for s in summaries])
                if summaries
                else "(no instance summaries available)"
            )

        prompt = DECOMPOSE_PROMPT.format(
            query=query,
            ontology_instances=instances_text,
        )
        messages.append({"role": "user", "content": prompt})

        try:
            response = await self.llm_client.chat(
                model=model,
                messages=messages,
                temperature=0.2,
            )
            payload = self._extract_json(response)
            raw_intents = payload.get("intents") if isinstance(payload, dict) else None
            intents: list[DecomposedIntent] = []
            if isinstance(raw_intents, list):
                for item in raw_intents:
                    if not isinstance(item, dict):
                        continue
                    subquery = str(item.get("subquery") or "").strip()
                    if not subquery:
                        continue
                    target = str(item.get("target_data_type") or "mixed").strip().lower()
                    if target not in {"entity", "scene", "milestone", "mixed"}:
                        target = "mixed"
                    reason = str(item.get("reason") or "general").strip() or "general"
                    intents.append(
                        DecomposedIntent(
                            subquery=subquery,
                            target_data_type=target,
                            reason=reason,
                        )
                    )

            if not intents:
                intents = [
                    DecomposedIntent(
                        subquery=query,
                        target_data_type="mixed",
                        reason="fallback_parse",
                    )
                ]

            intents = intents[: self.max_subqueries]

            if trace is not None:
                trace.append(
                    TraceStep(
                        step="decompose",
                        data={
                            "model": model,
                            "intents": [intent.model_dump() for intent in intents],
                            "used_chat_history": bool(chat_history),
                        },
                    )
                )
            return intents
        except Exception as exc:
            logger.error("Decomposition failed: %s", exc)
            return [
                DecomposedIntent(
                    subquery=query,
                    target_data_type="mixed",
                    reason="fallback_error",
                )
            ]

    async def _retrieve_intents(
        self,
        *,
        intents: list[DecomposedIntent],
        ontology_ids: list[int],
        top_k: int,
        candidate_limit: int | None,
        rerank_limit: int | None,
    ) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _run(intent: DecomposedIntent) -> dict[str, Any]:
            async with semaphore:
                started = time.monotonic()
                labels = self._labels_from_target(intent.target_data_type)
                chunks: list[RetrievedChunk] = []
                debug_stats: dict[str, Any] = {}
                try:
                    chunks = await self.graph_retriever.search(
                        query=intent.subquery,
                        ontology_ids=ontology_ids,
                        top_k=top_k,
                        node_scope="everything",
                        allowed_labels=labels,
                        candidate_limit=candidate_limit,
                        rerank_limit=rerank_limit,
                    )
                    # retriever may expose stats per ontology; aggregate best-effort
                    stats_entries = getattr(self.graph_retriever, "last_search_stats", []) or []
                    agg = {"raw_candidates": 0, "after_parent_grouping": 0, "after_dedup": 0, "final_k": 0}
                    for entry in stats_entries:
                        ds = (entry or {}).get("debug_stats") or {}
                        for key in agg:
                            agg[key] += int(ds.get(key) or 0)
                    debug_stats = agg
                except Exception as exc:
                    logger.error("Retrieval failed for intent '%s': %s", intent.subquery, exc)

                duration_ms = round((time.monotonic() - started) * 1000, 2)
                top_ids = [c.node_id for c in chunks[:5]]
                logger.info(
                    "elder_intent_retrieve subquery='%s' target=%s duration_ms=%.2f top_node_ids=%s",
                    intent.subquery,
                    intent.target_data_type,
                    duration_ms,
                    top_ids,
                )
                return {
                    "intent": intent,
                    "chunks": chunks,
                    "duration_ms": duration_ms,
                    "debug_stats": debug_stats,
                }

        tasks = [asyncio.create_task(_run(intent)) for intent in intents]
        results = await asyncio.gather(*tasks)

        self.last_retrieval_debug = [
            {
                "subquery": r["intent"].subquery,
                "target_data_type": r["intent"].target_data_type,
                "duration_ms": r["duration_ms"],
                "counters": r["debug_stats"],
                "top_node_ids": [c.node_id for c in r["chunks"][:5]],
            }
            for r in results
        ]

        return results

    def _attach_intent_top_k(
        self,
        *,
        intents: list[DecomposedIntent],
        retrieval_results: list[dict[str, Any]],
    ) -> list[DecomposedIntent]:
        enriched: list[DecomposedIntent] = []
        for intent, result in zip(intents, retrieval_results):
            chunks: list[RetrievedChunk] = result.get("chunks") or []
            entity_ids: list[str] = []
            scene_ids: list[str] = []
            milestone_ids: list[str] = []

            for chunk in chunks:
                node_id = (chunk.node_id or "").strip()
                if not node_id:
                    continue
                label = (chunk.node_label or "").strip().lower()
                if label == "entityinstance":
                    if node_id not in entity_ids:
                        entity_ids.append(node_id)
                elif label == "scene":
                    if node_id not in scene_ids:
                        scene_ids.append(node_id)
                elif label == "milestone":
                    if node_id not in milestone_ids:
                        milestone_ids.append(node_id)

            enriched.append(
                intent.model_copy(
                    update={
                        "top_k_entities": entity_ids,
                        "top_k_scenes": scene_ids,
                        "top_k_milestones": milestone_ids,
                    }
                )
            )
        if len(enriched) < len(intents):
            enriched.extend(intents[len(enriched) :])
        return enriched

    def _consolidate_sources(self, retrieval_results: list[dict[str, Any]]) -> list[SourceNode]:
        by_node: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "node_id": "",
                "node_label": None,
                "node_name": None,
                "score": 0.0,
                "evidence_chunks": [],
            }
        )

        for result in retrieval_results:
            for chunk in result.get("chunks") or []:
                node_id = chunk.node_id
                if not node_id:
                    continue
                entry = by_node[node_id]
                entry["node_id"] = node_id
                entry["node_label"] = chunk.node_label
                entry["node_name"] = chunk.node_name or chunk.node_alias or chunk.node_id
                entry["score"] = max(float(entry["score"]), float(chunk.score))
                entry["evidence_chunks"].append(
                    SourceEvidenceChunk(
                        chunk_id=chunk.chunk_id,
                        chunk_type=chunk.chunk_type,
                        score=float(chunk.score),
                        text=chunk.text[:300] if chunk.text else None,
                    )
                )

        sources: list[SourceNode] = []
        for node_id, entry in by_node.items():
            evidence_chunks = sorted(
                entry["evidence_chunks"],
                key=lambda x: x.score,
                reverse=True,
            )[:3]
            sources.append(
                SourceNode(
                    node_id=node_id,
                    node_label=entry["node_label"],
                    node_name=entry["node_name"],
                    score=float(entry["score"]),
                    evidence_chunks=evidence_chunks,
                )
            )

        return sources

    def _apply_memory_priors(
        self,
        *,
        request_query: str,
        sources: list[SourceNode],
        memory_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        priors_applied: list[dict[str, Any]] = []
        if not sources:
            return priors_applied

        recent_entities = {e.lower() for e in memory_summary.get("recent_entities", [])}
        temporal_terms = set(memory_summary.get("temporal_terms", []))
        last_answer_terms = set(memory_summary.get("last_answer_terms", []))
        query_has_pronouns = bool(re.search(r"\b(it|they|he|she|that|those|this)\b", request_query.lower()))

        boosted_entity_targets: list[str] = []
        boosted_temporal_targets: list[str] = []
        boosted_disambiguation_targets: list[str] = []
        boosted_continuity_targets: list[str] = []

        for source in sources:
            base = source.score
            name_l = (source.node_name or "").lower()

            if recent_entities and any(token in name_l for token in recent_entities):
                source.score += 0.03
                boosted_entity_targets.append(source.node_id)

            if source.node_label in {"Scene", "Milestone"} and temporal_terms:
                source.score += 0.02
                boosted_temporal_targets.append(source.node_id)

            if query_has_pronouns and source.node_label == "EntityInstance":
                source.score += 0.015
                boosted_disambiguation_targets.append(source.node_id)

            source_text = " ".join((c.text or "") for c in source.evidence_chunks).lower()
            if last_answer_terms and any(term in source_text for term in last_answer_terms):
                source.score += 0.01
                boosted_continuity_targets.append(source.node_id)

            if source.score > 1.0:
                source.score = 1.0
            if source.score < base:
                source.score = base

        if boosted_entity_targets:
            priors_applied.append(
                {
                    "type": "entity_prior",
                    "effect": "boost",
                    "targets": sorted(set(boosted_entity_targets)),
                    "why": "recently discussed entities in chat history",
                    "impact_on_scores": 0.03,
                }
            )
        if boosted_temporal_targets:
            priors_applied.append(
                {
                    "type": "temporal_prior",
                    "effect": "boost",
                    "targets": sorted(set(boosted_temporal_targets)),
                    "why": "temporal references in recent memory",
                    "impact_on_scores": 0.02,
                }
            )
        if boosted_disambiguation_targets:
            priors_applied.append(
                {
                    "type": "disambiguation_prior",
                    "effect": "boost",
                    "targets": sorted(set(boosted_disambiguation_targets)),
                    "why": "pronoun/reference ambiguity in query",
                    "impact_on_scores": 0.015,
                }
            )
        if boosted_continuity_targets:
            priors_applied.append(
                {
                    "type": "continuity_prior",
                    "effect": "boost",
                    "targets": sorted(set(boosted_continuity_targets)),
                    "why": "follow-up continuity with previous answer",
                    "impact_on_scores": 0.01,
                }
            )

        return priors_applied

    async def _synthesize(
        self,
        *,
        agent: Agent,
        query: str,
        sources: list[SourceNode],
    ) -> str:
        if not sources:
            return (
                "I couldn't find relevant grounded evidence in the knowledge base for this question. "
                "Please try rephrasing or ask for a narrower scope."
            )

        model = self._query_model(LLMTask.SYNTHESIS)
        source_lines: list[str] = []
        for src in sources[:12]:
            source_lines.append(
                f"- [{src.node_label or 'Node'}] {src.node_name or src.node_id} "
                f"(id={src.node_id}, score={src.score:.3f})"
            )
            for ev in src.evidence_chunks[:2]:
                snippet = (ev.text or "").replace("\n", " ").strip()
                if len(snippet) > 220:
                    snippet = snippet[:220] + "…"
                source_lines.append(
                    f"  * chunk={ev.chunk_id or 'n/a'} type={ev.chunk_type or 'n/a'} "
                    f"score={ev.score:.3f} text={snippet}"
                )

        sources_block = "\n".join(source_lines)
        prompt = SYNTHESIS_PROMPT.format(
            agent_name=agent.name,
            writing_style=agent.writing_style or "thoughtful, concise mentor",
            query=query,
            sources_block=sources_block,
        )

        try:
            return await self.llm_client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
        except Exception as exc:
            logger.error("Synthesis failed: %s", exc)
            return "I had trouble synthesizing a response from the retrieved evidence. Please try again."

    @staticmethod
    def _labels_from_target(target_data_type: str) -> list[str]:
        target = (target_data_type or "mixed").strip().lower()
        if target == "entity":
            return ["EntityInstance"]
        if target == "scene":
            return ["Scene"]
        if target == "milestone":
            return ["Milestone"]
        return ["EntityInstance", "Scene", "Milestone"]

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass

        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    @staticmethod
    def _extract_memory_summary(
        chat_history: Optional[list[dict[str, str]]],
    ) -> dict[str, Any]:
        if not chat_history:
            return {
                "recent_entities": [],
                "temporal_terms": [],
                "last_answer_terms": [],
            }

        recent = chat_history[-8:]
        entity_tokens: list[str] = []
        temporal_terms: list[str] = []
        last_answer_terms: list[str] = []

        temporal_vocab = {
            "before",
            "after",
            "during",
            "then",
            "later",
            "earlier",
            "next",
            "previous",
            "recent",
            "timeline",
            "milestone",
        }

        assistant_messages = [m.get("content", "") for m in recent if m.get("role") == "assistant"]
        last_assistant = assistant_messages[-1] if assistant_messages else ""

        for msg in recent:
            text = str(msg.get("content") or "")
            caps = re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", text)
            for token in caps:
                token = token.strip()
                if token not in entity_tokens:
                    entity_tokens.append(token)
            low = text.lower()
            for term in temporal_vocab:
                if term in low and term not in temporal_terms:
                    temporal_terms.append(term)

        for token in re.findall(r"\b[a-z]{4,}\b", last_assistant.lower())[:30]:
            if token not in {"that", "with", "from", "this", "have", "will"}:
                last_answer_terms.append(token)

        return {
            "recent_entities": entity_tokens[:12],
            "temporal_terms": temporal_terms[:8],
            "last_answer_terms": last_answer_terms[:20],
        }
