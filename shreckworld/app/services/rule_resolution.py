"""Read-only source-priority resolution shared by UI and authenticated agents.

Priority is an explicit source preference, not proof of rule applicability. Without
edition/supersession metadata this service deliberately reports ambiguity instead
of treating a retrieved passage as an executable game rule.
"""
from sqlalchemy.orm import Session

from app.schemas.shreckworld import ActionResolutionRequest, ActionResolutionResponse
from app.services.query import query_shreckworld


def resolve_action(session: Session, *, shreckworld_id: str, request: ActionResolutionRequest) -> ActionResolutionResponse:
    citations, _ = query_shreckworld(session, shreckworld_id=shreckworld_id,
        query=" ".join([request.action, *request.facts])[:4000], top_k=12, library_item_id=request.library_item_id)
    if not citations:
        return ActionResolutionResponse(status="unsupported", explanation="No indexed source supports this action.",
            citations=[], missing_facts=["An applicable indexed rule is required."])
    priority = min(citation.authority_priority for citation in citations)
    preferred = list(dict.fromkeys(c.library_item_id for c in citations if c.authority_priority == priority))
    other = list(dict.fromkeys(c.library_item_id for c in citations if c.authority_priority != priority))
    return ActionResolutionResponse(status="ambiguous",
        explanation="Evidence is ordered by configured source priority (lower values first). Rule applicability, editions and explicit overrides are not modeled; confirm the applicable rule before executing this action.",
        citations=sorted(citations, key=lambda c: (c.authority_priority, -c.score, c.chunk_id)),
        preferred_source_ids=preferred, conflicting_source_ids=other + (preferred if len(preferred) > 1 else []),
        assumptions=["Configured source priority expresses the world's preferred authority; tier names do not imply precedence."],
        missing_facts=["Confirm ruleset/edition, applicability and any explicit override or errata."])
