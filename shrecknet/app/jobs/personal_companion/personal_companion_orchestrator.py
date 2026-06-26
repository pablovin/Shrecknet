"""Herald orchestrator core logic for Personal Companion turns."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Awaitable, Callable

from app.core.config_store import get_settings
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.personal_companion.prompts import ROUTING_PROMPT, SYNTHESIS_PROMPT

AgentRunner = Callable[[str], Awaitable[dict[str, Any]]]


class PersonalCompanionOrchestrator:
    """Routing, fan-out, synthesis, and evidence analysis for companion turns."""

    def __init__(self, llm_client: ShreckLLMClient):
        self.llm_client = llm_client

    @staticmethod
    def extract_json_object(raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def keyword_route(query: str) -> dict[str, Any]:
        q = (query or "").lower()
        librarian_terms = (
            "rule",
            "rules",
            "mechanic",
            "mechanics",
            "dexterity",
            "stat",
            "status",
            "dice",
            "roll",
        )
        elder_terms = (
            "story",
            "character",
            "who",
            "when",
            "where",
            "what happened",
            "canon",
            "scene",
            "timeline",
        )
        use_librarian = any(term in q for term in librarian_terms)
        use_elder = any(term in q for term in elder_terms)
        if not use_elder and not use_librarian:
            use_elder = True
        return {
            "use_elder": use_elder,
            "use_librarian": use_librarian,
            "reason": "keyword_fallback",
        }

    async def route_query(self, query: str) -> dict[str, Any]:
        settings = get_settings()
        prompt = ROUTING_PROMPT.format(query=query)
        try:
            raw = await self.llm_client.chat(
                model=settings.model_orchestrator_routing,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                usage_tag="companion_orchestrator.routing",
            )
            parsed = self.extract_json_object(raw)
            use_elder = bool(parsed.get("use_elder"))
            use_librarian = bool(parsed.get("use_librarian"))
            reason = str(parsed.get("reason") or "llm_route")
            if not use_elder and not use_librarian:
                return self.keyword_route(query)
            return {
                "use_elder": use_elder,
                "use_librarian": use_librarian,
                "reason": reason,
            }
        except Exception:
            return self.keyword_route(query)

    async def fanout_tools(
        self,
        *,
        selected_elder_ids: list[str],
        selected_librarian_ids: list[str],
        elder_runner: AgentRunner,
        librarian_runner: AgentRunner,
    ) -> list[dict[str, Any]]:
        tasks: list[asyncio.Task] = []
        for agent_id in selected_elder_ids:
            tasks.append(asyncio.create_task(elder_runner(agent_id)))
        for agent_id in selected_librarian_ids:
            tasks.append(asyncio.create_task(librarian_runner(agent_id)))
        return await asyncio.gather(*tasks) if tasks else []

    @staticmethod
    def normalize_sources(agent_responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        source_index = 1
        for item in agent_responses:
            if not item.get("ok"):
                continue
            agent_id = str(item.get("agent_id") or "")
            agent_name = str(item.get("agent_name") or "")
            agent_job = str(item.get("agent_job") or "")
            for src in item.get("sources") or []:
                source_id = f"S{source_index}"
                source_index += 1
                if agent_job == "elder":
                    normalized.append(
                        {
                            "source_id": source_id,
                            "source_type": "canon",
                            "agent_id": agent_id,
                            "agent_name": agent_name,
                            "node_id": src.get("node_id"),
                            "node_label": src.get("node_label"),
                            "node_name": src.get("node_name"),
                            "score": src.get("score"),
                            "evidence": src.get("evidence_chunks") or [],
                        }
                    )
                elif agent_job == "librarian":
                    normalized.append(
                        {
                            "source_id": source_id,
                            "source_type": "rules",
                            "agent_id": agent_id,
                            "agent_name": agent_name,
                            "library_item_id": src.get("library_item_id"),
                            "book_title": src.get("book_title"),
                            "page_number": src.get("page_number"),
                            "page_url": src.get("page_url"),
                            "pdf_url": src.get("pdf_url"),
                            "score": src.get("score"),
                        }
                    )
        return normalized

    @staticmethod
    def _split_claims(answer: str) -> list[str]:
        text = (answer or "").strip()
        if not text:
            return []
        chunks = re.split(r"(?<=[.!?])\s+", text)
        out = [chunk.strip() for chunk in chunks if chunk.strip()]
        return out[:2]

    @staticmethod
    def build_claims(
        agent_responses: list[dict[str, Any]],
        normalized_sources: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        source_ids_by_agent: dict[str, list[str]] = {}
        for source in normalized_sources:
            key = str(source.get("agent_id") or "")
            if not key:
                continue
            source_ids_by_agent.setdefault(key, []).append(str(source.get("source_id")))

        claims: list[dict[str, Any]] = []
        claim_counter = 1
        for item in agent_responses:
            if not item.get("ok"):
                continue
            agent_id = str(item.get("agent_id") or "")
            answer = str(item.get("answer") or "")
            claim_parts = PersonalCompanionOrchestrator._split_claims(answer)
            for claim_text in claim_parts:
                claim_id = f"C{claim_counter}"
                claim_counter += 1
                claims.append(
                    {
                        "claim_id": claim_id,
                        "text": claim_text,
                        "agent_id": agent_id,
                        "agent_name": item.get("agent_name"),
                        "agent_job": item.get("agent_job"),
                        "source_ids": source_ids_by_agent.get(agent_id, []),
                    }
                )
        return claims

    @staticmethod
    def analyze_claim_relationships(claims: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        contradictions: list[dict[str, Any]] = []
        complements: list[dict[str, Any]] = []

        negation_tokens = {"not", "no", "never", "cannot", "can't", "without"}

        for i, left in enumerate(claims):
            left_text = str(left.get("text") or "").lower()
            left_tokens = set(re.findall(r"[a-z0-9]+", left_text))
            left_has_negation = any(token in left_tokens for token in negation_tokens)
            left_numbers = re.findall(r"\d+", left_text)

            for right in claims[i + 1 :]:
                if str(left.get("agent_job")) == str(right.get("agent_job")):
                    continue
                right_text = str(right.get("text") or "").lower()
                right_tokens = set(re.findall(r"[a-z0-9]+", right_text))
                if not left_tokens or not right_tokens:
                    continue

                overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
                right_has_negation = any(token in right_tokens for token in negation_tokens)
                right_numbers = re.findall(r"\d+", right_text)

                contradictory = False
                reason = ""
                if overlap >= 0.18 and left_has_negation != right_has_negation:
                    contradictory = True
                    reason = "negation_conflict"
                elif overlap >= 0.2 and left_numbers and right_numbers and left_numbers != right_numbers:
                    contradictory = True
                    reason = "numeric_mismatch"

                if contradictory:
                    contradictions.append(
                        {
                            "left_claim_id": left.get("claim_id"),
                            "right_claim_id": right.get("claim_id"),
                            "reason": reason,
                            "left_text": left.get("text"),
                            "right_text": right.get("text"),
                        }
                    )
                    continue

                if overlap >= 0.08:
                    complements.append(
                        {
                            "left_claim_id": left.get("claim_id"),
                            "right_claim_id": right.get("claim_id"),
                            "reason": "cross_source_complement",
                        }
                    )

        return {
            "contradictions": contradictions,
            "complements": complements,
        }

    @staticmethod
    def format_annotated_answer(
        *,
        final_text: str,
        claims: list[dict[str, Any]],
    ) -> str:
        lines = [str(final_text or "").strip(), "", "Claim Anchors:"]
        if not claims:
            lines.append("- No explicit claim anchors available.")
            return "\n".join(lines).strip()

        for claim in claims:
            source_ids = claim.get("source_ids") or []
            source_str = ", ".join(str(item) for item in source_ids) if source_ids else "none"
            lines.append(
                f"- [{claim.get('claim_id')}] {claim.get('text')} (sources: {source_str})"
            )
        return "\n".join(lines).strip()

    async def synthesize_final_answer(
        self,
        *,
        query: str,
        companion_name: str,
        companion_writing_style: str,
        agent_responses: list[dict[str, Any]],
    ) -> str:
        settings = get_settings()
        source_lines: list[str] = []
        for item in agent_responses:
            if not item.get("ok"):
                continue
            answer = str(item.get("answer") or "").strip()
            if not answer:
                continue
            source_lines.append(f"- [{item.get('agent_job')}] {item.get('agent_name')}: {answer}")
        if not source_lines:
            return (
                "I could not retrieve grounded evidence from the selected tools for this "
                "question. Please try a more specific question."
            )

        prompt = SYNTHESIS_PROMPT.format(
            companion_name=companion_name,
            companion_writing_style=companion_writing_style,
            query=query,
            tool_responses="\n".join(source_lines),
        )
        try:
            return await self.llm_client.chat(
                model=settings.model_orchestrator_synthesis,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                usage_tag="companion_orchestrator.synthesis",
            )
        except Exception:
            return "Based on available evidence:\n" + "\n".join(source_lines)

    def build_turn_payload(
        self,
        *,
        session_id: str,
        query: str,
        routing: dict[str, Any],
        selected_tools: dict[str, list[str]],
        agent_responses: list[dict[str, Any]],
        final_text: str,
    ) -> dict[str, Any]:
        normalized_sources = self.normalize_sources(agent_responses)
        claims = self.build_claims(agent_responses, normalized_sources)
        analysis = self.analyze_claim_relationships(claims)
        annotated_text = self.format_annotated_answer(final_text=final_text, claims=claims)
        return {
            "status": "done",
            "session_id": session_id,
            "query": query,
            "routing": routing,
            "selected_tools": selected_tools,
            "agent_responses": agent_responses,
            "sources": normalized_sources,
            "claims": claims,
            "analysis": {
                "method": "heuristic_cross_source_v1",
                "contradictions": analysis.get("contradictions", []),
                "complements": analysis.get("complements", []),
            },
            "final": {
                "text": final_text,
                "annotated_text": annotated_text,
            },
            "tool_failures": [
                {
                    "agent_id": item.get("agent_id"),
                    "agent_name": item.get("agent_name"),
                    "agent_job": item.get("agent_job"),
                    "error": item.get("error"),
                }
                for item in agent_responses
                if not item.get("ok")
            ],
        }
