"""Librarian Query v2 evidence-loop orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config_store import LLMModelTarget
from app.integrations.llm.shreckllm_client import ShreckLLMClient
from app.jobs.librarian.citations import extract_sources, render_inline_citations
from app.jobs.librarian.debug_artifacts import LibrarianDebugArtifacts, debug_value
from app.jobs.librarian.prompts import (
    EVIDENCE_WARNING_PROMPT, MODEL_PREWARM_PROMPT, SIMPLIFIED_ANSWER_STYLE_PROMPT,
    SYNTHESIS_SYSTEM_PROMPT, planner_messages, validator_messages,
)
from app.jobs.librarian.retrieval_strategies import get_librarian_retrieval_strategy, is_table_like_query
from app.jobs.librarian.schemas import LibrarianQueryRequest, LibrarianQueryResponse, RetrievedChunk
from app.jobs.shrecknet import validate_or_repair_json
from app.models.agent import Agent
from app.models.library import LibraryItem

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EvidenceValidation:
    adequate: bool
    covered_needs: list[str] = field(default_factory=list)
    missing_needs: list[str] = field(default_factory=list)
    reason: str = ""
    failed: bool = False


class LibrarianQueryV2:
    """Plan, retrieve, validate, retry, and synthesize grounded book evidence."""

    def __init__(self, llm_client: ShreckLLMClient, answer_model: LLMModelTarget | str = "gpt-4o",
                 repair_json_model: LLMModelTarget | str | None = None,
                 debug_artifacts_enabled: bool = False):
        self.llm_client = llm_client
        self.answer_model = answer_model
        self.repair_json_model = repair_json_model or answer_model
        self.debug_artifacts_enabled = bool(debug_artifacts_enabled)
        self.retrieval = get_librarian_retrieval_strategy()
        self._prewarm_at = 0.0

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
        needs = await self._plan(request.query, rpg_system, debug)
        trace.append({"step": "v2_plan", "data": {"information_needs": needs}})
        active = {oid: await self._active_items(db_session, oid, request.library_item_ids) for oid in ontology_ids}
        evidence: list[dict[str, Any]] = []
        searched: set[str] = set()
        pending, passes, stop = needs, 0, "retry_limit"
        validation = EvidenceValidation(False, missing_needs=needs, reason="Not validated")
        for pass_number in range(3):
            novel = [need for need in pending if need.casefold() not in searched]
            if not novel:
                stop = "no_novel_missing_needs"; break
            searched.update(value.casefold() for value in novel)
            found = await self._retrieve_pass(novel, pass_number, ontology_ids, active, request,
                                              top_k, trace, debug, run_id)
            passes += 1
            before = len(evidence)
            evidence = self._merge(evidence, found)
            added = len(evidence) - before
            trace.append({"step": "v2_evidence_merge", "data": {
                "pass": pass_number, "retrieved": len(found), "added": added, "total": len(evidence)}})
            debug.write(f"evidence_merge_pass_{pass_number}", input={"previous_count": before, "chunks": found},
                        output={"added": added, "evidence": evidence})
            if pass_number and not added:
                stop = "no_new_evidence"; break
            validation = await self._validate(request.query, needs, evidence, pass_number, debug)
            trace.append({"step": "v2_evidence_validation", "data": {"pass": pass_number, **asdict(validation)}})
            if validation.adequate:
                stop = "adequate"; break
            if validation.failed:
                stop = "validation_failed"; break
            pending = validation.missing_needs
            if not pending:
                stop = "no_missing_needs"; break
            trace.append({"step": "v2_retry_decision", "data": {
                "pass": pass_number, "retry": pass_number < 2, "next_needs": pending}})

        evidence.sort(key=lambda row: float(row.get("score", 0)), reverse=True)
        evidence = evidence[:max(14, min(30, top_k * max(1, len(needs))))]
        metadata = await self._metadata(db_session, {int(row["library_item_id"]) for row in evidence})
        chunks = self._chunks(evidence, metadata)
        answer, sources = None, []
        if request.mode in ("nl", "both"):
            if not chunks:
                answer = "I couldn't find any relevant information in the available books to answer your question."
            else:
                raw = await self._synthesize(
                    request.query, chunks, agent.writing_style, rpg_system, trace, debug,
                    validation=validation,
                    warning=None if validation.adequate else validation.reason or stop,
                )
                sources = extract_sources(raw, chunks)
                answer = render_inline_citations(raw, chunks)
                trace.append({"step": "citation_rendering", "data": {"sources_used": len(sources)}})
                debug.write("citation_rendering", input={"raw_answer": raw}, output={"answer": answer})
        used = list({chunk.library_item_id for chunk in (sources or chunks)})
        response = LibrarianQueryResponse(
            agent_id=agent.id, mode=request.mode, query=request.query, subqueries=needs,
            answer=answer, chunks=chunks if request.mode in ("context", "both") else [],
            sources_used=sources, library_items_used=used,
            trace=trace if request.include_trace else None,
        )
        debug.write("final_response", input={"mode": request.mode}, output=response.model_dump())
        elapsed = (time.monotonic() - started) * 1000
        debug.write_manifest(run_id=run_id, strategy="v2", model=str(self.answer_model), status="success",
                             pass_count=passes, stop_reason=stop, elapsed_ms=elapsed)
        logger.info("librarian_v2_complete run_id=%s passes=%s stop=%s evidence=%s elapsed_ms=%.1f",
                    run_id, passes, stop, len(chunks), elapsed)
        return response

    async def _plan(self, query: str, rpg_system: str, debug: LibrarianDebugArtifacts) -> list[str]:
        messages, raw = planner_messages(query=query, rpg_system=rpg_system), ""
        try:
            raw = str(await self.llm_client.chat(model=self.answer_model, messages=messages,
                                                 temperature=0.0, usage_tag="librarian_plan"))
            parsed, repaired = await self._parse_or_repair(
                raw, '{"information_needs": ["standalone search question"]}'
            )
            needs = self._clean(parsed.get("information_needs"), 8)
            if not needs: raise ValueError("planner returned no valid information needs")
            fallback = False
        except Exception as exc:
            logger.warning("librarian_v2_planner_fallback error=%s", exc)
            needs, fallback = [query.strip()], True
        debug.write("v2_planner", input={"messages": messages},
                    output={"raw_response": raw, "information_needs": needs, "fallback": fallback,
                            "json_repaired": locals().get("repaired", False)})
        return needs

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

    async def _validate(self, query: str, needs: list[str], evidence: list[dict[str, Any]],
                        pass_number: int, debug: LibrarianDebugArtifacts) -> EvidenceValidation:
        excerpts = []
        for i, row in enumerate(evidence[:30], 1):
            compact = re.sub(r"\s+", " ", str(row.get("text") or ""))[:1800]
            excerpts.append(f"source-{i} | needs={row.get('matched_needs', [])}\n{compact}")
        messages = validator_messages(query=query, needs_json=json.dumps(needs, ensure_ascii=False),
                                      evidence="\n\n".join(excerpts) or "[none]")
        raw = ""
        try:
            raw = str(await self.llm_client.chat(model=self.answer_model, messages=messages,
                                                 temperature=0.0, usage_tag="librarian_evidence_validation"))
            value, repaired = await self._parse_or_repair(
                raw,
                '{"adequate": true, "covered_needs": ["..."], '
                '"missing_needs": ["standalone search question"], "reason": "..."}',
            )
            if not isinstance(value.get("adequate"), bool): raise ValueError("invalid adequate value")
            result = EvidenceValidation(value["adequate"], self._clean(value.get("covered_needs"), 16),
                                        self._clean(value.get("missing_needs"), 8), str(value.get("reason") or ""))
        except Exception as exc:
            result = EvidenceValidation(False, reason=f"Evidence validation failed: {exc}", failed=True)
        debug.write(f"evidence_validation_pass_{pass_number}", input={"messages": messages},
                    output={"raw_response": raw, "parsed": asdict(result),
                            "json_repaired": locals().get("repaired", False)})
        return result

    async def _synthesize(self, query: str, chunks: list[RetrievedChunk], writing_style: str | None,
                          rpg_system: str, trace: list[dict[str, Any]], debug: LibrarianDebugArtifacts,
                          validation: EvidenceValidation, warning: str | None) -> str:
        excerpts = "".join(
            f"\n--- Source {i} (source_id={chunk.source_id}): {chunk.book_title or 'Book'}, "
            f"Page {chunk.display_page_label or chunk.page_number} ---\n"
            f"{'[INCOMPLETE EVIDENCE: do not infer a partial list.]' if chunk.incomplete_evidence else chunk.text}\n"
            for i, chunk in enumerate(chunks, 1))
        prompt = SIMPLIFIED_ANSWER_STYLE_PROMPT.format(
            query=query, rpg_system=rpg_system, chunks=excerpts,
            validation_result=json.dumps(asdict(validation), ensure_ascii=False, indent=2),
            writing_style=writing_style or "Use a clear, direct tone suitable for game masters.")
        if warning: prompt += EVIDENCE_WARNING_PROMPT.format(warning=warning)
        system = SYNTHESIS_SYSTEM_PROMPT.format(rpg_system=rpg_system)
        await self._prewarm()
        try:
            answer = str(await self.llm_client.chat(model=self.answer_model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                temperature=0.2, usage_tag="librarian_answer"))
        except Exception as exc:
            if isinstance(exc, httpx.TimeoutException) or "504" in str(exc):
                return "I found relevant excerpts, but answer generation timed out. Please try again."
            raise
        trace.append({"step": "answer_with_style", "data": {"chunks_used": len(chunks)}})
        debug.write("llm_synthesis", input={"messages": [system, prompt]}, output={"raw_answer": answer})
        return answer

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
            repaired = await validate_or_repair_json(
                llm_client=self.llm_client,
                model=self.repair_json_model,
                raw_text=raw,
                schema_hint=schema_hint,
                usage_tag="agents.json_repair",
            )
            if not isinstance(repaired, dict):
                raise ValueError("JSON repair did not return an object")
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

    async def _prewarm(self) -> None:
        if not isinstance(self.answer_model, LLMModelTarget) or self.answer_model.provider != "ollama": return
        if time.monotonic() - self._prewarm_at < 300: return
        try:
            await asyncio.wait_for(self.llm_client.chat(model=self.answer_model,
                messages=[{"role": "user", "content": MODEL_PREWARM_PROMPT}], temperature=0.0,
                usage_tag="librarian_model_prewarm"), timeout=8)
            self._prewarm_at = time.monotonic()
        except Exception as exc:
            logger.warning("librarian_model_prewarm_failed error=%s", exc)
