"""Wire contracts for CharacterAgent dispositions; semantic metadata comes from the API."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

TraitKey = Literal['integrity', 'caution', 'presence', 'forbearance', 'diligence', 'curiosity', 'sharing', 'restlessness']
SlotKey = Literal['integrity', 'caution', 'presence', 'forbearance', 'diligence', 'curiosity', 'sharing', 'restlessness', 'steadiness']
ZValue = Annotated[float, Field(ge=-1.9, le=1.9)]

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class TraitEdit(StrictModel):
    z: ZValue | None
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator('z', mode='before')
    @classmethod
    def reject_boolean_z(cls, value):
        if isinstance(value, bool):
            raise ValueError('z cannot be boolean')
        return value

class TraitEstimate(StrictModel):
    z: float | None
    status: Literal['unknown', 'provisional', 'supported', 'contested', 'manual']
    observation_ids: list[str] = Field(default_factory=list)
    qualifying_count: int = 0
    uncertainty: list[str] = Field(default_factory=list)
    accepted_count: int = 0
    comparison_group_count: int = 0
    required_qualifying_count: int = 0
    required_comparison_group_count: int = 0
    applied_source_ids: list[str] = Field(default_factory=list)

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
    pole: Literal['left', 'right']
    expression_z: ZValue
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

    @field_validator('expression_z', mode='before')
    @classmethod
    def reject_boolean_expression_z(cls, value):
        if isinstance(value, bool):
            raise ValueError('expression_z cannot be boolean')
        return value
