"""Stage 1 of Novelist v4: source interpretation into a compact story plan."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.jobs.novelist.prompts import (
    build_reconciliation_prompt,
    build_story_plan_prompt,
)
from app.jobs.novelist.story_plan import (
    STORY_PLAN_JSON_SCHEMA,
    NarrativeStoryPlan,
    SourceSegment,
    validate_source_references,
)

JsonCaller = Callable[[str, dict[str, Any], str], Awaitable[dict[str, Any]]]


def segment_source(text: str, *, max_chars: int = 12000) -> list[SourceSegment]:
    """Split on paragraph boundaries, also bounding an oversized paragraph."""

    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]
    bounded: list[str] = []
    for paragraph in paragraphs:
        while len(paragraph) > max_chars:
            split_at = paragraph.rfind(" ", 0, max_chars + 1)
            if split_at < max_chars // 2:
                split_at = max_chars
            bounded.append(paragraph[:split_at].strip())
            paragraph = paragraph[split_at:].strip()
        if paragraph:
            bounded.append(paragraph)

    chunks: list[str] = []
    buffer = ""
    for paragraph in bounded:
        if buffer and len(buffer) + len(paragraph) + 2 > max_chars:
            chunks.append(buffer)
            buffer = ""
        buffer = f"{buffer}\n\n{paragraph}".strip()
    if buffer:
        chunks.append(buffer)
    return [
        SourceSegment(id=f"source-{index:04d}", text=chunk)
        for index, chunk in enumerate(chunks, start=1)
    ]


async def interpret_source(
    *,
    text: str,
    source_type: str,
    language: str,
    instructions: str,
    call_json: JsonCaller,
) -> tuple[NarrativeStoryPlan, list[SourceSegment]]:
    """Build one story plan, using partial plans only when input is oversized."""

    segments = segment_source(text)
    if len(segments) <= 4:
        payload = await call_json(
            build_story_plan_prompt(
                source_type=source_type,
                language=language,
                instructions=instructions,
                segments=segments,
            ),
            STORY_PLAN_JSON_SCHEMA,
            "novelist.analysis.story_plan",
        )
    else:
        partials: list[dict[str, Any]] = []
        for segment in segments:
            partials.append(
                await call_json(
                    build_story_plan_prompt(
                        source_type=source_type,
                        language=language,
                        instructions=instructions,
                        segments=[segment],
                    ),
                    STORY_PLAN_JSON_SCHEMA,
                    "novelist.analysis.story_plan_chunk",
                )
            )
        payload = await call_json(
            build_reconciliation_prompt(segments=segments, partial_plans=partials),
            STORY_PLAN_JSON_SCHEMA,
            "novelist.analysis.story_plan_reconcile",
        )

    plan = NarrativeStoryPlan.model_validate(payload)
    return validate_source_references(plan, segments), segments
