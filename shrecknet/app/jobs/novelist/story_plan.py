"""Compact, source-linked narrative plan used by the Novelist v4 pipeline."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceSegment(BaseModel):
    """Bounded source text made available to analysis and writing stages."""

    model_config = ConfigDict(extra="forbid")

    id: str
    text: str


class StoryBeat(BaseModel):
    """One ordered narrative event with coarse references to its source text."""

    model_config = ConfigDict(extra="forbid")

    beat_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    importance: Literal["major", "supporting", "transition"]
    source_ids: list[str] = Field(min_length=1)


class CastMember(BaseModel):
    """Explicit player-to-character association with a strict object shape."""

    model_config = ConfigDict(extra="forbid")

    player: str = Field(min_length=1)
    character: str = Field(min_length=1)


class NarrativeStoryPlan(BaseModel):
    """Small editorial plan passed to the prose writer."""

    model_config = ConfigDict(extra="forbid")

    title: str | None
    cast: list[CastMember]
    beats: list[StoryBeat] = Field(min_length=1)
    continuity_notes: str | None

    @model_validator(mode="after")
    def validate_unique_beat_ids(self) -> "NarrativeStoryPlan":
        ids = [beat.beat_id for beat in self.beats]
        if len(ids) != len(set(ids)):
            raise ValueError("beat_id values must be unique")
        return self


# Strict structured-output providers require every object property to be listed
# in `required`, including nullable properties. Keep this synchronized with the
# Pydantic models above and the human-readable contract in prompts.py.
STORY_PLAN_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "cast", "beats", "continuity_notes"],
    "properties": {
        "title": {"type": ["string", "null"]},
        "cast": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["player", "character"],
                "properties": {
                    "player": {"type": "string", "minLength": 1},
                    "character": {"type": "string", "minLength": 1},
                },
            },
        },
        "beats": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["beat_id", "summary", "importance", "source_ids"],
                "properties": {
                    "beat_id": {"type": "string", "minLength": 1},
                    "summary": {"type": "string", "minLength": 1},
                    "importance": {
                        "type": "string",
                        "enum": ["major", "supporting", "transition"],
                    },
                    "source_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "minLength": 1},
                    },
                },
            },
        },
        "continuity_notes": {"type": ["string", "null"]},
    },
}


def validate_source_references(
    plan: NarrativeStoryPlan, source_segments: list[SourceSegment]
) -> NarrativeStoryPlan:
    """Reject plans whose coarse references do not identify supplied source."""

    valid_ids = {segment.id for segment in source_segments}
    for beat in plan.beats:
        unknown = set(beat.source_ids) - valid_ids
        if unknown:
            raise ValueError(f"Unknown source IDs in story plan: {sorted(unknown)}")
    return plan
