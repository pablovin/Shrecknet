"""Version 5 dramatic plans and exact source-passage reference contracts.

Historical V3/V4 artifacts remain JSON records and are not parsed as V5 plans.
"""
from __future__ import annotations

from typing import Any, Literal
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
