"""ShreckWorld-owned planned retrieval and evidence-coverage orchestration."""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.shreckllm import ShreckLLMUnavailable, generate_shreckworld_structured_output
from app.jobs.shreckworld_agent.prompts import (
    COVERAGE_REVIEW_SCHEMA, COVERAGE_REVIEW_SYSTEM_PROMPT, QUERY_PLAN_SCHEMA, QUERY_PLANNER_SYSTEM_PROMPT,
)
from app.jobs.shreckworld_agent.interpretation import interpret_query
from app.models import ShreckWorld, ShreckWorldLibraryItem
from app.schemas.query_v3 import EvidenceCoverage, EvidenceGroup, NeedCoverage, QueryNeed, QueryPlan, QueryV3Trace
from app.schemas.shreckworld import (
    QuerySourceCoverage, ShreckWorldModelTarget, ShreckWorldQueryRequest, ShreckWorldQueryResponse,
)
from app.services.model_settings import get_model_shreckworld_synthesizer
from app.services.query import RetrievalScope, hydrate_candidate, resolve_retrieval_scope, search_candidates
from app.services.grounded_answer import (
    EvidenceBudgetExceeded, GroundedAnswerInvalid, GroundedAnswerUnavailable,
    evidence_item, synthesize_grounded_answer,
)

logger = logging.getLogger(__name__)
_MAX_CANDIDATES_PER_NEED = 40
_MAX_PARENTS_PER_NEED = 3
_MAX_EVIDENCE_GROUPS = 12
_REFINEMENT_STOPWORDS = {
    "a", "an", "and", "are", "as", "be", "by", "does", "from", "how", "in", "is",
    "it", "of", "on", "or", "the", "to", "what", "when", "which", "who", "with",
}


class QueryScopeError(ValueError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _ReviewedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    quote: str


class _ReviewedNeed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    need_id: str
    supported_requirements: list[str]
    missing_requirements: list[str]
    evidence: list[_ReviewedEvidence]
    explanation: str


class _CoverageReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    needs: list[_ReviewedNeed] = Field(min_length=1, max_length=6)


def validate_query_scope(session: Session, *, shreckworld_id: str,
                         payload: ShreckWorldQueryRequest) -> ShreckWorld:
    world = session.get(ShreckWorld, shreckworld_id)
    if world is None:
        raise QueryScopeError(404, "ShreckWorld not found")
    if payload.library_item_id is not None:
        item = session.get(ShreckWorldLibraryItem, payload.library_item_id)
        if item is None or item.shreckworld_id != shreckworld_id:
            raise QueryScopeError(404, "ShreckWorld library item not found")
    if payload.library_item_ids is not None:
        for item_id in payload.library_item_ids:
            item = session.get(ShreckWorldLibraryItem, item_id)
            if item is None or item.shreckworld_id != shreckworld_id:
                raise QueryScopeError(404, "ShreckWorld library item not found")
    if payload.selection is not None:
        selected = session.get(ShreckWorldLibraryItem, payload.selection.library_item_id)
        if selected is None or selected.shreckworld_id != shreckworld_id:
            raise QueryScopeError(404, "Selected ShreckWorld source not found")
        if selected.source_sha256 != payload.selection.source_sha256:
            raise QueryScopeError(409, "The selected document has changed. Reopen it before asking.")
        if payload.library_item_id and payload.selection.library_item_id != payload.library_item_id:
            raise QueryScopeError(422, "Selection must belong to the requested book")
        if payload.library_item_ids is not None and payload.selection.library_item_id not in payload.library_item_ids:
            raise QueryScopeError(422, "Selection must belong to one of the requested books")
    return world


def _deterministic_plan(query: str) -> QueryPlan:
    normalized = query.casefold()
    if re.search(r"\b(?:recover|recovery|restore|restoring)\s+(?:sanity|san|sanity points?)\b", normalized):
        return QueryPlan(
            intent="multi_rule",
            needs=[
                QueryNeed(
                    id="need-1", question="How are numerical Sanity (SAN) points restored?",
                    kind="mechanic", required=True,
                    coverage_requirement="The explicit methods and limits for restoring numerical SAN points.",
                    priority=1,
                ),
                QueryNeed(
                    id="need-2", question="How does a character recover from insanity?",
                    kind="procedure", required=True,
                    coverage_requirement="The conditions, procedures, and timing for recovering from insanity.",
                    priority=2,
                ),
            ],
        )
    if re.search(r"\bwhat\s+is\b", normalized) and len(normalized.split()) <= 12:
        intent = "simple"
        kind = "definition"
        requirement = "A direct definition of the named concept."
    else:
        intent = "simple"
        kind = "other"
        requirement = "Direct evidence that answers the user's question."
    return QueryPlan(
        intent=intent,
        needs=[QueryNeed(
            id="need-1", question=query, kind=kind, required=True,
            coverage_requirement=requirement, priority=1,
        )],
    )


def _requires_planning(query: str) -> bool:
    normalized = query.casefold()
    if re.search(r"\b(?:recover|recovery|restore|restoring)\s+(?:sanity|san|sanity points?)\b", normalized):
        return False
    return bool(re.search(
        r"\b(?:compare|comparison|versus|difference|both|combine|combining|together|"
        r"all (?:the )?ways|every way|all of|every|exhaustive|multiple|process|steps|"
        r"conditions and|duration and|checks and|as well as|and|or)\b",
        normalized,
    )) or len(normalized.split()) > 24


def _source_metadata(session: Session, *, shreckworld_id: str,
                     request: ShreckWorldQueryRequest) -> list[dict[str, str]]:
    statement = select(ShreckWorldLibraryItem).where(ShreckWorldLibraryItem.shreckworld_id == shreckworld_id)
    if request.library_item_id is not None:
        statement = statement.where(ShreckWorldLibraryItem.id == request.library_item_id)
    elif request.library_item_ids is not None:
        statement = statement.where(ShreckWorldLibraryItem.id.in_(request.library_item_ids))
    items = session.scalars(statement.order_by(ShreckWorldLibraryItem.authority_priority, ShreckWorldLibraryItem.title))
    return [{"title": item.title, "authors": item.authors or ""} for item in items]


def _plan_query(session: Session, *, world: ShreckWorld, request: ShreckWorldQueryRequest,
                trace: QueryV3Trace, model_target: ShreckWorldModelTarget) -> tuple[QueryPlan, bool]:
    fallback = _deterministic_plan(request.query)
    if not _requires_planning(request.query) or not get_settings().query_v3_planning_enabled:
        return fallback, False
    user_data: dict[str, Any] = {
        "question": request.query,
        "world_name": world.name,
        "sources": _source_metadata(session, shreckworld_id=world.id, request=request),
        "selected_passage": None,
    }
    if request.selection is not None:
        selected_item = session.get(ShreckWorldLibraryItem, request.selection.library_item_id)
        user_data["selected_passage"] = {
            "source_title": selected_item.title if selected_item else "",
            "page_number": request.selection.page_number,
            "text": request.selection.text,
        }
    trace.planner_llm_calls = 1
    try:
        raw = generate_shreckworld_structured_output(
            usage_tag="shreckworld.query_v3.planner",
            system_prompt=QUERY_PLANNER_SYSTEM_PROMPT,
            user_content=json.dumps(user_data, ensure_ascii=False),
            schema_name="shreckworld_query_plan",
            response_schema=QUERY_PLAN_SCHEMA,
            max_tokens=1500,
            model_target=model_target,
        )
        parsed = json.loads(raw)
        plan = QueryPlan.model_validate(parsed)
        expected_ids = [f"need-{index}" for index in range(1, len(plan.needs) + 1)]
        if [need.id for need in plan.needs] != expected_ids:
            raise ValueError("Planner need IDs must be sequential and stable")
        if [need.priority for need in plan.needs] != list(range(1, len(plan.needs) + 1)):
            raise ValueError("Planner need priorities must be sequential")
        return plan, False
    except (ShreckLLMUnavailable, json.JSONDecodeError, ValidationError, ValueError) as exc:
        logger.warning("ShreckWorld query planner fell back to a deterministic one-need plan: %s", exc)
        trace.planner_fallback = True
        return fallback, True


def _group_key(scope: RetrievalScope, candidate: dict[str, Any]) -> tuple[str, str]:
    chunk, _ = scope.by_id[candidate["chunk_id"]]
    return chunk.document_id or "", chunk.parent_chunk_id or chunk.id


def _targeted_query(request: ShreckWorldQueryRequest, need: QueryNeed, scope: RetrievalScope,
                    groups: dict[tuple[str, str], dict[str, Any]]) -> str:
    original_terms = set(re.findall(r"[\w]+", request.query.casefold()))
    allowed_text = [request.query]
    if request.selection is not None:
        allowed_text.append(request.selection.text)
    for group in groups.values():
        chunk, item = scope.by_id[group["candidate"]["chunk_id"]]
        allowed_text.extend((chunk.text, item.title, item.authors or ""))
    allowed_terms = set(re.findall(r"[\w]+", " ".join(allowed_text).casefold()))
    proposed_terms = re.findall(
        r"[\w]+", f"{need.question} {need.coverage_requirement}".casefold(),
    )
    refinements = list(dict.fromkeys(
        term for term in proposed_terms
        if term not in original_terms and term not in _REFINEMENT_STOPWORDS and term in allowed_terms
    ))
    return request.query + ((" " + " ".join(refinements)) if refinements else "")


def _merge_candidates(
    scope: RetrievalScope,
    plan: QueryPlan,
    by_need: dict[str, list[dict[str, Any]]],
    *,
    existing: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    groups = existing if existing is not None else {}
    included_per_need = {
        need.id: sum(need.id in group["need_ranks"] for group in groups.values())
        for need in plan.needs
    }
    for need in plan.needs:
        for rank, candidate in enumerate(by_need.get(need.id, [])):
            key = _group_key(scope, candidate)
            group = groups.get(key)
            if group is None:
                group = {"key": key, "candidate": candidate, "need_ranks": {}}
                groups[key] = group
            group["need_ranks"].setdefault(need.id, rank + included_per_need[need.id])
    return groups


def _ordered_groups(plan: QueryPlan, groups: dict[tuple[str, str], dict[str, Any]],
                    *, max_groups: int) -> list[dict[str, Any]]:
    ordered: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for rank in range(_MAX_PARENTS_PER_NEED * 2):
        for need in sorted(plan.needs, key=lambda item: item.priority):
            for key, group in groups.items():
                if key not in seen and group["need_ranks"].get(need.id) == rank:
                    ordered.append(group)
                    seen.add(key)
                    if len(ordered) >= max_groups:
                        return ordered
    for key, group in groups.items():
        if key not in seen:
            ordered.append(group)
            if len(ordered) >= max_groups:
                break
    return ordered


def _need_ids(group: dict[str, Any]) -> list[str]:
    return sorted(group["need_ranks"], key=lambda need_id: group["need_ranks"][need_id])


def _is_interaction_question(question: str) -> bool:
    return bool(re.search(
        r"\b(?:combine|combining|together|both|simultaneous|compatible|interaction)\b",
        question.casefold(),
    ))


def _hydrate_groups(
    scope: RetrievalScope, plan: QueryPlan, groups: dict[tuple[str, str], dict[str, Any]],
    *, citations: list, evidence_groups: list[EvidenceGroup], remaining_characters: int,
    max_groups: int,
) -> int:
    existing_keys = {
        (
            scope.by_id[group.chunk_ids[0]][0].document_id or "",
            scope.by_id[group.chunk_ids[0]][0].parent_chunk_id or group.chunk_ids[0],
        )
        for group in evidence_groups if group.chunk_ids
    }
    for evidence_group in evidence_groups:
        if not evidence_group.chunk_ids:
            continue
        chunk_id = evidence_group.chunk_ids[0]
        chunk = scope.by_id[chunk_id][0]
        key = (chunk.document_id or "", chunk.parent_chunk_id or chunk.id)
        if key in groups:
            evidence_group.need_ids = _need_ids(groups[key])
    for group in _ordered_groups(plan, groups, max_groups=max_groups):
        key = group["key"]
        if key in existing_keys:
            continue
        citation = hydrate_candidate(scope, group["candidate"], number=len(citations) + 1, complete_evidence=True)
        evidence = citation.evidence_text or ""
        estimated_tokens = (len(evidence) + 3) // 4
        need_ids = _need_ids(group)
        if estimated_tokens > remaining_characters // 4:
            evidence_groups.append(EvidenceGroup(
                evidence_id=f"omitted-{citation.chunk_id}", need_ids=need_ids,
                chunk_ids=[citation.chunk_id], incomplete_evidence=True,
                omission_reason="The complete logical evidence unit exceeds the remaining evidence budget.",
            ))
            continue
        citations.append(citation)
        evidence_groups.append(EvidenceGroup(
            evidence_id=citation.source_id, need_ids=need_ids, chunk_ids=[citation.chunk_id],
        ))
        remaining_characters -= len(evidence)
    return remaining_characters


def _review_evidence(plan: QueryPlan, citations: list,
                     evidence_groups: list[EvidenceGroup],
                     model_target: ShreckWorldModelTarget) -> dict[str, _ReviewedNeed] | None:
    settings = get_settings()
    if not settings.query_v3_coverage_review_enabled or not citations:
        return None
    needs = [{
        "need_id": need.id, "question": need.question,
        "coverage_requirement": need.coverage_requirement,
    } for need in plan.needs]
    evidence = [{
        "source_id": citation.source_id, "title": citation.title, "page_number": citation.page_number,
        "heading_path": citation.heading_path, "text": citation.evidence_text or "",
    } for citation in citations if citation.evidence_text]
    if not evidence:
        return None
    raw = generate_shreckworld_structured_output(
        usage_tag="shreckworld.query_v3.coverage_review",
        system_prompt=COVERAGE_REVIEW_SYSTEM_PROMPT,
        user_content=json.dumps({"needs": needs, "evidence": evidence}, ensure_ascii=False),
        schema_name="shreckworld_evidence_coverage",
        response_schema=COVERAGE_REVIEW_SCHEMA,
        max_tokens=2200,
        model_target=model_target,
    )
    parsed = _CoverageReview.model_validate_json(raw)
    by_need = {item.need_id: item for item in parsed.needs}
    if len(by_need) != len(parsed.needs) or set(by_need) != {need.id for need in plan.needs}:
        raise ValueError("Coverage reviewer must return each planned need exactly once")
    citation_text = {citation.source_id: citation.evidence_text or "" for citation in citations}
    citation_needs = {group.evidence_id: set(group.need_ids) for group in evidence_groups}
    for review in parsed.needs:
        for quote in review.evidence:
            if quote.source_id not in citation_text or not quote.quote or quote.quote not in citation_text[quote.source_id]:
                raise ValueError("Coverage reviewer returned an unknown citation or non-verbatim quote")
            if review.need_id not in citation_needs.get(quote.source_id, set()):
                raise ValueError("Coverage reviewer associated evidence with an unrelated planned need")
        if review.supported_requirements and not review.evidence:
            raise ValueError("Coverage reviewer reported support without a source quotation")
    return by_need


def _fallback_coverage(plan: QueryPlan, evidence_groups: list[EvidenceGroup]) -> EvidenceCoverage:
    citation_needs = {
        need_id for group in evidence_groups if not group.incomplete_evidence for need_id in group.need_ids
    }
    unresolved: list[str] = []
    statuses = []
    for need in plan.needs:
        found = need.id in citation_needs
        ambiguous = _is_interaction_question(need.question) or bool(re.search(
            r"\b(?:all ways|every|exhaustive|forbidden|prohibit)\b", need.question.casefold(),
        ))
        status = "unresolved" if found or ambiguous or plan.requires_exhaustive_coverage else "missing"
        if status in {"unresolved", "missing"}:
            unresolved.append(f"{need.question}: support was not verified from retrieved source text.")
        statuses.append(NeedCoverage(
            need_id=need.id, status=status, evidence_ids=[
                group.evidence_id for group in evidence_groups if need.id in group.need_ids and not group.incomplete_evidence
            ],
            supported_requirements=[],
            missing_requirements=[need.coverage_requirement],
            assessment_method="unverified",
            explanation="No structured source-support assessment was available; retrieval relevance alone is not proof.",
        ))
    return EvidenceCoverage(status="unresolved" if any(item.status == "unresolved" for item in statuses) else "partial",
                            needs=statuses, unresolved_questions=unresolved)


def _make_coverage(plan: QueryPlan, evidence_groups: list[EvidenceGroup],
                   reviews: dict[str, _ReviewedNeed] | None, *, planner_fallback: bool) -> EvidenceCoverage:
    if reviews is None:
        return _fallback_coverage(plan, evidence_groups)
    result: list[NeedCoverage] = []
    unresolved: list[str] = []
    for need in plan.needs:
        review = reviews[need.id]
        citation_ids = list(dict.fromkeys(item.source_id for item in review.evidence))
        if review.supported_requirements and not review.missing_requirements:
            status = "covered"
        elif review.supported_requirements:
            status = "partial"
        elif _is_interaction_question(need.question) or re.search(
            r"\b(?:all ways|every|exhaustive|forbidden|prohibit)\b", need.question.casefold(),
        ):
            status = "unresolved"
        else:
            status = "missing"
        if _is_interaction_question(need.question):
            interaction_supported = any(
                re.search(r"\b(?:combin\w*|together|simultaneous\w*|compatib\w*|interaction\w*)\b", value.casefold())
                for value in review.supported_requirements
            )
            if not interaction_supported:
                status = "unresolved"
        if plan.requires_exhaustive_coverage and status == "covered":
            status = "partial"
            if not review.missing_requirements:
                review.missing_requirements.append("Exhaustive completeness is not established by retrieved passages.")
        if planner_fallback and status == "covered":
            status = "partial"
            review.missing_requirements.append(
                "The deterministic planner fallback cannot establish full complex-query coverage."
            )
        if status in {"missing", "unresolved"}:
            unresolved.append(f"{need.question}: {review.explanation}")
        if status == "partial":
            unresolved.append(f"{need.question}: {', '.join(review.missing_requirements) or review.explanation}")
        result.append(NeedCoverage(
            need_id=need.id, status=status, evidence_ids=citation_ids,
            supported_requirements=review.supported_requirements,
            missing_requirements=review.missing_requirements,
            assessment_method="verified_model", explanation=review.explanation,
        ))
    if planner_fallback and all(item.status == "covered" for item in result):
        unresolved.append("The complex query planner failed; a deterministic fallback plan cannot establish full coverage.")
    if result and all(item.status == "covered" for item in result) and not unresolved:
        coverage_status = "adequate"
    elif any(item.status == "unresolved" for item in result) or planner_fallback:
        coverage_status = "unresolved"
    else:
        coverage_status = "partial"
    return EvidenceCoverage(status=coverage_status, needs=result, unresolved_questions=unresolved)


def run_shreckworld_agent(
    session: Session, *, shreckworld_id: str, request: ShreckWorldQueryRequest,
    on_stage: Callable[[str], None] | None = None,
) -> ShreckWorldQueryResponse:
    """Execute one planned evidence pipeline, optionally followed by grounded synthesis."""
    world = validate_query_scope(session, shreckworld_id=shreckworld_id, payload=request)
    def stage(name: str) -> None:
        if on_stage is not None:
            on_stage(name)

    trace = QueryV3Trace()
    model_target = get_model_shreckworld_synthesizer(session)
    started = time.perf_counter()
    stage("interpreting")
    scope = resolve_retrieval_scope(
        session, shreckworld_id=shreckworld_id,
        library_item_id=request.library_item_id, library_item_ids=request.library_item_ids,
    )
    interpretation = interpret_query(request.query, scope)
    interpreted_request = request.model_copy(update={"query": interpretation.interpreted_query})
    stage("planning")
    planning_started = time.perf_counter()
    plan, planner_fallback = _plan_query(
        session, world=world, request=interpreted_request, trace=trace, model_target=model_target,
    )
    trace.planning_ms = round((time.perf_counter() - planning_started) * 1000, 2)

    stage("retrieving")
    retrieval_started = time.perf_counter()
    initial_by_need: dict[str, list[dict[str, Any]]] = {}
    fallback_flags: list[dict[str, int | bool]] = []
    for need in plan.needs:
        retrieval_query = need.question
        if request.selection is not None:
            retrieval_query += " " + request.selection.text
        candidates, fallback = search_candidates(scope, retrieval_query, limit=_MAX_CANDIDATES_PER_NEED)
        candidates = candidates[:_MAX_CANDIDATES_PER_NEED]
        trace.retrieved_candidates += len(candidates)
        fallback_flags.append(fallback)
        initial_by_need[need.id] = candidates[:_MAX_PARENTS_PER_NEED]
    groups = _merge_candidates(scope, plan, initial_by_need)
    trace.deduplicated_evidence = len(groups)
    budget_characters = get_settings().query_v3_evidence_token_budget * 4
    citations = []
    evidence_groups: list[EvidenceGroup] = []
    max_groups = min(_MAX_EVIDENCE_GROUPS, request.top_k)
    remaining = _hydrate_groups(
        scope, plan, groups, citations=citations, evidence_groups=evidence_groups,
        remaining_characters=budget_characters, max_groups=max_groups,
    )
    trace.evidence_tokens = (budget_characters - remaining + 3) // 4
    trace.qdrant_fallback = any(item["qdrant_fallback"] for item in fallback_flags)
    trace.reranker_fallback = any(item["reranker_fallback"] for item in fallback_flags)
    trace.retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 2)

    stage("assessing_coverage")
    coverage_started = time.perf_counter()
    initial_reviews: dict[str, _ReviewedNeed] | None = None
    if get_settings().query_v3_coverage_review_enabled and citations:
        trace.coverage_llm_calls += 1
        try:
            initial_reviews = _review_evidence(plan, citations, evidence_groups, model_target)
        except (ShreckLLMUnavailable, json.JSONDecodeError, ValidationError, ValueError) as exc:
            logger.warning("ShreckWorld initial evidence coverage review was unavailable or invalid: %s", exc)
    preliminary = _make_coverage(
        plan, evidence_groups, initial_reviews, planner_fallback=planner_fallback,
    )
    followup_needs = [
        item.need_id for item in preliminary.needs
        if item.status in {"missing", "partial", "unresolved"}
    ]
    trace.coverage_ms = round((time.perf_counter() - coverage_started) * 1000, 2)

    if followup_needs:
        stage("targeted_followup")
        followup_started = time.perf_counter()
        trace.followup_rounds = 1
        followup_by_need: dict[str, list[dict[str, Any]]] = {}
        need_lookup = {need.id: need for need in plan.needs}
        for need_id in followup_needs:
            need = need_lookup[need_id]
            targeted_query = _targeted_query(interpreted_request, need, scope, groups)
            candidates, fallback = search_candidates(scope, targeted_query, limit=_MAX_CANDIDATES_PER_NEED)
            candidates = candidates[:_MAX_CANDIDATES_PER_NEED]
            already_associated = {
                key for key, group in groups.items() if need_id in group["need_ranks"]
            }
            selected_candidates = []
            for candidate in candidates:
                if _group_key(scope, candidate) in already_associated:
                    continue
                selected_candidates.append(candidate)
                if len(selected_candidates) >= _MAX_PARENTS_PER_NEED:
                    break
            fallback_flags.append(fallback)
            followup_by_need[need_id] = selected_candidates
        groups = _merge_candidates(scope, plan, followup_by_need, existing=groups)
        before = len(citations)
        remaining = _hydrate_groups(
            scope, plan, groups, citations=citations, evidence_groups=evidence_groups,
            remaining_characters=remaining, max_groups=max_groups,
        )
        trace.evidence_tokens = (budget_characters - remaining + 3) // 4
        trace.deduplicated_evidence = len(groups)
        trace.retrieval_ms += round((time.perf_counter() - followup_started) * 1000, 2)
        if len(citations) > before and get_settings().query_v3_coverage_review_enabled:
            stage("reassessing_coverage")
            reassessment_started = time.perf_counter()
            trace.coverage_llm_calls += 1
            try:
                initial_reviews = _review_evidence(plan, citations, evidence_groups, model_target)
            except (ShreckLLMUnavailable, json.JSONDecodeError, ValidationError, ValueError) as exc:
                logger.warning("ShreckWorld final evidence coverage review was unavailable or invalid: %s", exc)
                initial_reviews = None
            trace.coverage_ms += round((time.perf_counter() - reassessment_started) * 1000, 2)

    coverage = _make_coverage(plan, evidence_groups, initial_reviews, planner_fallback=planner_fallback)
    source_trace = scope.trace
    unavailable_sources = (
        int(source_trace["sources_without_ready_index"])
        + int(source_trace["sources_requiring_reindex"])
    )
    scope_sensitive = (
        plan.requires_exhaustive_coverage
        or plan.intent == "comparison"
        or len([need for need in plan.needs if need.required]) > 1
    )
    if unavailable_sources and scope_sensitive:
        coverage.unresolved_questions.append(
            f"{unavailable_sources} requested source(s) were not searched because their indexes are unavailable or incompatible."
        )
        if coverage.status == "adequate":
            coverage.status = "partial"
    source_warnings = []
    if int(source_trace["sources_without_ready_index"]):
        count = int(source_trace["sources_without_ready_index"])
        source_warnings.append({
            "code": "source_not_indexed", "count": count,
            "message": f"{count} requested source(s) have not been indexed; results use available indexed sources.",
        })
    if int(source_trace["sources_requiring_reindex"]):
        count = int(source_trace["sources_requiring_reindex"])
        source_warnings.append({
            "code": "source_requires_reindex", "count": count,
            "message": f"{count} requested source(s) need reindexing; results use compatible active sources.",
        })
    source_trace["returned_citations"] = len(citations)
    source_trace["retrieval_ms"] = trace.retrieval_ms
    source_trace["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    answer = (
        f"Retrieved {len(citations)} citation-ready evidence unit(s). No answer synthesis was run."
        if citations else
        "No citation-ready evidence was retrieved. No answer synthesis was run; missing results do not prove that a rule is absent."
    )
    answer_status = None
    answer_content = None
    claims = []
    missing_information: list[str] = []
    conflicts = []
    evidence = [evidence_item(citation) for citation in citations]
    if request.mode == "answer":
        if citations:
            stage("synthesizing")
            synthesis_started = time.perf_counter()
            try:
                (
                    answer, answer_status, claims, missing_information, conflicts, _omitted,
                    answer_content, evidence,
                ) = synthesize_grounded_answer(
                    query=interpretation.interpreted_query,
                    citations=citations,
                    model_target=model_target,
                    session=session,
                    response_detail=request.response_detail or "concise",
                )
            except EvidenceBudgetExceeded as exc:
                raise QueryScopeError(422, str(exc)) from exc
            except GroundedAnswerUnavailable as exc:
                raise QueryScopeError(503, "Grounded answer synthesis is unavailable") from exc
            except GroundedAnswerInvalid as exc:
                logger.warning(
                    "ShreckWorld agent grounded answer rejected (reason=%s)", exc.reason,
                )
                raise QueryScopeError(
                    502,
                    "Grounded answer synthesis returned invalid structured output or evidence references",
                ) from exc
            trace.synthesis_ms = round((time.perf_counter() - synthesis_started) * 1000, 2)
            if unavailable_sources and scope_sensitive and answer_status == "complete":
                answer_status = "partial"
        else:
            answer_status = "unsupported"
            missing_information = ["No citation-ready evidence was retrieved for this query."]
            answer = "The available indexed sources do not establish an answer to this question."
            from app.schemas.shreckworld import AnswerPresentation
            answer_content = AnswerPresentation(
                direct_answer=answer,
                follow_up_hint="Try another indexed source or rephrase the question.",
            )
    source_trace["synthesis_ms"] = trace.synthesis_ms
    source_trace["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    stage("completed")
    return ShreckWorldQueryResponse(
        shreckworld_id=shreckworld_id, query=request.query, answer=answer, citations=citations,
        trace=source_trace if request.include_trace else None, mode=request.mode,
        answer_status=answer_status, answer_content=answer_content, claims=claims,
        evidence=evidence, missing_information=missing_information, conflicts=conflicts,
        source_warnings=source_warnings, query_interpretation=interpretation,
        source_coverage=QuerySourceCoverage(
            requested_source_count=int(source_trace["requested_sources"]),
            searched_source_count=int(source_trace["sources_searched"]),
            sources_without_ready_index=int(source_trace["sources_without_ready_index"]),
            sources_requiring_reindex=int(source_trace["sources_requiring_reindex"]),
        ),
        retrieval_strategy="planned", query_plan=plan, evidence_coverage=coverage,
        evidence_groups=evidence_groups, v3_trace=trace if request.include_trace else None,
    )
