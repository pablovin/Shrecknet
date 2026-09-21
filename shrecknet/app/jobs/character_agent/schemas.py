"""Internal contracts for the two-stage CharacterAgent query pipeline."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


from app.schemas.character_traits import TraitKey, TRAIT_BY_KEY


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RelevantTrait(StrictModel):
    trait: TraitKey
    situation_type: str
    relevance: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def diagnostic(self):
        if self.situation_type not in TRAIT_BY_KEY[self.trait].diagnostic_situations:
            raise ValueError("trait does not govern this affordance")
        return self


class CharacterQueryFrame(StrictModel):
    context_summary: str = Field(min_length=1, max_length=2_000)
    relevant_traits: list[RelevantTrait] = Field(default_factory=list)
    relevant_aspect_ids: list[str] = Field(default_factory=list)
    relevant_goal_ids: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)

    @field_validator("context_summary")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("context_summary must not be blank")
        return normalized


class CharacterDeliberation(StrictModel):
    content: Any
    decision_basis: str = Field(min_length=1, max_length=2_000)

    @field_validator("decision_basis")
    @classmethod
    def normalize_basis(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("decision_basis must not be blank")
        return normalized
