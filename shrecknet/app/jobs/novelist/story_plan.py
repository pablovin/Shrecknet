"""Version 5 dramatic plans and exact source-passage reference contracts.

Historical V3/V4 artifacts remain JSON records and are not parsed as V5 plans.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_SUMMARY_CHARS = 600
MAX_CAST_MEMBERS = 10
MAX_NAME_CHARS = 60
MAX_TITLE_CHARS = 120
MAX_CONTINUITY_CHARS = 2000
MAX_SOURCE_RANGES = 8
MAX_ID_CHARS = 32


class SourceSegment(BaseModel):
    """A reference passage, with offsets into canonical extracted source text."""
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str
    start: int = 0
    end: int = 0
    pages: list[int] = Field(default_factory=list)


class SourceRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    end_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)


class StoryBeat(BaseModel):
    """One meaningful dramatic development, potentially spanning many passages."""
    model_config = ConfigDict(extra="forbid")
    beat_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_CHARS)
    importance: Literal["major", "supporting", "transition"]
    source_ranges: list[SourceRange] = Field(min_length=1, max_length=MAX_SOURCE_RANGES)


class CastMember(BaseModel):
    model_config = ConfigDict(extra="forbid")
    player: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    character: str = Field(min_length=1, max_length=MAX_NAME_CHARS)


class NarrativeStoryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(max_length=MAX_TITLE_CHARS)
    cast: list[CastMember] = Field(max_length=MAX_CAST_MEMBERS)
    beats: list[StoryBeat] = Field(min_length=1)
    continuity_notes: str | None = Field(max_length=MAX_CONTINUITY_CHARS)

    @model_validator(mode="after")
    def validate_unique_beat_ids(self):
        ids = [beat.beat_id for beat in self.beats]
        if len(ids) != len(set(ids)):
            raise ValueError("beat_id values must be unique")
        return self


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_CHARS)
    source_ranges: list[SourceRange] = Field(min_length=1, max_length=MAX_SOURCE_RANGES)


class SourceInventory(BaseModel):
    """Factual window inventory, never a locally structured chapter plan."""
    model_config = ConfigDict(extra="forbid")
    observations: list[Observation] = Field(min_length=1)
    cast: list[CastMember] = Field(max_length=MAX_CAST_MEMBERS)


STORY_PLAN_JSON_SCHEMA: dict[str, Any] = NarrativeStoryPlan.model_json_schema()
INVENTORY_JSON_SCHEMA: dict[str, Any] = SourceInventory.model_json_schema()


def resolve_ranges(ranges: list[SourceRange], segments: list[SourceSegment]) -> list[SourceSegment]:
    positions = {segment.id: index for index, segment in enumerate(segments)}
    selected: set[int] = set()
    seen: set[tuple[str, str]] = set()
    for span in ranges:
        key = (span.start_id, span.end_id)
        if key in seen:
            raise ValueError("Duplicate source range")
        seen.add(key)
        if any(endpoint not in positions for endpoint in key):
            raise ValueError(f"Unknown source IDs in range: {key}")
        start, end = positions[span.start_id], positions[span.end_id]
        if start > end:
            raise ValueError(f"Reversed source range: {key}")
        selected.update(range(start, end + 1))
    return [segment for index, segment in enumerate(segments) if index in selected]


def validate_source_references(plan: NarrativeStoryPlan, source_segments: list[SourceSegment]) -> NarrativeStoryPlan:
    for beat in plan.beats:
        resolve_ranges(beat.source_ranges, source_segments)
    return plan


def coverage_diagnostics(plan: NarrativeStoryPlan, segments: list[SourceSegment]) -> dict[str, Any]:
    counts = dict.fromkeys((segment.id for segment in segments), 0)
    mapping = {}
    for beat in plan.beats:
        selected = resolve_ranges(beat.source_ranges, segments)
        mapping[beat.beat_id] = [segment.id for segment in selected]
        for segment in selected:
            counts[segment.id] += 1
    return {
        "beat_passages": mapping,
        "unreferenced_passages": [key for key, count in counts.items() if not count],
        "shared_passages": [key for key, count in counts.items() if count > 1],
        "semantic_coverage": "not_independently_verified",
    }


class IdentityEvidence(BaseModel):
    """Only explicit identity evidence; ambiguous people have no chosen character."""
    model_config = ConfigDict(extra="forbid")
    player: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    character: str | None = Field(min_length=1, max_length=MAX_NAME_CHARS)
    status: Literal["confirmed", "ambiguous", "gm"]

    @model_validator(mode="after")
    def validate_mapping(self):
        if self.status == "confirmed" and not (self.character and self.character.strip()):
            raise ValueError("Confirmed identities must name a character")
        if self.status != "confirmed" and self.character is not None:
            raise ValueError("Ambiguous/GM identities must have a null character")
        return self


FactText = Annotated[str, Field(min_length=1, max_length=300)]


class CandidateBeat(StoryBeat):
    material: Literal["fiction", "mixed", "gameplay"]
    confirmed_facts: list[FactText] = Field(max_length=12)
    unresolved_facts: list[FactText] = Field(max_length=12)


def validate_identity_evidence(cast: list[CastMember], identities: list[IdentityEvidence]) -> None:
    names = [identity.player.casefold() for identity in identities]
    if len(names) != len(set(names)):
        raise ValueError("Identity evidence must have unique player names")
    expected = {(identity.player, identity.character) for identity in identities if identity.status == "confirmed"}
    actual = [(member.player, member.character) for member in cast]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("Cast mappings must match exactly the confirmed identity evidence")


class CandidateExtraction(NarrativeStoryPlan):
    """Internal evidence envelope; legacy story_plan artifacts retain their shape."""
    beats: list[CandidateBeat] = Field(min_length=1)
    identities: list[IdentityEvidence] = Field(max_length=MAX_CAST_MEMBERS)

    @model_validator(mode="after")
    def validate_identities(self):
        validate_identity_evidence(self.cast, self.identities)
        return self

    def legacy_plan(self) -> NarrativeStoryPlan:
        return NarrativeStoryPlan(
            title=self.title, cast=self.cast, continuity_notes=self.continuity_notes,
            beats=[StoryBeat(**{key: getattr(beat, key) for key in StoryBeat.model_fields}) for beat in self.beats],
        )


class EvidenceObservation(Observation):
    material: Literal["fiction", "mixed", "gameplay"]
    confirmed_facts: list[FactText] = Field(max_length=12)
    unresolved_facts: list[FactText] = Field(max_length=12)


class EvidenceInventory(SourceInventory):
    observations: list[EvidenceObservation] = Field(min_length=1)
    identities: list[IdentityEvidence] = Field(max_length=MAX_CAST_MEMBERS)

    @model_validator(mode="after")
    def validate_identities(self):
        validate_identity_evidence(self.cast, self.identities)
        return self


class FactReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    beat_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    fact_index: int = Field(ge=0)


class DirectedArc(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arc_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    outline: str = Field(min_length=1, max_length=300)
    importance: Literal["major", "supporting", "transition"]
    beat_ids: list[str] = Field(min_length=1)
    confirmed_facts: list[FactReference]
    unresolved_facts: list[FactReference]


class DirectedStoryPlan(BaseModel):
    """Editorial grouping; facts and passages are inherited by reference, never rewritten."""
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(max_length=MAX_TITLE_CHARS)
    canonical_cast: list[CastMember] = Field(max_length=MAX_CAST_MEMBERS)
    identity_restrictions: list[IdentityEvidence] = Field(max_length=MAX_CAST_MEMBERS)
    arcs: list[DirectedArc] = Field(min_length=1)
    excluded_beat_ids: list[str]


CANDIDATE_JSON_SCHEMA = CandidateExtraction.model_json_schema()
EVIDENCE_INVENTORY_JSON_SCHEMA = EvidenceInventory.model_json_schema()
DIRECTED_PLAN_JSON_SCHEMA = DirectedStoryPlan.model_json_schema()


def validate_directed_plan(plan: DirectedStoryPlan, candidate: CandidateExtraction) -> DirectedStoryPlan:
    """Validate accounting and identities; this does not establish semantic fidelity."""
    positions = {beat.beat_id: i for i, beat in enumerate(candidate.beats)}
    assigned = [beat_id for arc in plan.arcs for beat_id in arc.beat_ids]
    accounted = assigned + plan.excluded_beat_ids
    if len(accounted) != len(set(accounted)) or set(accounted) != set(positions):
        raise ValueError("Every candidate beat must be assigned once or explicitly excluded")
    if len({arc.arc_id for arc in plan.arcs}) != len(plan.arcs):
        raise ValueError("arc_id values must be unique")
    if [positions[beat_id] for beat_id in assigned] != sorted(positions[beat_id] for beat_id in assigned):
        raise ValueError("Arc assignments must preserve candidate chronology")
    required = [identity for identity in candidate.identities if identity.status != "confirmed"]
    if plan.identity_restrictions != required:
        raise ValueError("Preserve every ambiguous/GM identity restriction without resolving it")
    allowed = {(identity.player, identity.character) for identity in candidate.identities if identity.status == "confirmed"}
    actual = [(member.player, member.character) for member in plan.canonical_cast]
    if len(actual) != len(set(actual)) or set(actual) != allowed:
        raise ValueError("Canonical cast must preserve exactly the confirmed mappings")
    by_id = {beat.beat_id: beat for beat in candidate.beats}
    if any(by_id[beat_id].material == "gameplay" for beat_id in assigned):
        raise ValueError("Gameplay-only beats cannot become fiction")
    if any(beat.material != "gameplay" and beat.beat_id in plan.excluded_beat_ids for beat in candidate.beats):
        raise ValueError("Preserve fictional developments, including mixed-beat consequences")
    for arc in plan.arcs:
        for field in ("confirmed_facts", "unresolved_facts"):
            expected = {(beat_id, i) for beat_id in arc.beat_ids for i, _ in enumerate(getattr(by_id[beat_id], field))}
            actual_refs = [(ref.beat_id, ref.fact_index) for ref in getattr(arc, field)]
            if len(actual_refs) != len(set(actual_refs)) or set(actual_refs) != expected:
                raise ValueError(f"Arc {arc.arc_id} must preserve every {field} reference exactly once")
    return plan


# Deliberately narrow grammar: unrecognized free-form instructions remain an LLM
# contract, rather than guessing identity constraints from arbitrary style prose.
_INSTRUCTION_NAME = r"[A-ZÀ-ÖØ-Þ][\w’'-]*(?:[ \t]+[A-ZÀ-ÖØ-Þ][\w’'-]*){0,3}"


def validate_instruction_identities(candidate: CandidateExtraction, instructions: str) -> None:
    """Enforce explicit 'Name is/plays Character' and ambiguous/GM statements.

    First names may match an extracted full player name. Conflicting or missing
    evidence is rejected inside extraction's existing repair boundary; the
    Director cannot override those user constraints by trusting a mistaken cast.
    """
    import re

    for line in instructions.splitlines():
        match = re.fullmatch(rf"\s*({_INSTRUCTION_NAME})\s+(?:is|plays)\s+(.+?)\s*", line)
        if not match:
            continue
        player, mapping = match.groups()
        mapping = re.split(r"\.\s|\s[-–—]\s", mapping, maxsplit=1)[0].rstrip('.').strip()
        role = re.sub(r"^the\s+", '', mapping, flags=re.IGNORECASE).casefold()
        if role in {'dm', 'gm', 'dungeon master', 'game master', 'narrator'}:
            status, character = 'gm', None
        else:
            choices = re.split(r"\s+(?:or|and)\s+|\s*/\s*", re.sub(r"^both\s+", '', mapping))
            if not all(re.fullmatch(_INSTRUCTION_NAME, choice) for choice in choices):
                continue
            status, character = ('confirmed', choices[0]) if len(choices) == 1 else ('ambiguous', None)
        matches = [identity for identity in candidate.identities if identity.player.casefold() == player.casefold() or identity.player.casefold().startswith(player.casefold() + ' ')]
        if len(matches) != 1 or matches[0].status != status or matches[0].character != character:
            raise ValueError(f"Identity evidence contradicts or omits explicit user mapping for {player}")
