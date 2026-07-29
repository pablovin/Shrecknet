"""Librarian Query v2 evidence-loop orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config_store import LLMModelTarget
from app.jobs.character_incorporation import (
    NeutralAnswer,
    cited_ids,
    incorporate_character,
    neutral_answer_schema,
    normalize_target_language,
    render_answer,
)
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.librarian.debug_artifacts import LibrarianDebugArtifacts, debug_value
from app.jobs.librarian.prompts import (
    SIMPLIFIED_ANSWER_STYLE_PROMPT,
    SYNTHESIS_SYSTEM_PROMPT,
    planner_messages,
)
from app.jobs.librarian.retrieval_strategies import get_librarian_retrieval_strategy, is_table_like_query
from app.jobs.librarian.schemas import LibrarianQueryRequest, LibrarianQueryResponse, RetrievedChunk
from app.jobs.elder.context_budget import estimate_tokens
from app.jobs.shrecknet import repair_invalid_json
from app.models.agent import Agent
from app.models.library import LibraryItem

logger = logging.getLogger(__name__)

SYNTHESIS_EVIDENCE_TOKEN_BUDGET = 30_000

class LibrarianQueryV2:
    """Plan, retrieve, and synthesize grounded book evidence."""

    def __init__(self, llm_client: ShreckLLMClient,
                 planner_model: LLMModelTarget | str = "gpt-4o",
                 synthesis_model: LLMModelTarget | str = "gpt-4o",
                 character_model: LLMModelTarget | str | None = None,
                 repair_json_model: LLMModelTarget | str | None = None,
                 debug_artifacts_enabled: bool = False):
        self.llm_client = llm_client
        self.planner_model = planner_model
        self.synthesis_model = synthesis_model
        self.character_model = character_model
        self.repair_json_model = repair_json_model or planner_model
        self.debug_artifacts_enabled = bool(debug_artifacts_enabled)
        self.retrieval = get_librarian_retrieval_strategy()

    async def execute(self, agent: Agent, request: LibrarianQueryRequest,
                      db_session: AsyncSession) -> LibrarianQueryResponse:
        started, run_id = time.monotonic(), uuid.uuid4().hex[:12]
        debug = LibrarianDebugArtifacts.create(enabled=self.debug_artifacts_enabled)
        trace: list[dict[str, Any]] = []
        ontology_ids = [int(item.id) for item in agent.ontologies]
        rpg_system = self._rpg_systems(agent)
        top_k = request.top_k or 6
        debug.write("query_request_and_scope", input={"agent_id": agent.id, "request": debug_value(request)},
                    output={"run_id": run_id, "ontology_ids": ontology_ids, "rpg_system": rpg_system,
                            "effective_top_k_per_need": top_k})
        needs, target_language = await self._plan(request.query, rpg_system, debug)
        trace.append({"step": "v2_plan", "data": {
            "information_needs": needs, "target_language": target_language,
        }})
        active = {oid: await self._active_items(db_session, oid, request.library_item_ids) for oid in ontology_ids}
        evidence = await self._retrieve_pass(
            needs, 0, ontology_ids, active, request, top_k, trace, debug, run_id
        )
        evidence = self._merge([], evidence)
        trace.append({"step": "v2_evidence_merge", "data": {
            "pass": 0, "retrieved": len(evidence), "added": len(evidence),
            "total": len(evidence)}})

        evidence.sort(key=lambda row: float(row.get("score", 0)), reverse=True)
        evidence = evidence[:max(14, min(30, top_k * max(1, len(needs))))]
        metadata = await self._metadata(db_session, {int(row["library_item_id"]) for row in evidence})
        chunks = self._chunks(evidence, metadata)
        answer, sources = None, []
        if request.mode in ("nl", "both"):
            if not chunks:
                neutral = NeutralAnswer.model_validate({
                    "claims": [{
                        "id": "uncertainty-1",
                        "text": "I couldn't find any relevant information in the available books to answer your question.",
                        "citations": [],
                    }],
                    "uncertainty": "No relevant book evidence was found.",
                })
                rendered = await incorporate_character(
                    llm_client=self.llm_client,
                    model=self.character_model,
                    original_query=request.query,
                    target_language=target_language,
                    agent_name=agent.name,
                    agent_description=getattr(agent, "description", None),
                    writing_style=agent.writing_style,
                    answer=neutral,
                    usage_tag="librarian_character_incorporation",
                    renderer_name="librarian_character_incorporation",
                    required=True,
                    repair_model=self.repair_json_model,
                )
                answer = render_answer(
                    neutral, rendered=rendered, citation_order=[]
                )
                trace.append({"step": "character_incorporation", "data": {
                    "target_language": target_language,
                    "rendered": rendered is not None,
                    "fallback": rendered is None,
                }})
            else:
                synthesis_chunks, evidence_tokens = self._select_synthesis_evidence(chunks)
                trace.append({"step": "v2_synthesis_evidence_budget", "data": {
                    "candidate_chunks": len(chunks),
                    "selected_chunks": len(synthesis_chunks),
                    "estimated_evidence_tokens": evidence_tokens,
                    "budget_tokens": SYNTHESIS_EVIDENCE_TOKEN_BUDGET,
                    "overflow_chunk_allowed": evidence_tokens > SYNTHESIS_EVIDENCE_TOKEN_BUDGET,
                }})
                logger.info(
                    "librarian_v2_synthesis_evidence_budget run_id=%s candidate_chunks=%s selected_chunks=%s estimated_evidence_tokens=%s budget_tokens=%s",
                    run_id, len(chunks), len(synthesis_chunks), evidence_tokens, SYNTHESIS_EVIDENCE_TOKEN_BUDGET,
                )
                neutral = await self._synthesize(
                    request.query, synthesis_chunks,
                    rpg_system, trace, debug,
                )
                rendered = await incorporate_character(
                    llm_client=self.llm_client,
                    model=self.character_model,
                    original_query=request.query,
                    target_language=target_language,
                    agent_name=agent.name,
                    agent_description=getattr(agent, "description", None),
                    writing_style=agent.writing_style,
                    answer=neutral,
                    usage_tag="librarian_character_incorporation",
                    renderer_name="librarian_character_incorporation",
                    required=True,
                    repair_model=self.repair_json_model,
                )
                trace.append({"step": "character_incorporation", "data": {
                    "target_language": target_language,
                    "rendered": rendered is not None,
                    "fallback": rendered is None,
                }})
                used_source_ids = cited_ids(neutral, rendered=rendered)
                sources = [
                    chunk for chunk in chunks
                    if chunk.source_id in used_source_ids
                ]
                answer = render_answer(
                    neutral,
                    rendered=rendered,
                    citation_order=[
                        chunk.source_id for chunk in sources if chunk.source_id
                    ],
                )
                trace.append({"step": "citation_rendering", "data": {"sources_used": len(sources)}})
                debug.write(
                    "citation_rendering",
                    input={"claim_associations": [row.model_dump() for row in (rendered or [])]},
                    output={"answer": answer},
                )
        used = list({chunk.library_item_id for chunk in (sources or chunks)})
        response = LibrarianQueryResponse(
            agent_id=agent.id, mode=request.mode, query=request.query, subqueries=needs,
            answer=answer, chunks=chunks if request.mode in ("context", "both") else [],
            sources_used=sources, library_items_used=used,
            trace=trace if request.include_trace else None,
        )
        debug.write("final_response", input={"mode": request.mode}, output=response.model_dump())
        elapsed = (time.monotonic() - started) * 1000
        debug.write_manifest(run_id=run_id, strategy="v2", model=str(self.synthesis_model), status="success",
                             pass_count=1, stop_reason="retrieval_complete", elapsed_ms=elapsed)
        logger.info("librarian_v2_complete run_id=%s evidence=%s elapsed_ms=%.1f",
                    run_id, len(chunks), elapsed)
        return response

    async def _plan(
        self, query: str, rpg_system: str, debug: LibrarianDebugArtifacts
    ) -> tuple[list[str], str]:
        messages, raw = planner_messages(query=query, rpg_system=rpg_system), ""
        try:
            raw = str(await self._structured_chat(
                model=self.planner_model,
                messages=messages,
                temperature=0.0,
                usage_tag="librarian_plan",
                name="librarian_plan",
                schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "information_needs": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 8,
                            "items": {"type": "string", "minLength": 3},
                        },
                        "target_language": {"type": "string", "minLength": 2},
                    },
                    "required": ["information_needs", "target_language"],
                },
            ))
            parsed, repaired = await self._parse_or_repair(
                raw, '{"information_needs":["standalone search question"],"target_language":"und"}'
            )
            needs = self._clean(parsed.get("information_needs"), 8)
            if not needs:
                repaired_text = await repair_invalid_json(
                    llm_client=self.llm_client,
                    model=self.repair_json_model,
                    malformed_text=json.dumps(parsed, ensure_ascii=False),
                    schema_hint='{"information_needs":["standalone search question"],"target_language":"und"}',
                    usage_tag="agents.json_repair",
                )
                parsed = self._json(repaired_text)
                repaired = True
                needs = self._clean(parsed.get("information_needs"), 8)
            if not needs:
                raise ValueError("planner returned no valid information needs")
            target_language = normalize_target_language(parsed.get("target_language"))
            fallback = False
        except Exception as exc:
            logger.warning("librarian_v2_planner_fallback error=%s", exc)
            needs, target_language, fallback = [query.strip()], "und", True
        debug.write("v2_planner", input={"messages": messages},
                    output={"raw_response": raw, "information_needs": needs,
                            "target_language": target_language, "fallback": fallback,
                            "json_repaired": locals().get("repaired", False)})
        return needs, target_language

    async def _retrieve_pass(self, needs: list[str], pass_number: int, ontology_ids: list[int],
                             active: dict[int, list[int]], request: LibrarianQueryRequest, top_k: int,
                             trace: list[dict[str, Any]], debug: LibrarianDebugArtifacts,
                             run_id: str) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(4)
        async def one(need: str, oid: int) -> list[dict[str, Any]]:
            async with semaphore:
                local_trace: list[dict[str, Any]] = []
                try:
                    rows = await self.retrieval.retrieve(
                        query=need, ontology_id=oid, library_item_ids=request.library_item_ids,
                        active_library_item_ids=active.get(oid, []), top_k=top_k,
                        trace=local_trace, table_like=is_table_like_query(need))
                    for row in rows:
                        row["matched_needs"], row["retrieval_passes"] = [need], [pass_number]
                    error = None
                except Exception as exc:
                    logger.warning("librarian_v2_retrieval_failed run_id=%s pass=%s need=%r error=%s",
                                   run_id, pass_number, need, exc)
                    rows, error = [], str(exc)
                summary = {"pass": pass_number, "ontology_id": oid, "information_need": need,
                           "selected": len(rows), "error": error}
                trace.append({"step": "v2_information_need_retrieval", "data": summary})
                debug.write(f"retrieval_pass_{pass_number}_ontology_{oid}", input={"information_need": need},
                            output={**summary, "retrieval_trace": local_trace, "chunks": rows})
                return rows
        groups = await asyncio.gather(*(one(need, oid) for need in needs for oid in ontology_ids))
        rows = [row for group in groups for row in group]
        trace.append({"step": "v2_retrieval_pass", "data": {
            "pass": pass_number, "information_needs": needs, "retrieved": len(rows)}})
        return rows

    async def _synthesize(self, query: str, chunks: list[RetrievedChunk],
                          rpg_system: str, trace: list[dict[str, Any]],
                          debug: LibrarianDebugArtifacts) -> NeutralAnswer:
        excerpts = "".join(
            f"\n--- Source {i} (source_id={chunk.source_id}): {chunk.book_title or 'Book'}, "
            f"Page {chunk.display_page_label or chunk.page_number} ---\n"
            f"{'[INCOMPLETE EVIDENCE: do not infer a partial list.]' if chunk.incomplete_evidence else chunk.text}\n"
            for i, chunk in enumerate(chunks, 1))
        prompt = SIMPLIFIED_ANSWER_STYLE_PROMPT.format(
            query=query, rpg_system=rpg_system, chunks=excerpts)
        system = SYNTHESIS_SYSTEM_PROMPT.format(rpg_system=rpg_system)
        try:
            raw = str(await self._structured_chat(
                model=self.synthesis_model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                temperature=0.2,
                usage_tag="librarian_answer",
                name="librarian_cited_answer",
                schema={
                    "type": "object",
                    "additionalProperties": False,
                    **neutral_answer_schema(),
                },
            ))
            payload, repaired = await self._parse_or_repair(
                raw, '{"claims":[{"id":"claim-1","text":"Supported claim","citations":["source-1"]}],"uncertainty":null}'
            )
            try:
                answer = NeutralAnswer.model_validate(payload)
            except Exception:
                repaired_text = await repair_invalid_json(
                    llm_client=self.llm_client,
                    model=self.repair_json_model,
                    malformed_text=json.dumps(payload, ensure_ascii=False),
                    schema_hint=json.dumps(neutral_answer_schema(), ensure_ascii=False),
                    usage_tag="agents.json_repair",
                )
                answer = NeutralAnswer.model_validate_json(repaired_text)
                repaired = True
            available = {chunk.source_id for chunk in chunks if chunk.source_id}
            cited = {
                citation for claim in answer.claims for citation in claim.citations
            }
            if cited - available or (available and not cited):
                source_id = sorted(available)[0]
                answer = NeutralAnswer.model_validate({
                    "claims": [{
                        "id": "fallback-1",
                        "text": "Relevant material was found, but a complete grounded answer could not be produced.",
                        "citations": [source_id],
                    }],
                    "uncertainty": "Neutral synthesis citation validation failed.",
                })
        except Exception as exc:
            if isinstance(exc, httpx.TimeoutException) or "504" in str(exc):
                source_id = next((chunk.source_id for chunk in chunks if chunk.source_id), None)
                return NeutralAnswer.model_validate({
                    "claims": [{
                        "id": "fallback-1",
                        "text": "Relevant material was found, but a complete grounded answer could not be produced.",
                        "citations": [source_id] if source_id else [],
                    }],
                    "uncertainty": "Neutral synthesis timed out.",
                })
            raise
        trace.append({"step": "answer_with_style", "data": {
            "chunks_used": len(chunks),
            "json_repaired": locals().get("repaired", False),
            "citations_valid": True,
        }})
        debug.write("llm_synthesis", input={"messages": [system, prompt]}, output={"neutral_answer": answer})
        return answer

    async def _structured_chat(
        self, *, model: Any, messages: list[dict[str, str]], temperature: float,
        usage_tag: str, name: str, schema: dict[str, Any],
    ) -> str:
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "usage_tag": usage_tag,
        }
        try:
            return str(await self.llm_client.chat(
                **kwargs,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": name, "strict": True, "schema": schema},
                },
            ))
        except TypeError:
            return str(await self.llm_client.chat(**kwargs))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 400:
                raise
            return str(await self.llm_client.chat(**kwargs))

    @staticmethod
    def _select_synthesis_evidence(chunks: list[RetrievedChunk]) -> tuple[list[RetrievedChunk], int]:
        """Keep ranked evidence through one budget-crossing chunk for synthesis."""
        selected: list[RetrievedChunk] = []
        total_tokens = 0
        overflow_chunk_added = False
        for number, chunk in enumerate(chunks, 1):
            if overflow_chunk_added:
                break
            rendered = (
                f"\n--- Source {number} (source_id={chunk.source_id}): {chunk.book_title or 'Book'}, "
                f"Page {chunk.display_page_label or chunk.page_number} ---\n"
                f"{'[INCOMPLETE EVIDENCE: do not infer a partial list.]' if chunk.incomplete_evidence else chunk.text}\n"
            )
            total_tokens += estimate_tokens(rendered)
            selected.append(chunk)
            overflow_chunk_added = total_tokens > SYNTHESIS_EVIDENCE_TOKEN_BUDGET
        return selected, total_tokens

    @staticmethod
    def _merge(current: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for row in current + incoming:
            key = str(row.get("parent_chunk_id") or row.get("chunk_id") or f"{row.get('library_item_id')}:{row.get('chunk_index')}")
            if key not in merged:
                merged[key] = dict(row); continue
            old = merged[key]
            needs = list(dict.fromkeys(list(old.get("matched_needs") or []) + list(row.get("matched_needs") or [])))
            passes = sorted(set(list(old.get("retrieval_passes") or []) + list(row.get("retrieval_passes") or [])))
            if float(row.get("score", 0)) > float(old.get("score", 0)): merged[key] = dict(row)
            merged[key]["matched_needs"], merged[key]["retrieval_passes"] = needs, passes
        return list(merged.values())

    @staticmethod
    def _json(raw: str) -> dict[str, Any]:
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
        value = json.loads(text[text.find("{"):text.rfind("}") + 1])
        if not isinstance(value, dict): raise ValueError("expected object")
        return value

    async def _parse_or_repair(self, raw: str, schema_hint: str) -> tuple[dict[str, Any], bool]:
        """Parse model JSON, invoking the shared repair service only on failure."""
        try:
            return self._json(raw), False
        except Exception as parse_error:
            logger.warning("librarian_json_parse_failed repairing=true error=%s", parse_error)
            repaired_text = await repair_invalid_json(
                llm_client=self.llm_client,
                model=self.repair_json_model,
                malformed_text=raw,
                schema_hint=schema_hint,
                usage_tag="agents.json_repair",
            )
            repaired = self._json(repaired_text)
            return repaired, True

    @staticmethod
    def _clean(values: Any, limit: int) -> list[str]:
        if not isinstance(values, list): return []
        cleaned = [re.sub(r"\s+", " ", value).strip()[:500] for value in values
                   if isinstance(value, str) and 3 <= len(value.strip()) <= 1000]
        return list(dict.fromkeys(cleaned))[:limit]

    @staticmethod
    async def _active_items(db: AsyncSession, oid: int, requested: list[int] | None) -> list[int]:
        query = select(LibraryItem.id).where(LibraryItem.ontology_id == oid, LibraryItem.vectorized.is_(True))
        if requested: query = query.where(LibraryItem.id.in_(requested))
        return [int(row[0]) for row in (await db.execute(query)).all()]

    @staticmethod
    async def _metadata(db: AsyncSession, ids: set[int]) -> dict[int, dict[str, Any]]:
        if not ids: return {}
        items = (await db.execute(select(LibraryItem).where(LibraryItem.id.in_(ids)))).scalars().all()
        return {item.id: {"title": item.title, "authors": item.authors, "vectorized": bool(item.vectorized)} for item in items}

    @staticmethod
    def _chunks(rows: list[dict[str, Any]], metadata: dict[int, dict[str, Any]]) -> list[RetrievedChunk]:
        result = []
        for number, row in enumerate(rows, 1):
            item_id, item = int(row["library_item_id"]), metadata.get(int(row["library_item_id"]), {})
            if not item.get("vectorized"): continue
            page = int(row.get("page_number") or 1)
            result.append(RetrievedChunk(
                library_item_id=item_id, page_number=page, text=str(row.get("text") or ""), score=float(row.get("score") or 0),
                pdf_url=row.get("pdf_url"), page_url=row.get("page_url"), book_title=item.get("title"), book_authors=item.get("authors"),
                source_id=f"source-{number}", chunk_id=row.get("chunk_id"), parent_chunk_id=row.get("parent_chunk_id"),
                physical_page_numbers=row.get("physical_page_numbers") or [page], displayed_page_labels=row.get("displayed_page_labels") or [],
                display_page_label=row.get("display_page_label"), bounding_boxes=row.get("bounding_boxes") or [],
                matched_child_text=row.get("matched_child_text"), expansion_mode=row.get("expansion_mode"),
                incomplete_evidence=bool(row.get("incomplete_evidence", False))))
        return result

    @staticmethod
    def _rpg_systems(agent: Agent) -> str:
        systems = list(dict.fromkeys(filter(None, ((getattr(item, "rpg_system", None) or "").strip() for item in agent.ontologies))))
        return ", ".join(systems) if systems else "relevant"
