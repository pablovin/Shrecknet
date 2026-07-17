"""Default Elder query and retrieval v2 orchestration."""

from __future__ import annotations

import json
import re
import time
from difflib import SequenceMatcher
from typing import Any
from uuid import uuid4

from app.integrations.llm.model_policy import LLMTask
from app.core.config_store import LLMModelTarget
from app.jobs.elder.context_budget import partition_complete_records, serialize_evidence
from app.jobs.elder.debug_artifacts import ElderDebugArtifacts
from app.jobs.elder.evidence import assemble_evidence, compact_synthesis_evidence
from app.jobs.elder.executor import ElderRetrievalExecutor
from app.jobs.elder.grounding import build_grounding_package
from app.jobs.elder.planner import create_retrieval_plan
from app.jobs.elder.prompts import (
    V2_GROUNDING_RULES_PROMPT,
    V2_OVERFLOW_EVIDENCE_PROMPT,
    V2_OVERFLOW_FINAL_PROMPT,
    V2_PERSONA_PROMPT,
    V2_SYNTHESIS_PROMPT,
)
from app.jobs.elder.schemas import (
    ElderRetrievalPlan,
    ElderRetrievalPlanStep,
    ElderQueryRequest,
    ElderQueryResponse,
    SourceNode,
    TraceStep,
)
from app.jobs.elder.v2_schemas import RetrievalPlan


PIPELINE_VERSION = "elder-query-retrieval-v2"


class ElderQueryV2:
    """Two-call normal-path Elder with deterministic retrieval between calls."""

    def __init__(
        self,
        llm_client,
        model_policy,
        graph_retriever,
        default_top_k: int = 20,
        llm_max_concurrency: int = 1,
        debug_artifacts_enabled: bool = False,
    ):
        self.llm_client = llm_client
        self.model_policy = model_policy
        self.graph_retriever = graph_retriever
        self.default_top_k = default_top_k
        self.llm_max_concurrency = max(1, int(llm_max_concurrency))
        self.repair_json_model = (
            getattr(model_policy, "model_agents_repair_json", None)
            or self._query_model(LLMTask.DECOMPOSE)
        )
        self.debug_artifacts_enabled = bool(debug_artifacts_enabled)

    def _query_model(self, fallback_task: LLMTask) -> LLMModelTarget:
        model = getattr(self.model_policy, "model_elder", None)
        if isinstance(model, LLMModelTarget):
            return model
        if isinstance(model, str) and model.strip():
            return LLMModelTarget(provider="openai", name=model.strip())
        return self.model_policy.get_model(fallback_task)

    async def _match_query_entities(self, *, query: str, ontology_ids: list[int]) -> list[dict[str, Any]]:
        normalized_query = self._normalize_text(query)
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for ontology_id in ontology_ids:
            skip = 0
            while True:
                rows = await self.graph_retriever.list_entities_by_ontology(
                    ontology_id, skip=skip, limit=500
                )
                if not rows:
                    break
                for row in rows:
                    node_id = str(row.get("node_id") or "").strip()
                    alias = str(row.get("alias") or "").strip()
                    if not node_id or not alias or node_id in seen:
                        continue
                    confidence = self._query_alias_match_score(normalized_query, alias)
                    if confidence < 0.6:
                        continue
                    results.append(
                        {
                            "node_id": node_id,
                            "alias": alias,
                            "entity_definition_id": row.get("entity_definition_id"),
                            "confidence": round(confidence, 4),
                        }
                    )
                    seen.add(node_id)
                if len(rows) < 500:
                    break
                skip += 500
        return sorted(results, key=lambda item: item["confidence"], reverse=True)[:8]

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", value.lower())).strip()

    @classmethod
    def _query_alias_match_score(cls, query_normalized: str, alias: str) -> float:
        alias_normalized = cls._normalize_text(alias)
        if not alias_normalized:
            return 0.0
        if alias_normalized in query_normalized:
            return 1.0
        alias_tokens = alias_normalized.split()
        query_tokens = query_normalized.split()
        overlap = sum(token in query_tokens for token in alias_tokens) / max(1, len(alias_tokens))
        windows = (
            " ".join(query_tokens[index : index + max(1, len(alias_tokens))])
            for index in range(max(1, len(query_tokens) - len(alias_tokens) + 1))
        )
        partial = max(
            (SequenceMatcher(None, alias_normalized, window).ratio() for window in windows),
            default=0.0,
        )
        return max(overlap, SequenceMatcher(None, alias_normalized, query_normalized).ratio() * 0.8, partial * 0.95)

    @classmethod
    def _is_generic_entity_overview(cls, query: str, resolved_entities: list[dict[str, Any]]) -> bool:
        if len(resolved_entities) != 1 or float(resolved_entities[0].get("confidence") or 0) < 0.99:
            return False
        normalized = cls._normalize_text(query)
        alias = cls._normalize_text(str(resolved_entities[0].get("alias") or ""))
        if not alias or alias not in normalized:
            return False
        patterns = (
            r"^(what|who) (can you tell me|do you know) about ",
            r"^tell me about ", r"^give me (an |a )?(overview|profile) of ",
            r"^what is known about ", r"^who is ",
        )
        exhaustive = {"complete", "exhaustive", "entire", "full history", "everything"}
        return any(re.search(pattern, normalized) for pattern in patterns) and not any(
            term in normalized for term in exhaustive
        )

    @staticmethod
    def _overview_plan(query: str, entity: dict[str, Any]) -> RetrievalPlan:
        alias = str(entity.get("alias") or "the resolved entity")
        return RetrievalPlan.model_validate({
            "answer_goal": (
                f"Provide a useful overview of {alias}'s identity, defining characteristics, "
                "important connections, and a few major narrative developments."
            ),
            "response_scope": "standard",
            "evidence_budget_tokens": 10_000,
            "steps": [
                {"id": "entity_profile", "operation": "exact_lookup", "query": alias,
                 "entity_refs": [alias], "target_data_type": "entity", "limit": 1},
                {"id": "entity_context", "operation": "hybrid_search",
                 "query": f"Important characteristics, actions, relationships, and major developments involving {alias}",
                 "inputs": ["entity_profile"], "entity_refs": [alias],
                 "target_data_type": "mixed", "limit": 14},
            ],
        })

    @staticmethod
    def _entity_bindings(resolved_entities: list[dict[str, Any]]) -> dict[str, list[str]]:
        bindings: dict[str, list[str]] = {}
        for row in resolved_entities:
            alias = str(row.get("alias") or "").casefold()
            node_id = str(row.get("node_id") or "")
            if alias and node_id:
                bindings.setdefault(alias, []).append(node_id)
        return bindings

    @staticmethod
    def _extract_memory_summary(chat_history) -> dict[str, Any]:
        recent = list(chat_history or [])[-8:]
        names: list[str] = []
        temporal: list[str] = []
        temporal_terms = {"before", "after", "during", "later", "earlier", "latest", "timeline"}
        for message in recent:
            text = str(message.get("content") or "")
            for name in re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", text):
                if name not in names:
                    names.append(name)
            for term in temporal_terms:
                if term in text.lower() and term not in temporal:
                    temporal.append(term)
        assistant = [str(row.get("content") or "") for row in recent if row.get("role") == "assistant"]
        last_terms = re.findall(r"\b[a-z]{4,}\b", assistant[-1].lower())[:20] if assistant else []
        return {"recent_entities": names[:12], "temporal_terms": temporal[:8], "last_answer_terms": last_terms}

    @staticmethod
    def _apply_memory_priors(
        *, request_query: str, sources: list[SourceNode], memory_summary: dict[str, Any],
        query_entity_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        applied: list[dict[str, Any]] = []
        recent = {value.lower() for value in memory_summary.get("recent_entities", [])}
        query_matches: list[str] = []
        memory_matches: list[str] = []
        for source in sources:
            if query_entity_ids and source.node_id in query_entity_ids:
                source.score = min(1.0, source.score + 0.04)
                query_matches.append(source.node_id)
            if recent and any(value in (source.node_name or "").lower() for value in recent):
                source.score = min(1.0, source.score + 0.03)
                memory_matches.append(source.node_id)
        if query_matches:
            applied.append({"type": "query_entity_match_prior", "effect": "boost", "targets": query_matches, "why": "resolved query entities", "impact_on_scores": 0.04})
        if memory_matches:
            applied.append({"type": "entity_prior", "effect": "boost", "targets": memory_matches, "why": "recent conversation entities", "impact_on_scores": 0.03})
        return applied

    async def execute(self, agent, request: ElderQueryRequest, chat_history=None) -> ElderQueryResponse:
        debug = ElderDebugArtifacts.create(enabled=self.debug_artifacts_enabled)
        started = time.monotonic()
        trace_id = str(uuid4())
        usage_prefix = f"elder.v2.{trace_id}"
        usage_start = self._usage_event_count()
        trace: list[TraceStep] = []
        timings: dict[str, float] = {}
        ontology_ids = [ontology.id for ontology in (agent.ontologies or [])]
        if not ontology_ids:
            raise ValueError("Elder agent has no assigned ontologies")

        resolve_started = time.monotonic()
        if callable(getattr(self.graph_retriever, "list_entities_by_ontology", None)):
            resolved_entities = await self._match_query_entities(
                query=request.query, ontology_ids=ontology_ids
            )
        else:
            resolved_entities = []
        grounding = await build_grounding_package(
            retriever=self.graph_retriever,
            ontology_ids=ontology_ids,
            instance_id=request.instance_id,
            definitions=request.grounding_definitions,
            resolved_entities=resolved_entities,
            chat_history=chat_history,
        )
        debug.write(
            "request_and_grounding",
            input={"agent_id": agent.id, "request": request, "chat_history": chat_history or []},
            output=grounding,
        )
        timings["grounding_ms"] = round((time.monotonic() - resolve_started) * 1000, 2)

        plan_started = time.monotonic()
        if self._is_generic_entity_overview(request.query, resolved_entities):
            plan = self._overview_plan(request.query, resolved_entities[0])
            debug.write("retrieval_plan_fast_path", input={"query": request.query}, output=plan)
        else:
            plan = await create_retrieval_plan(
                llm_client=self.llm_client,
                model=self._query_model(LLMTask.DECOMPOSE),
                query=request.query,
                grounding=grounding,
                repair_model=self.repair_json_model,
                debug=debug,
                usage_tag=f"{usage_prefix}.plan",
            )
        timings["plan_ms"] = round((time.monotonic() - plan_started) * 1000, 2)
        trace.append(TraceStep(step="retrieval_plan", data=plan.model_dump()))

        retrieval_started = time.monotonic()
        executor = ElderRetrievalExecutor(self.graph_retriever)
        step_results, waves, retrieval_debug = await executor.execute(
            plan=plan,
            ontology_ids=ontology_ids,
            instance_id=request.instance_id,
            candidate_limit=request.candidate_limit or 120,
            rerank_limit=request.rerank_limit or 50,
            entity_bindings=self._entity_bindings(resolved_entities),
            definitions=grounding.get("definitions") or [],
        )
        debug.write(
            "deterministic_retrieval",
            input={"plan": plan, "waves": waves},
            output={"step_results": step_results, "debug": retrieval_debug},
        )
        timings["retrieve_ms"] = round((time.monotonic() - retrieval_started) * 1000, 2)
        trace.append(TraceStep(step="retrieval_waves", data={"waves": waves}))

        consolidate_started = time.monotonic()
        evidence, sources = await assemble_evidence(
            retriever=self.graph_retriever,
            plan=plan,
            step_results=step_results,
            limit=request.top_k or self.default_top_k,
            ontology_ids=ontology_ids,
            instance_id=request.instance_id,
        )
        debug.write(
            "unified_evidence",
            input={"selected_limit": request.top_k or self.default_top_k},
            output={"evidence": evidence, "sources": sources},
        )
        timings["consolidate_ms"] = round((time.monotonic() - consolidate_started) * 1000, 2)

        rerank_started = time.monotonic()
        memory_summary = self._extract_memory_summary(chat_history)
        memory_priors = self._apply_memory_priors(
            request_query=request.query,
            sources=sources,
            memory_summary=memory_summary,
            query_entity_ids={row["node_id"] for row in resolved_entities},
        )
        timings["rerank_ms"] = round((time.monotonic() - rerank_started) * 1000, 2)

        synthesis_started = time.monotonic()
        overflow_passes = 0
        if request.mode == "context":
            answer = ""
        elif not evidence:
            failed_steps = [row for row in retrieval_debug if row.get("status") == "failed"]
            if failed_steps:
                operations = ", ".join(str(row.get("operation")) for row in failed_steps)
                answer = (
                    "I couldn't complete the knowledge-base retrieval required for this question "
                    f"because these operations failed: {operations}."
                )
            else:
                answer = (
                    "I couldn't find relevant grounded evidence in the knowledge base for this question. "
                    "Please try rephrasing or ask for a narrower scope."
                )
        else:
            synthesis_evidence = compact_synthesis_evidence(
                evidence, evidence_budget_tokens=plan.evidence_budget_tokens
            )
            answer, overflow_passes = await self._synthesize_v2(
                agent=agent,
                query=request.query,
                evidence=synthesis_evidence,
                chat_history=chat_history,
                debug=debug,
                usage_prefix=usage_prefix,
            )
        timings["synthesize_ms"] = round((time.monotonic() - synthesis_started) * 1000, 2)
        timings["total_ms"] = round((time.monotonic() - started) * 1000, 2)
        trace.extend(
            [
                TraceStep(
                    step="evidence",
                    data={
                        "selected_records": len(evidence),
                        "complete": all(item.complete for item in evidence),
                        "overflow_passes": overflow_passes,
                    },
                ),
                TraceStep(step="timings", data={"trace_id": trace_id, "timings": timings}),
            ]
        )
        retrieval_debug.append(
            {
                "pipeline_version": PIPELINE_VERSION,
                "waves": waves,
                "evidence_records": len(evidence),
                "overflow_passes": overflow_passes,
            }
        )
        llm_usage, llm_usage_totals = self._usage_for_request(
            start_index=usage_start, usage_prefix=usage_prefix
        )
        self._print_llm_usage(
            trace_id=trace_id,
            agent_id=agent.id,
            calls=llm_usage,
            totals=llm_usage_totals,
        )
        trace.append(
            TraceStep(
                step="llm_usage",
                data={"calls": llm_usage, "totals": llm_usage_totals},
            )
        )
        response = ElderQueryResponse(
            agent_id=agent.id,
            query=request.query,
            answer=answer,
            timings=timings,
            retrieval_plan=self._public_retrieval_plan(plan),
            sources=sources,
            memory_priors_applied=memory_priors,
            trace_id=trace_id,
            trace=trace if request.include_trace else None,
            retrieval_debug=retrieval_debug,
            pipeline_version=PIPELINE_VERSION,
            llm_usage=llm_usage,
            llm_usage_totals=llm_usage_totals,
        )
        debug.write("elder_response", input={"query": request.query}, output=response)
        debug.write_final_response(response)
        debug.write_manifest(
            trace_id=trace_id,
            agent_id=agent.id,
            query=request.query,
            source_count=len(sources),
            overflow_passes=overflow_passes,
            llm_usage=llm_usage,
            llm_usage_totals=llm_usage_totals,
        )
        return response

    @staticmethod
    def _public_retrieval_plan(plan: RetrievalPlan) -> ElderRetrievalPlan:
        return ElderRetrievalPlan(
            answer_goal=plan.answer_goal,
            response_scope=plan.response_scope,
            evidence_budget_tokens=plan.evidence_budget_tokens,
            query_intent=plan.query_intent.model_dump(),
            steps=[
                ElderRetrievalPlanStep(
                    id=step.id,
                    purpose=step.purpose,
                    operation=step.operation,
                    query=step.query,
                    inputs=step.inputs,
                    entity_refs=step.entity_refs,
                    temporal=step.temporal.model_dump(),
                    traversal=step.traversal.model_dump(),
                    target_data_type=step.target_data_type,
                    limit=step.limit,
                    hydration_mode=step.hydration_mode,
                    context_chunks_before=step.context_chunks_before,
                    context_chunks_after=step.context_chunks_after,
                    max_tokens_per_source=step.max_tokens_per_source,
                )
                for step in plan.steps
            ],
        )

    async def _synthesize_v2(
        self, *, agent, query, evidence, chat_history, debug: ElderDebugArtifacts,
        usage_prefix: str,
    ) -> tuple[str, int]:
        persona = V2_PERSONA_PROMPT.format(
            agent_name=agent.name,
            writing_style=agent.writing_style or "thoughtful, precise mentor",
        )
        rules = V2_GROUNDING_RULES_PROMPT
        conversation = json.dumps(
            [
                {"role": row.get("role"), "content": row.get("content")}
                for row in list(chat_history or [])[-8:]
                if row.get("role") in {"user", "assistant"} and row.get("content")
            ],
            ensure_ascii=False,
        )
        fixed = f"{persona}\n{rules}\nQUERY:\n{query}\nCONVERSATION:\n{conversation}"
        batches = partition_complete_records(evidence, fixed_prompt=fixed)
        model = self._query_model(LLMTask.SYNTHESIS)
        if len(batches) == 1:
            block = "\n".join(serialize_evidence(record) for record in batches[0])
            prompt = V2_SYNTHESIS_PROMPT.format(
                persona=persona,
                rules=rules,
                query=query,
                conversation_json=conversation,
                evidence_block=block,
            )
            answer = await self.llm_client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                usage_tag=f"{usage_prefix}.synthesize",
            )
            debug.write("synthesis_llm", input={"prompt": prompt}, output={"raw": answer})
            return answer, 0

        memoranda: list[str] = []
        for index, batch in enumerate(batches):
            block = "\n".join(serialize_evidence(record) for record in batch)
            prompt = V2_OVERFLOW_EVIDENCE_PROMPT.format(
                persona=persona,
                rules=rules,
                query=query,
                conversation_json=conversation,
                batch_number=index + 1,
                batch_count=len(batches),
                evidence_block=block,
            )
            memorandum = await self.llm_client.chat(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    usage_tag=f"{usage_prefix}.overflow_evidence.{index + 1}",
            )
            memoranda.append(memorandum)
            debug.write(
                f"overflow_evidence_llm_{index + 1}",
                input={"prompt": prompt},
                output={"raw": memorandum},
            )
        combined = "\n\n".join(
            f"MEMORANDUM {index + 1}:\n{memo}" for index, memo in enumerate(memoranda)
        )
        final_prompt = V2_OVERFLOW_FINAL_PROMPT.format(
            persona=persona,
            rules=rules,
            query=query,
            conversation_json=conversation,
            memoranda_block=combined,
        )
        answer = await self.llm_client.chat(
            model=model,
            messages=[{"role": "user", "content": final_prompt}],
            temperature=0.3,
            usage_tag=f"{usage_prefix}.overflow_final",
        )
        debug.write("overflow_final_llm", input={"prompt": final_prompt}, output={"raw": answer})
        return answer, len(batches)

    def _usage_event_count(self) -> int:
        getter = getattr(self.llm_client, "get_usage_event_count", None)
        return int(getter()) if callable(getter) else 0

    def _usage_for_request(
        self, *, start_index: int, usage_prefix: str
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        getter = getattr(self.llm_client, "get_usage_events_since", None)
        if not callable(getter):
            return [], {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        rows: list[dict[str, Any]] = []
        for event in getter(start_index):
            tag = str(event.get("usage_tag") or "")
            if not tag.startswith(f"{usage_prefix}."):
                continue
            input_tokens = int(event.get("prompt_tokens") or event.get("input_tokens_est") or 0)
            output_tokens = int(event.get("completion_tokens") or 0)
            total_tokens = int(event.get("total_tokens") or (input_tokens + output_tokens))
            rows.append(
                {
                    "call": len(rows) + 1,
                    "stage": tag.removeprefix(f"{usage_prefix}."),
                    "usage_tag": tag,
                    "model": str(event.get("model") or "unknown"),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens,
                }
            )
        totals = {
            "calls": len(rows),
            "input_tokens": sum(row["input_tokens"] for row in rows),
            "output_tokens": sum(row["output_tokens"] for row in rows),
            "total_tokens": sum(row["total_tokens"] for row in rows),
        }
        return rows, totals

    @staticmethod
    def _print_llm_usage(
        *, trace_id: str, agent_id: str, calls: list[dict[str, Any]], totals: dict[str, int]
    ) -> None:
        """Emit stable, grep-friendly per-call and total token accounting to stdout."""
        for row in calls:
            print(
                "[ELDER_LLM_USAGE] "
                f"trace_id={trace_id} agent_id={agent_id} call={row['call']} "
                f"stage={row['stage']} model={row['model']} "
                f"input_tokens={row['input_tokens']} output_tokens={row['output_tokens']} "
                f"total_tokens={row['total_tokens']}",
                flush=True,
            )
        print(
            "[ELDER_LLM_USAGE_TOTAL] "
            f"trace_id={trace_id} agent_id={agent_id} calls={totals.get('calls', 0)} "
            f"input_tokens={totals.get('input_tokens', 0)} "
            f"output_tokens={totals.get('output_tokens', 0)} "
            f"total_tokens={totals.get('total_tokens', 0)}",
            flush=True,
        )
