"""Index authoritative passages, then plan globally or synthesize factual windows."""
from __future__ import annotations
import re
from typing import Any, Awaitable, Callable

from app.schemas.novelist import SourcePage
from app.jobs.novelist.context_budget import fits_context, require_context
from app.jobs.novelist.prompts import build_inventory_prompt, build_reconciliation_prompt, build_story_plan_prompt
from app.jobs.novelist.story_plan import (
    EVIDENCE_INVENTORY_JSON_SCHEMA, CANDIDATE_JSON_SCHEMA, CandidateExtraction,
    EvidenceInventory, SourceSegment, resolve_ranges, validate_source_references, validate_instruction_identities,
)

JsonCaller = Callable[..., Awaitable[dict[str, Any]]]


def segment_source(text: str, *, max_chars: int = 1800, pages: list[SourcePage] | None = None) -> list[SourceSegment]:
    """Keep exact text/offsets; paragraph reference units never become beat quotas."""
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    segments = []
    for paragraph in re.finditer(r'.+?(?:\n[ \t]*\n|\Z)', text, re.DOTALL):
        start, stop = paragraph.span()
        while start < stop:
            end = min(start + max_chars, stop)
            if end < stop:
                boundary = text.rfind(' ', start + max_chars // 2, end)
                if boundary >= 0:
                    end = boundary + 1
            segments.append(SourceSegment(
                id=f"passage-{len(segments) + 1:06d}", text=text[start:end],
                start=start, end=end,
                pages=[page.page for page in pages or [] if page.start < end and page.end > start],
            ))
            start = end
    return segments


async def interpret_source(
    *, text: str, source_type: str, language: str, instructions: str,
    call_json: JsonCaller, context_tokens: int = 64000, output_tokens: int = 6000,
    pages: list[SourcePage] | None = None,
    diagnostic: Callable[..., None] | None = None,
) -> tuple[CandidateExtraction, list[SourceSegment]]:
    if any(page.end > len(text) for page in pages or []):
        raise ValueError("PDF page offsets exceed canonical source length")
    segments = segment_source(text, pages=pages)
    if not segments or not text.strip():
        raise ValueError("Novelist source contains no text")

    def plan_validator(value):
        candidate = CandidateExtraction.model_validate(value)
        validate_instruction_identities(candidate, instructions)
        return validate_source_references(candidate, segments)

    prompt = build_story_plan_prompt(source_type=source_type, language=language, instructions=instructions, segments=segments)
    if diagnostic:
        diagnostic("source_index", indexing_version="paragraph-v1", passages=[s.model_dump() for s in segments])
    if fits_context(prompt, CANDIDATE_JSON_SCHEMA, context_tokens=context_tokens, output_tokens=output_tokens):
        path = "global"
        if diagnostic:
            diagnostic("planning_path", path=path, context_tokens=context_tokens, output_tokens=output_tokens)
        value = await call_json(prompt, CANDIDATE_JSON_SCHEMA, "novelist.analysis.story_plan", validator=plan_validator)
    else:
        path = "inventory_synthesis"
        if diagnostic:
            diagnostic("planning_path", path=path, context_tokens=context_tokens, output_tokens=output_tokens)
        # Reserve recovery headroom before packing windows. Each complete request,
        # including the bounded repair, is checked again by the orchestrator.
        window_context = int(context_tokens * 0.8)
        windows: list[list[SourceSegment]] = []
        current: list[SourceSegment] = []
        for segment in segments:
            candidate = current + [segment]
            request = build_inventory_prompt(segments=candidate, source_type=source_type, language=language, instructions=instructions)
            if current and not fits_context(request, EVIDENCE_INVENTORY_JSON_SCHEMA, context_tokens=window_context, output_tokens=output_tokens):
                windows.append(current)
                current = [segment]
            else:
                current = candidate
        if current:
            windows.append(current)
        inventories = []
        for window in windows:
            def inventory_validator(value, window=window):
                inventory = EvidenceInventory.model_validate(value)
                for item in inventory.observations:
                    resolve_ranges(item.source_ranges, window)
                return inventory
            request = build_inventory_prompt(segments=window, source_type=source_type, language=language, instructions=instructions)
            if diagnostic:
                diagnostic("inventory.request", prompt=request, schema=EVIDENCE_INVENTORY_JSON_SCHEMA)
            require_context(request, EVIDENCE_INVENTORY_JSON_SCHEMA, context_tokens=context_tokens, output_tokens=output_tokens)
            inventories.append(await call_json(request, EVIDENCE_INVENTORY_JSON_SCHEMA, "novelist.analysis.inventory", validator=inventory_validator))
        prompt = build_reconciliation_prompt(segments=segments, partial_plans=inventories, language=language, instructions=instructions)
        # An explicitly bounded failure is preferable to dropping inventories or
        # sending a synthesis request that still exceeds the configured context.
        if diagnostic:
            diagnostic("synthesis.request", prompt=prompt, schema=CANDIDATE_JSON_SCHEMA)
        require_context(prompt, CANDIDATE_JSON_SCHEMA, context_tokens=context_tokens, output_tokens=output_tokens)
        value = await call_json(prompt, CANDIDATE_JSON_SCHEMA, "novelist.analysis.story_plan_reconcile", validator=plan_validator)
    if diagnostic:
        diagnostic("planning_path", path=path, context_tokens=context_tokens, output_tokens=output_tokens)
    return plan_validator(value), segments
