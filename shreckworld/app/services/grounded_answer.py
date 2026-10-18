"""Validate and render evidence-grounded ShreckWorld query answers."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.config import get_settings
from app.integrations.shreckllm import ShreckLLMUnavailable, generate_grounded_answer
from app.schemas.shreckworld import GroundedClaim, GroundedConflict, ShreckWorldCitation
from app.schemas.shreckworld import ShreckWorldModelTarget


class GroundedAnswerError(RuntimeError):
    """The requested synthesis could not safely be produced."""


class GroundedAnswerUnavailable(GroundedAnswerError):
    """The configured ShreckLLM service could not complete synthesis."""


class GroundedAnswerInvalid(GroundedAnswerError):
    """The model returned invalid structure or unsupported citation references."""


class EvidenceBudgetExceeded(GroundedAnswerError):
    """No complete evidence passage fits the configured synthesis budget."""


class _GroundedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["complete", "partial", "unsupported"]
    claims: list[GroundedClaim]
    missing_information: list[str]
    conflicts: list[GroundedConflict]


def synthesize_grounded_answer(
    *, query: str, citations: list[ShreckWorldCitation],
    model_target: ShreckWorldModelTarget,
) -> tuple[str, Literal["complete", "partial", "unsupported"], list[GroundedClaim],
           list[str], list[GroundedConflict], int]:
    evidence, supplied_ids = _evidence_package(citations)
    if not evidence:
        raise EvidenceBudgetExceeded(
            "No complete evidence passage fits the configured synthesis budget"
        )
    try:
        raw = generate_grounded_answer(
            query=query, evidence=evidence, model_target=model_target
        )
    except ShreckLLMUnavailable as exc:
        raise GroundedAnswerUnavailable(str(exc)) from exc
    try:
        result = _GroundedAnswer.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise GroundedAnswerInvalid("ShreckLLM returned invalid grounded-answer structure") from exc
    _validate_citations(result, supplied_ids)
    answer = _render_answer(result)
    omitted_citations = len(citations) - len(supplied_ids)
    return answer, result.status, result.claims, result.missing_information, result.conflicts, omitted_citations


def _evidence_package(
    citations: list[ShreckWorldCitation],
) -> tuple[list[dict[str, Any]], set[str]]:
    budget_chars = get_settings().synthesis_evidence_token_budget * 4
    evidence: list[dict[str, Any]] = []
    supplied_ids: set[str] = set()
    used_chars = 0
    for citation in citations:
        item = {
            "citation_id": citation.source_id,
            "source": citation.title,
            "library_item_id": citation.library_item_id,
            "source_revision": citation.source_sha256,
            "authority_tier": citation.authority_tier,
            "authority_priority": citation.authority_priority,
            "heading_path": citation.heading_path,
            "page_numbers": citation.page_numbers or [citation.page_number],
            "text": citation.evidence_text or citation.excerpt,
        }
        item_chars = len(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
        if used_chars + item_chars > budget_chars:
            continue
        evidence.append(item)
        supplied_ids.add(citation.source_id)
        used_chars += item_chars
    return evidence, supplied_ids


def _validate_citations(result: _GroundedAnswer, supplied_ids: set[str]) -> None:
    if result.status == "unsupported":
        if result.claims:
            raise GroundedAnswerInvalid("Unsupported answers must not contain factual claims")
    elif not result.claims:
        raise GroundedAnswerInvalid("Supported answers must contain at least one cited claim")

    for claim in result.claims:
        if not claim.text.strip() or not set(claim.citation_ids) <= supplied_ids:
            raise GroundedAnswerInvalid("A claim is empty or cites evidence not supplied to synthesis")
    for conflict in result.conflicts:
        if not conflict.description.strip() or not set(conflict.citation_ids) <= supplied_ids:
            raise GroundedAnswerInvalid("A conflict is empty or cites evidence not supplied to synthesis")
    if any(not item.strip() for item in result.missing_information):
        raise GroundedAnswerInvalid("Missing-information entries must not be empty")


def _render_answer(result: _GroundedAnswer) -> str:
    lines = [
        f"{claim.text} [{', '.join(claim.citation_ids)}]"
        for claim in result.claims
    ]
    lines.extend(
        f"Source conflict: {conflict.description} [{', '.join(conflict.citation_ids)}]"
        for conflict in result.conflicts
    )
    if result.missing_information:
        lines.append("Not established by the available sources: " + "; ".join(result.missing_information))
    if not lines:
        lines.append("The available sources do not establish an answer to this question.")
    return "\n\n".join(lines)
