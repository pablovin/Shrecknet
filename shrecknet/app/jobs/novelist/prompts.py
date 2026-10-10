"""Prompts for the Novelist v4 chapter pipeline.

Execution order:
1. ``build_story_plan_prompt`` frames source segments as a compact editorial
   plan. Its JSON output becomes the planning and writing input.
2. ``build_analysis_retry_prompt`` repairs one malformed or truncated structured
   result from either analysis stage while retaining the complete source prompt.
3. ``build_reconciliation_prompt`` is used only for oversized sources. It
   combines ordered partial plans into the same complete JSON contract.
4. ``build_writer_prompt`` renders one planned block as plain literary prose.
   Blocks run sequentially and receive a tail from the preceding accepted block.
5. ``build_writer_retry_prompt`` repairs only an unusable writing response.

Only narrative planning, its retry, and reconciliation return structured JSON.
Writer and writer-retry prose can become public output after deterministic checks
and backend-owned HTML rendering.
"""

from __future__ import annotations

import json
from typing import Any

from app.jobs.novelist.story_plan import NarrativeStoryPlan, SourceSegment


# Purpose: frame raw source as an ordered story plan.
# Used by: source_interpreter, analysis stage 1.
# Expected output: the complete story-plan JSON object documented below.
def build_story_plan_prompt(
    *, source_type: str, language: str, instructions: str, segments: list[SourceSegment]
) -> str:
    return f"""Stage 1 — Narrative Understanding (framing).

Act as an editor organizing notes for a novelist. Understand who did what and in
which order. Distinguish player/table discussion from fictional action and
dialogue. Keep important discoveries, decisions, outcomes, character details,
and transitions. Do not invent events or resolve ambiguity that the source does
not resolve.

Input contract:
- source_type: a non-authoritative hint; allowed values are auto, transcript,
  recap, notes, adventure, event_log, or narrative.
- language: requested chapter language; it does not change source facts.
- instructions: user constraints that apply to planning and writing.
- source_segments: ordered objects with immutable `id` and verbatim `text`.

Return exactly one JSON object with all four required root fields:
{{
  "title": string or null,
  "cast": [{{"player": "player name", "character": "character name"}}],
  "beats": [
    {{
      "beat_id": "beat-001",
      "summary": "A concise source-faithful narrative event",
      "importance": "major" or "supporting" or "transition",
      "source_ids": ["source-0001"]
    }}
  ],
  "continuity_notes": string or null
}}

Every root field and every beat field is required. `beats` and each `source_ids`
array must be non-empty. Beat IDs must be unique and follow narrative order.
Every source ID must come from source_segments. Use an empty array for `cast`
when no player-to-character mapping exists. Do not return scenes, evidence
claims, prose, Markdown, or any key not shown in the contract.

source_type={source_type}
language={language or 'source language'}
instructions={instructions or 'none'}
source_segments={json.dumps([segment.model_dump() for segment in segments], ensure_ascii=False)}"""


# Purpose: repair one rejected story-plan or reconciliation response.
# Used by: orchestrator after a malformed, incomplete, or truncated analysis call.
# Expected output: the same complete JSON object required by the original prompt.
def build_analysis_retry_prompt(
    *,
    original_prompt: str,
    rejected: str,
    error: Exception | None,
    schema: dict[str, Any],
) -> str:
    return f"""{original_prompt}

The previous structured response was unusable. Return the complete root object
again, with every required field. Do not return a nested field by itself or omit
nullable fields. Return JSON only.
rejected_response={rejected[:12000]}
validation_error={error}
required_json_schema={json.dumps(schema, ensure_ascii=False)}"""


# Purpose: reconcile transport-sized partial plans without expanding them.
# Used by: source_interpreter, analysis stage 1b for oversized inputs only.
# Expected output: one complete story-plan JSON object with renumbered beats.
def build_reconciliation_prompt(
    *, segments: list[SourceSegment], partial_plans: list[dict[str, Any]]
) -> str:
    return f"""Stage 1b — Narrative Plan Reconciliation (framing).

Combine the ordered partial plans into one compact, source-faithful plan. Remove
duplication at chunk boundaries, preserve chronology, and renumber beat_id from
beat-001. Preserve only supplied source_ids. Do not add events.

Return exactly one JSON object with all required fields:
{{"title": string|null, "cast": [{{"player":"...", "character":"..."}}], "beats":
[{{"beat_id":"beat-001", "summary":"...", "importance":"major|supporting|transition",
"source_ids":["source-0001"]}}], "continuity_notes": string|null}}

Every displayed root and beat field is required. No additional keys are allowed.
source_segments={json.dumps([segment.model_dump() for segment in segments], ensure_ascii=False)}
partial_plans={json.dumps(partial_plans, ensure_ascii=False)}"""


# Purpose: render one story-plan block as continuous literary prose.
# Used by: orchestrator, writing stage 2 for every block.
# Expected output: plain prose paragraphs only, never JSON or HTML.
def build_writer_prompt(
    *,
    plan: NarrativeStoryPlan,
    block: dict[str, Any],
    source_segments: list[SourceSegment],
    language: str,
    instructions: str,
    continuity: dict[str, Any],
    previous_tail: str,
) -> str:
    ending = (
        "This is the final section. End with a complete, deliberate literary "
        "conclusion; do not trail off or promise a later continuation."
        if block["is_final"]
        else "This is not the final section. End at a natural transition without summarizing the chapter."
    )
    return f"""Stage 2 — Literary Writing (rendering).

Write this material as a polished chapter of a published novel, not as a recap,
screenplay, transcript, or retelling of gameplay. Use continuous literary prose,
fully developed paragraphs, natural dialogue, sensory description, narrative
transitions, and deliberate pacing. Dramatize important events instead of merely
summarizing them.

Avoid headings, bullet points, enumerations, repetitive rhetorical structures,
and a cascade of isolated sentence paragraphs. Short paragraphs are allowed when
dramatically appropriate. Preserve the actual characters, events, discoveries,
decisions, chronology, and outcomes. You may enrich atmosphere and connective
narration, but never invent consequential events or change the established story.

Return plain prose paragraphs only. Do not return HTML, Markdown fences, a title,
JSON, notes, or an explanation. Write in {language or 'the source language'} and
target approximately {block['target_words']} words. {ending}

Input contract:
- complete_story_plan: the chapter-wide ordered editorial plan.
- current_block: ordered beat IDs assigned to this prose section.
- supporting_source_segments: verbatim source text relevant to those beats.
- continuity: lower-authority prior-session context; it may guide continuity but
  must not override or add facts to the current source.
- previous_accepted_tail: the end of the preceding section, or empty for the first.
- user_instructions: user-supplied style and content constraints.

user_instructions={instructions or 'none'}
complete_story_plan={plan.model_dump_json()}
current_block={json.dumps(block, ensure_ascii=False)}
supporting_source_segments={json.dumps([segment.model_dump() for segment in source_segments], ensure_ascii=False)}
continuity={json.dumps(continuity, ensure_ascii=False)}
previous_accepted_tail={previous_tail or 'none'}"""


# Purpose: retry a genuinely unusable prose section once.
# Used by: orchestrator immediately after a failed deterministic completion check.
# Expected output: replacement plain prose paragraphs only.
def build_writer_retry_prompt(
    *, original_prompt: str, rejected: str, errors: list[str]
) -> str:
    return f"""{original_prompt}

The previous rendering was unusable for these concrete reasons:
{json.dumps(errors, ensure_ascii=False)}

Rewrite the entire section once. Correct those failures while preserving the same
story facts, order, language, instructions, and target length. Return plain prose
paragraphs only.
rejected_rendering={rejected[:12000]}"""
