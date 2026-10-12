"""One editorial analysis call over candidate JSON, never original source content."""
from __future__ import annotations

from app.jobs.novelist.prompts import build_director_prompt
from app.jobs.novelist.source_interpreter import JsonCaller
from app.jobs.novelist.story_plan import (
    DIRECTED_PLAN_JSON_SCHEMA, CandidateExtraction,
    DirectedStoryPlan, validate_directed_plan, validate_instruction_identities,
)


async def direct_story(
    *, candidate: CandidateExtraction, instructions: str, language: str,
    call_json: JsonCaller,
) -> DirectedStoryPlan:
    """The intentionally narrow signature prevents accidental PDF/prose forwarding."""
    validate_instruction_identities(candidate, instructions)

    def validator(value):
        return validate_directed_plan(DirectedStoryPlan.model_validate(value), candidate)

    value = await call_json(
        build_director_prompt(candidate=candidate, instructions=instructions, language=language),
        DIRECTED_PLAN_JSON_SCHEMA, "novelist.analysis.director", validator=validator,
    )
    return validator(value)
