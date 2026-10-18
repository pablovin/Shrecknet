"""Validate and render evidence-grounded ShreckWorld query answers."""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.shreckllm import ShreckLLMUnavailable, generate_grounded_answer
from app.models import (
    ShreckWorldDocumentBlock, ShreckWorldDocumentChunk, ShreckWorldDocumentPage,
)
from app.schemas.shreckworld import (
    AnswerPresentation, AnswerSection, CitationLocation, CitationRectangle,
    ClaimEvidenceSupport, GroundedClaim, GroundedConflict, ShreckWorldCitation,
    ShreckWorldEvidence, ShreckWorldModelTarget,
)
from app.services.citation_regions import citation_rectangles


class GroundedAnswerError(RuntimeError):
    """The requested synthesis could not safely be produced."""


class GroundedAnswerUnavailable(GroundedAnswerError):
    """The configured ShreckLLM service could not complete synthesis."""


class GroundedAnswerInvalid(GroundedAnswerError):
    """The model returned invalid structure or unsupported citation references."""


class EvidenceBudgetExceeded(GroundedAnswerError):
    """No complete evidence passage fits the configured synthesis budget."""


class _GroundedSupport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str
    quote: str


class _GroundedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    citation_ids: list[str]
    kind: Literal["fact", "rule", "condition", "exception", "interpretation"]
    support: list[_GroundedSupport]


class _GroundedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["complete", "partial", "unsupported"]
    claims: list[_GroundedClaim]
    missing_information: list[str]
    conflicts: list[GroundedConflict]


def synthesize_grounded_answer(
    *, query: str, citations: list[ShreckWorldCitation],
    model_target: ShreckWorldModelTarget, session: Session | None = None,
    response_detail: Literal["concise", "standard", "detailed"] = "concise",
) -> tuple[str, Literal["complete", "partial", "unsupported"], list[GroundedClaim],
           list[str], list[GroundedConflict], int, AnswerPresentation, list[ShreckWorldEvidence]]:
    evidence, supplied_ids = _evidence_package(citations)
    if not evidence:
        raise EvidenceBudgetExceeded(
            "No complete evidence passage fits the configured synthesis budget"
        )
    try:
        raw = generate_grounded_answer(
            query=query, evidence=evidence, model_target=model_target,
            response_detail=response_detail,
        )
    except ShreckLLMUnavailable as exc:
        raise GroundedAnswerUnavailable(str(exc)) from exc
    try:
        result = _GroundedAnswer.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise GroundedAnswerInvalid("ShreckLLM returned invalid grounded-answer structure") from exc
    _validate_citations(result, supplied_ids, citations)
    by_source = {citation.source_id: citation for citation in citations}
    claims = [
        GroundedClaim(
            id=f"claim-{index}",
            text=claim.text,
            citation_ids=claim.citation_ids,
            kind=claim.kind,
            support=[
                ClaimEvidenceSupport(
                    evidence_id=support.source_id,
                    quote=support.quote,
                    location=_resolve_quote_location(
                        session, by_source[support.source_id], support.quote,
                    ),
                )
                for support in claim.support
            ],
        )
        for index, claim in enumerate(result.claims, start=1)
    ]
    presentation = _render_presentation(claims, result.missing_information)
    answer = _render_answer(presentation)
    omitted_citations = len(citations) - len(supplied_ids)
    return (
        answer, result.status, claims, result.missing_information, result.conflicts,
        omitted_citations, presentation, [evidence_item(citation) for citation in citations],
    )


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


def _validate_citations(
    result: _GroundedAnswer, supplied_ids: set[str], citations: list[ShreckWorldCitation],
) -> None:
    if result.status == "unsupported":
        if result.claims:
            raise GroundedAnswerInvalid("Unsupported answers must not contain factual claims")
    elif not result.claims:
        raise GroundedAnswerInvalid("Supported answers must contain at least one cited claim")

    citation_text = {citation.source_id: citation.evidence_text or citation.excerpt for citation in citations}
    for claim in result.claims:
        claim_ids = set(claim.citation_ids)
        support_ids = {support.source_id for support in claim.support}
        if not claim.text.strip() or not claim_ids <= supplied_ids or support_ids != claim_ids:
            raise GroundedAnswerInvalid("A claim is empty or cites evidence not supplied to synthesis")
        for support in claim.support:
            source_text = citation_text.get(support.source_id, "")
            if not support.quote.strip() or _canonical(support.quote) not in _canonical(source_text):
                raise GroundedAnswerInvalid("A claim contains a non-verbatim evidence quote")
    for conflict in result.conflicts:
        if not conflict.description.strip() or not set(conflict.citation_ids) <= supplied_ids:
            raise GroundedAnswerInvalid("A conflict is empty or cites evidence not supplied to synthesis")
    if any(not item.strip() for item in result.missing_information):
        raise GroundedAnswerInvalid("Missing-information entries must not be empty")


def _canonical(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _format_claim(claim: GroundedClaim) -> str:
    refs = " ".join(f"[{citation_id}]" for citation_id in dict.fromkeys(claim.citation_ids))
    return f"{claim.text.rstrip()} {refs}"


def _render_presentation(
    claims: list[GroundedClaim], missing_information: list[str],
) -> AnswerPresentation:
    if not claims:
        return AnswerPresentation(
            direct_answer="The available sources do not establish an answer to this question.",
            follow_up_hint="Try narrowing the question or selecting a different indexed source.",
        )
    direct_answer = _format_claim(claims[0])
    remaining = claims[1:]
    grouped: dict[str, list[GroundedClaim]] = {}
    headings = {
        "fact": "Key details", "rule": "How it works", "condition": "Conditions",
        "exception": "Limits and exceptions", "interpretation": "Interpretation",
    }
    for claim in remaining:
        grouped.setdefault(headings[claim.kind], []).append(claim)
    sections = [
        AnswerSection(
            heading=heading,
            text=" ".join(_format_claim(claim) for claim in group),
            claim_ids=[claim.id for claim in group],
            citation_ids=list(dict.fromkeys(
                source_id for claim in group for source_id in claim.citation_ids
            )),
        )
        for heading, group in grouped.items()
    ]
    hint = "Ask a follow-up for more detail on the unresolved points." if missing_information else None
    return AnswerPresentation(
        direct_answer=direct_answer, sections=sections, follow_up_hint=hint,
    )


def _render_answer(presentation: AnswerPresentation) -> str:
    parts = [presentation.direct_answer]
    parts.extend(f"{section.heading}: {section.text}" for section in presentation.sections)
    if presentation.follow_up_hint:
        parts.append(presentation.follow_up_hint)
    return "\n\n".join(parts)


def evidence_item(citation: ShreckWorldCitation) -> ShreckWorldEvidence:
    blocks = list(dict.fromkeys(span.block_id for span in citation.provenance))
    location = CitationLocation(
        precision="block" if len(blocks) == 1 else "section",
        page_number=citation.page_number if len(blocks) == 1 else None,
        page_label=citation.page_label if len(blocks) == 1 else None,
        page_numbers=citation.page_numbers or [citation.page_number],
        block_id=blocks[0] if len(blocks) == 1 else None,
        rectangles=citation.rectangles if len(blocks) == 1 else [],
    )
    return ShreckWorldEvidence(
        id=citation.source_id,
        source={
            "library_item_id": citation.library_item_id,
            "title": citation.title,
            "source_sha256": citation.source_sha256,
            "authority_tier": citation.authority_tier,
            "authority_priority": citation.authority_priority,
            "document_id": citation.document_id,
        },
        location=location,
        excerpt=citation.excerpt,
        context_excerpt=citation.parent_excerpt,
        content_url=citation.content_url,
    )


def _resolve_quote_location(
    session: Session | None, citation: ShreckWorldCitation, quote: str,
) -> CitationLocation:
    fallback = evidence_item(citation).location.model_copy()
    if session is None or not citation.document_id:
        return fallback

    chunk = session.get(ShreckWorldDocumentChunk, citation.chunk_id)
    if chunk is None or chunk.document_id != citation.document_id:
        return fallback
    related_chunks = [chunk]
    if chunk.parent_chunk_id:
        related_chunks = list(session.scalars(select(ShreckWorldDocumentChunk).where(
            ShreckWorldDocumentChunk.document_id == chunk.document_id,
            ShreckWorldDocumentChunk.parent_chunk_id == chunk.parent_chunk_id,
        )))
    block_ids = list(dict.fromkeys(
        block_id for related in related_chunks for block_id in (related.source_block_ids or [])
    ))
    if not block_ids:
        return fallback
    blocks = list(session.scalars(select(ShreckWorldDocumentBlock).where(
        ShreckWorldDocumentBlock.document_id == citation.document_id,
        ShreckWorldDocumentBlock.id.in_(block_ids),
    )))
    matches = [block for block in blocks if _canonical(quote) in _canonical(block.display_text)]
    if len(matches) != 1:
        return fallback

    block = matches[0]
    spans = []
    for item in block.provenance or []:
        page_number = int(item.get("page_no") or 1)
        page = session.scalar(select(ShreckWorldDocumentPage).where(
            ShreckWorldDocumentPage.document_id == citation.document_id,
            ShreckWorldDocumentPage.page_number == page_number,
        ))
        spans.append({
            "block_id": block.id,
            "page_number": page_number,
            "page_label": page.page_label if page else None,
            "page_width": page.width if page else None,
            "page_height": page.height if page else None,
            "bounding_box": item.get("bbox") or None,
            "charspan": item.get("charspan"),
        })
    page_numbers = list(dict.fromkeys(span["page_number"] for span in spans))
    first = spans[0] if spans else {}
    return CitationLocation(
        precision="block",
        page_number=first.get("page_number"),
        page_label=first.get("page_label"),
        page_numbers=page_numbers or citation.page_numbers or [citation.page_number],
        block_id=block.id,
        charspan=first.get("charspan"),
        rectangles=citation_rectangles(spans),
    )
