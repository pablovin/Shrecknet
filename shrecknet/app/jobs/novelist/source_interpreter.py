"""Stage 1 of Novelist v3: source interpretation into a provenance-backed ledger.

For large sources, independently interpreted segment ledgers are reconciled by
the same analysis role.  Segmenting is only a transport boundary; it never
attempts to decide narrative meaning.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from app.jobs.novelist.evidence_ledger import NarrativeEvidenceLedger, SourceSegment, validate_provenance

JsonCaller = Callable[[str, dict[str, Any], str], Awaitable[dict[str, Any]]]


def segment_source(text: str, *, max_chars: int = 12000) -> list[SourceSegment]:
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]
    out: list[SourceSegment] = []
    buffer = ""
    for paragraph in paragraphs:
        if buffer and len(buffer) + len(paragraph) + 2 > max_chars:
            out.append(SourceSegment(id=f"source-{len(out) + 1:04d}", text=buffer))
            buffer = ""
        buffer = f"{buffer}\n\n{paragraph}".strip()
    if buffer:
        out.append(SourceSegment(id=f"source-{len(out) + 1:04d}", text=buffer))
    return out


LEDGER_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["scenes", "player_character_mapping", "chapter_title"],
    "properties": {
        "chapter_title": {"type": ["string", "null"]},
        "player_character_mapping": {"type": "object", "additionalProperties": {"type": "string"}},
        "scenes": {"type": "array", "minItems": 1, "items": {"type": "object"}},
    },
}


def _prompt(*, source_type: str, language: str, instructions: str, segments: list[SourceSegment]) -> str:
    return f"""Stage 1 — Semantic Source Interpreter. You convert arbitrary narrative-adjacent material into factual evidence for a chapter.

Input fields: source_type is a non-authoritative source hint; language is the requested output language; instructions are user constraints; source_segments is an ordered array of objects with immutable id and text.

Extract scenes/events, chronology, characters, player-to-character mapping, GM/player/fictional roles, resolved actions, direct versus paraphrased speech, explicit internal states, mechanics and narrative consequences, OOC/table discussion, and uncertainty. Do not infer missing motives, dialogue, locations, relationships, injuries, or facts. Current source is authoritative. Every claim must cite one or more exact source segment IDs.

Return JSON only with this complete shape:
{{"chapter_title": string|null, "player_character_mapping": {{"player": "character"}}, "scenes": [{{"scene_id":"scene-001", "title":"", "chronology":1, "narrative_weight":1, "characters":[], "facts":[{{"claim":"", "classification":"explicit|safe_implication|uncertain", "source_ids":["source-0001"]}}], "speech_acts":[], "internal_states":[], "mechanics":[], "uncertainties":[], "ooc_excluded":[]}}]}}
source_type={source_type}; language={language or 'source language'}; instructions={instructions or 'none'}
source_segments={json.dumps([segment.model_dump() for segment in segments], ensure_ascii=False)}"""


async def interpret_source(
    *, text: str, source_type: str, language: str, instructions: str, call_json: JsonCaller
) -> tuple[NarrativeEvidenceLedger, list[SourceSegment]]:
    segments = segment_source(text)
    # A compact source is interpreted once. Large sources are chunk-interpreted,
    # then reconciled in one bounded analysis call.
    if len(segments) <= 4:
        payload = await call_json(_prompt(source_type=source_type, language=language, instructions=instructions, segments=segments), LEDGER_SCHEMA, "novelist.analysis.interpret")
        ledger = NarrativeEvidenceLedger.model_validate(payload)
        return validate_provenance(ledger, segments), segments
    partials: list[dict[str, Any]] = []
    for segment in segments:
        partial = await call_json(_prompt(source_type=source_type, language=language, instructions=instructions, segments=[segment]), LEDGER_SCHEMA, "novelist.analysis.interpret_chunk")
        partials.append(partial)
    reconciliation = f"""Stage 1b — Evidence Ledger Reconciliation. Merge these partial ledgers without inventing facts. Preserve chronology and every provenance source ID. Return the exact Narrative Evidence Ledger JSON contract used below.\nsource_segments={json.dumps([item.model_dump() for item in segments], ensure_ascii=False)}\npartial_ledgers={json.dumps(partials, ensure_ascii=False)}\ncontract={json.dumps(LEDGER_SCHEMA)}"""
    payload = await call_json(reconciliation, LEDGER_SCHEMA, "novelist.analysis.reconcile")
    ledger = NarrativeEvidenceLedger.model_validate(payload)
    return validate_provenance(ledger, segments), segments
