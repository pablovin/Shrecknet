"""Wire contracts for CharacterAgent dispositions; semantic metadata comes from the API."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

TraitKey = Literal['integrity', 'caution', 'presence', 'forbearance', 'diligence', 'curiosity', 'sharing', 'restlessness']
SlotKey = Literal['integrity', 'caution', 'presence', 'forbearance', 'diligence', 'curiosity', 'sharing', 'restlessness', 'steadiness']
Point = Annotated[int, Field(strict=True, ge=1, le=9)]

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class TraitEdit(StrictModel):
    point: Point | None
    reason: str = Field(min_length=1, max_length=2000)

class TraitEstimate(StrictModel):
    z: float | None
    point: Point | None
    status: Literal['unknown', 'provisional', 'supported', 'contested', 'manual']
    observation_ids: list[str] = Field(default_factory=list)
    qualifying_count: int = 0
    uncertainty: list[str] = Field(default_factory=list)
    accepted_count: int = 0
    comparison_start: int = 0

class TraitProfile(StrictModel):
    version: str
    dispositional_traits: dict[TraitKey, TraitEstimate]
    steadiness: TraitEstimate
    inferred_traits: dict[SlotKey, TraitEstimate] = Field(default_factory=dict)
    overrides: dict[SlotKey, TraitEdit] = Field(default_factory=dict)

class ChoiceCondition(StrictModel):
    status: Literal['supported', 'contradicted', 'unknown']
    justification: str

class ChoiceConditions(StrictModel):
    knowledge: ChoiceCondition
    capability: ChoiceCondition
    options: ChoiceCondition
    freedom: ChoiceCondition

class TraitEvidence(StrictModel):
    id: str
    trait: TraitKey
    evidence_kind: Literal['behavior', 'authored_disposition']
    situation_type: str
    direction: Literal['low', 'midpoint', 'high']
    expression_point: Point | None
    diagnosticity: float
    confidence: float
    behavior: str
    justification: str
    evidence_ids: list[str]
    episode_id: str
    available_after_scene_id: str | None
    conditions: ChoiceConditions
    comparison_context: str | None
    source_group_id: str
    chronological_position: int
    eligible: bool
    exclusions: list[str] = Field(default_factory=list)
