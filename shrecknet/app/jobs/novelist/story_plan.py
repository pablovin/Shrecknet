"""Compact, source-linked narrative plan used by the Novelist v4 pipeline."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MAX_SUMMARY_CHARS = 1000
MAX_CAST_MEMBERS = 10
MAX_NAME_CHARS = 60
MAX_TITLE_CHARS = 120
MAX_CONTINUITY_CHARS = 2000
MAX_SOURCE_IDS = 8
MAX_ID_CHARS = 32


class SourceSegment(BaseModel):
    """Bounded source text made available to analysis and writing stages."""

    model_config = ConfigDict(extra="forbid")

    id: str
    text: str


class StoryBeat(BaseModel):
    """One ordered narrative event with coarse references to its source text."""

    model_config = ConfigDict(extra="forbid")

    beat_id: str = Field(min_length=1, max_length=MAX_ID_CHARS)
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_CHARS)
    importance: Literal["major", "supporting", "transition"]
    source_ids: list[Annotated[str, Field(min_length=1, max_length=MAX_ID_CHARS)]] = Field(
        min_length=1, max_length=MAX_SOURCE_IDS
    )


class CastMember(BaseModel):
    """Explicit player-to-character association with a strict object shape."""

    model_config = ConfigDict(extra="forbid")

    player: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    character: str = Field(min_length=1, max_length=MAX_NAME_CHARS)


class NarrativeStoryPlan(BaseModel):
    """Small editorial plan passed to the prose writer."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(max_length=MAX_TITLE_CHARS)
    cast: list[CastMember] = Field(max_length=MAX_CAST_MEMBERS)
    beats: list[StoryBeat] = Field(min_length=1)
    continuity_notes: str | None = Field(max_length=MAX_CONTINUITY_CHARS)

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
        "title": {"type": ["string", "null"], "maxLength": MAX_TITLE_CHARS},
        "cast": {
            "type": "array",
            "maxItems": MAX_CAST_MEMBERS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["player", "character"],
                "properties": {
                    "player": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_NAME_CHARS,
                    },
                    "character": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_NAME_CHARS,
                    },
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
                    "beat_id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_ID_CHARS,
                    },
                    "summary": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_SUMMARY_CHARS,
                    },
                    "importance": {
                        "type": "string",
                        "enum": ["major", "supporting", "transition"],
                    },
                    "source_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_SOURCE_IDS,
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": MAX_ID_CHARS,
                        },
                    },
                },
            },
        },
        "continuity_notes": {
            "type": ["string", "null"],
            "maxLength": MAX_CONTINUITY_CHARS,
        },
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
