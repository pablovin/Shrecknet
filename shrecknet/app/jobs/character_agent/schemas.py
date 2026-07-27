"""Internal contracts for the two-stage CharacterAgent query pipeline."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


TRAIT_NAMES = Literal[
    "calm_aggressive",
    "cautious_reckless",
    "compassionate_ruthless",
    "trusting_suspicious",
    "honest_deceptive",
    "patient_impulsive",
    "humble_proud",
    "cooperative_dominating",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CharacterQueryFrame(StrictModel):
    context_summary: str = Field(min_length=1, max_length=2_000)
    relevant_trait_axes: list[TRAIT_NAMES] = Field(default_factory=list)
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
