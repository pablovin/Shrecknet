"""Typed factual boundary for the Novelist v3 pipeline.

The interpreter is the only component that turns source material into claims.  The
writer receives this ledger, never raw source text, so provenance and the
current-session-over-continuity rule remain enforceable.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SourceSegment(BaseModel):
    id: str
    text: str


class LedgerFact(BaseModel):
    claim: str = Field(min_length=1)
    classification: Literal["explicit", "safe_implication", "uncertain"]
    source_ids: list[str] = Field(min_length=1)


class LedgerScene(BaseModel):
    scene_id: str
    title: str = ""
    chronology: int = Field(ge=0)
    narrative_weight: int = Field(default=1, ge=1, le=5)
    facts: list[LedgerFact] = Field(default_factory=list)
    speech_acts: list[LedgerFact] = Field(default_factory=list)
    internal_states: list[LedgerFact] = Field(default_factory=list)
    mechanics: list[LedgerFact] = Field(default_factory=list)
    uncertainties: list[LedgerFact] = Field(default_factory=list)
    ooc_excluded: list[LedgerFact] = Field(default_factory=list)
    characters: list[str] = Field(default_factory=list)


class NarrativeEvidenceLedger(BaseModel):
    scenes: list[LedgerScene] = Field(min_length=1)
    player_character_mapping: dict[str, str] = Field(default_factory=dict)
    chapter_title: str | None = None

    @model_validator(mode="after")
    def unique_scene_ids(self) -> "NarrativeEvidenceLedger":
        ids = [scene.scene_id for scene in self.scenes]
        if len(ids) != len(set(ids)):
            raise ValueError("scene_id values must be unique")
        return self


def validate_provenance(
    ledger: NarrativeEvidenceLedger, source_segments: list[SourceSegment]
) -> NarrativeEvidenceLedger:
    """Reject a ledger claim that cannot be traced to a supplied source segment."""
    valid_ids = {segment.id for segment in source_segments}
    for scene in ledger.scenes:
        for facts in (
            scene.facts,
            scene.speech_acts,
            scene.internal_states,
            scene.mechanics,
            scene.uncertainties,
            scene.ooc_excluded,
        ):
            for fact in facts:
                unknown = set(fact.source_ids) - valid_ids
                if unknown:
                    raise ValueError(f"Unknown source IDs in ledger: {sorted(unknown)}")
    return ledger
