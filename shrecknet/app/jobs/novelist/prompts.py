"""Novelist V5 prompts in execution order.

Normal path: stage 1 global planning consumes all indexed passages and returns a
source-linked dramatic plan. Oversized-input branch: stage 1a factual inventories
consume transport windows; stage 1b globally synthesizes inventories into the same
plan. Neither branch imposes a beat count on individual source windows. Stage 1r
repairs any rejected structured call once, beside its shared analysis contract.
Stage 2 writes ordered beat units; stage 2r replaces unusable prose once. Only
accepted writing becomes public output, after backend validation and HTML escaping.
There is no independent semantic verifier. Passage indexing, range resolution,
coverage diagnostics, and writing-unit subdivision run deterministically.
"""
from __future__ import annotations
import json
from typing import Any
from app.jobs.novelist.story_plan import NarrativeStoryPlan, SourceSegment


# Purpose: define dramatic scale and whole-source coverage consistently.
# Used by: global planning and inventory synthesis, plus their recovery variants.
# Output: compact source-faithful dramatic developments, usually 8–10 per session.
_DRAMATIC_CONTRACT = """Read the complete source as one narrative. A story beat is a meaningful
 dramatic development, NOT an individual paragraph, conversation, combat round,
 or gameplay action. Combine related events around conflicts, turning points,
 character decisions, revelations, stakes, resolutions, and consequences. Explain
 what changes and why in each summary. Aim for approximately 8–10 beats for a
 typical three-hour RPG session, adjusting to actual complexity; this is a soft
 target, with no maximum beat count. Do not pad brief sources or merge unrelated
 developments merely to meet a count. Exclude out-of-fiction discussions, rules,
 and administrative details unless their fictional consequences matter. Smaller
 relevant actions stay in original passages for the writer.
 Before returning, review coverage from the beginning through the middle and the
 final fictional development. Preserve unresolved endings. Do not invent missing
 outcomes, motives, characters, or consequential events."""

# Purpose: self-contained structured plan output and passage-reference contract.
# Used by: stage 1 planning and stage 1b synthesis; inherited by repair.
# Output: strict JSON matching NarrativeStoryPlan and SourceRange.
_PLAN_CONTRACT = """Return exactly this complete JSON structure (all keys required, no extra keys):
{"title":string|null,"cast":[{"player":string,"character":string}],
 "beats":[{"beat_id":string,"summary":string,
 "importance":"major"|"supporting"|"transition",
 "source_ranges":[{"start_id":string,"end_id":string}]}],
 "continuity_notes":string|null}
Every root field and every nested field is required. title: null or at most 120
characters; cast: 0–10 explicit player/character mappings, each name 1–60 characters;
beats: one or more ordered dramatic developments, each summary 1–600 characters
(prefer 1–3 concise sentences); beat_id: unique 1–32 character identifier in narrative
order, such as beat-001; source_ranges: 1–8 inclusive ranges per beat, each endpoint
an exact supplied passage ID of 1–32 characters. Never invent or shorten IDs.
A range includes all indexed passages from start_id through end_id in source order.
Use multiple ranges for separated evidence; never repeat an identical range.
Shared passages between beats are allowed when necessary. Each range must run
forward. continuity_notes: null or at most 2000 characters.
Use null for absent title/continuity notes, [] for absent cast. Return compact JSON
without indentation, prose, or Markdown. Keep the complete response within 6,000
tokens. Field maxima are ceilings, not targets; shorten summaries before losing
major developments or the ending."""

# Purpose: describe the authoritative index, independent of transport windows.
# Used by: planning, inventory, and writer input contracts.
# Output: exact selection of supplied passage IDs, without invented page metadata.
_PASSAGE_CONTRACT = """Passages are ordered objects: id is an immutable reference; text is verbatim
canonical extracted text; start/end are inclusive/exclusive character offsets into
that text; pages is an array of original PDF page numbers, empty when unavailable.
Paragraphs and bounded transcript fragments are references, not automatic beats."""


# Purpose: globally frame the complete session into dramatic developments.
# Used by: source_interpreter stage 1 when the entire prompt fits its budget.
# Output: complete NarrativeStoryPlan JSON for validation and sequential writing.
def build_story_plan_prompt(*, source_type: str, language: str, instructions: str, segments: list[SourceSegment]) -> str:
    return f"""Stage 1 — Global Narrative Understanding (framing).
{_DRAMATIC_CONTRACT}
{_PLAN_CONTRACT}
Input contract: source_type is a non-authoritative hint, one of auto, transcript,
recap, notes, adventure, event_log, narrative. language is the requested writing
language; instructions are user planning/style constraints. source_segments is
all authoritative source passages in order. {_PASSAGE_CONTRACT}
source_type={source_type}
language={language or 'source language'}
instructions={instructions or 'none'}
source_segments={json.dumps([s.model_dump() for s in segments], ensure_ascii=False)}"""


# Purpose: extract facts from one transport window without local chapter structure.
# Used by: source_interpreter stage 1a only when the global prompt cannot fit.
# Output: SourceInventory JSON; observations feed the global synthesis stage.
def build_inventory_prompt(*, segments: list[SourceSegment], source_type: str, language: str, instructions: str) -> str:
    return f"""Stage 1a — Factual Source Inventory (reasoning).
This is a window of a larger source, not an independent chapter. Record concise
chronological fictional developments, decisions, discoveries, consequences,
unresolved threads, and explicit player/character mappings. Retain the ending of
this window. Omit table chatter unless fictional consequences matter. Never
invent facts. Do not impose an 8–10 observation count or create a chapter plan.
Return exactly {{"observations":[{{"summary":string,"source_ranges":[{{"start_id":string,"end_id":string}}]}}],"cast":[{{"player":string,"character":string}}]}}.
All fields required; no extra keys. observations: non-empty ordered array; summary:
1–600 characters; source_ranges: 1–8 inclusive forward ranges of exact supplied IDs
(1–32 characters each), with no identical duplicate ranges. cast: 0–10 explicit
mappings, names 1–60 characters, [] if absent. For an entirely non-fiction window,
record that fact with its range so the global planner can omit it. Compact JSON
only, within 6,000 tokens. The output feeds global synthesis, never public prose.
Input: source_type is an auto/transcript/recap/notes/adventure/event_log/narrative
hint; language is requested chapter language; instructions are user constraints;
source_segments is this ordered source window. {_PASSAGE_CONTRACT}
source_type={source_type}
language={language}
instructions={instructions}
source_segments={json.dumps([s.model_dump() for s in segments], ensure_ascii=False)}"""


# Purpose: build one chapter-wide dramatic structure from factual inventories.
# Used by: source_interpreter stage 1b, only after all oversized-source windows.
# Output: complete NarrativeStoryPlan; never concatenated local beat lists.
def build_reconciliation_prompt(*, segments: list[SourceSegment], partial_plans: list[dict[str, Any]], language: str = "", instructions: str = "") -> str:
    return f"""Stage 1b — Global Narrative Synthesis (framing).
{_DRAMATIC_CONTRACT}
{_PLAN_CONTRACT}
Input: passage_ids is the complete ordered immutable index (text omitted for input
sizing); partial_plans contains ordered factual inventories, NOT local chapter
plans, each with observations (summary and inclusive source_ranges) and cast
(player/character mappings). Preserve their facts, merge related developments
across windows, remove duplication, and create ONE chapter-wide structure.
language is requested chapter language; instructions are user constraints.
This branch uses inventories rather than a single full-source reading.
language={language}
instructions={instructions}
passage_ids={json.dumps([s.id for s in segments])}
partial_plans={json.dumps(partial_plans, ensure_ascii=False)}"""


def _bounded_text(value: str, limit: int) -> str:
    return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


# Purpose: regenerate rejected structured output under its original contract.
# Used by: orchestrator stage 1r, immediately after any failed planning/inventory call.
# Output: complete original JSON shape; schema is supplemental to the original text.
def build_analysis_retry_prompt(*, original_prompt: str, rejected: str, error: Exception | None, schema: dict[str, Any]) -> str:
    return f"""{original_prompt}
Stage 1r — Structured Recovery (reasoning).
Regenerate the complete original root object, including nullable fields. The
previous output failed validation. Shorten summaries and combine related facts
while preserving major developments and the ending. Stay within the 6,000-token
budget. Fix invalid references using only supplied IDs. Never slice JSON.
rejected_response={_bounded_text(rejected, 6000)}
validation_error={_bounded_text(str(error), 2000)}
required_json_schema={json.dumps(schema, ensure_ascii=False)}"""


# Purpose: render one dramatic beat or bounded subdivision using original evidence.
# Used by: orchestrator stage 2 sequentially; only accepted prose is published.
# Output: plain prose paragraphs for local checks and backend HTML escaping.
def build_writer_prompt(*, plan: NarrativeStoryPlan, block: dict[str, Any], source_segments: list[SourceSegment], language: str, instructions: str, continuity: dict[str, Any], previous_tail: str) -> str:
    ending = ("This is the final writing unit. Give the source's ending a deliberate literary conclusion; preserve unresolved outcomes." if block["is_final"] else "End at a natural transition; do not conclude the chapter or consume later beats.")
    return f"""Stage 2 — Literary Writing (rendering).
Write polished novel prose, not a recap, screenplay, transcript, or gameplay log.
Use natural dialogue, atmosphere, developed paragraphs, and deliberate pacing.
Preserve source facts, decisions, discoveries, chronology, and outcomes. Develop
smaller relevant fictional actions in the supporting passages within the current
beat. Do not invent consequential events, resolve uncertainties, or turn table
chatter into fiction. Avoid repeating previous prose or writing future beats.
A subdivision covers only its supplied passages; the whole beat may continue in
another unit. Overall neighboring beat summaries guide transitions, not new facts.
Return plain prose paragraphs only: no title, headings, bullets, JSON, HTML,
Markdown fences, or commentary. Short dramatic paragraphs are allowed.
Write in {language or 'the source language'}, targeting {block['target_words']} words.
{ending}
Input contract: complete_story_plan has title, cast, ordered beats (beat_id,
summary, importance, source_ranges), and continuity_notes. current_block has
block_id, assigned beat_ids, target_words, and is_final. supporting_source_segments
are selected original passages. {_PASSAGE_CONTRACT}
continuity contains lower-authority previous_session, previous_run_context, and
authority_order; none may override current source. Previous-run references belong
to another document and must never resolve here. previous_accepted_tail is the
preceding accepted prose or empty. user_instructions are user style constraints.
user_instructions={instructions or 'none'}
complete_story_plan={plan.model_dump_json()}
current_block={json.dumps(block, ensure_ascii=False)}
supporting_source_segments={json.dumps([s.model_dump() for s in source_segments], ensure_ascii=False)}
continuity={json.dumps(continuity, ensure_ascii=False)}
previous_accepted_tail={previous_tail or 'none'}"""


# Purpose: replace an unusable literary unit once without changing its assignment.
# Used by: orchestrator stage 2r following failed local prose checks.
# Output: complete replacement plain prose paragraphs, never public until accepted.
def build_writer_retry_prompt(*, original_prompt: str, rejected: str, errors: list[str]) -> str:
    return f"""{original_prompt}
Stage 2r — Literary Recovery (rendering).
Rewrite the entire unit once to fix {json.dumps(errors, ensure_ascii=False)}.
Preserve the same facts, assignment, order, language, and target length. Plain prose
paragraphs only; never invent an ending.
rejected_rendering={_bounded_text(rejected, 6000)}"""
