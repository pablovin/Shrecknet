"""Point-based wire contracts for CharacterAgent dispositions."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

TraitKey = Literal['integrity', 'caution', 'presence', 'forbearance', 'diligence', 'curiosity', 'sharing', 'restlessness']
SlotKey = Literal['integrity', 'caution', 'presence', 'forbearance', 'diligence', 'curiosity', 'sharing', 'restlessness', 'steadiness']

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class TraitEdit(StrictModel):
    point: int | None = Field(ge=1, le=9, strict=True)
    reason: str = Field(min_length=1, max_length=2000)

class TraitEstimate(StrictModel):
    point: int | None = Field(ge=1, le=9, strict=True)
    status: Literal['unknown', 'provisional', 'supported', 'manual']
    observation_count: int = Field(ge=0)
    observation_ids: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def valid_estimate(self):
        if self.observation_count != len(self.observation_ids) or len(set(self.observation_ids)) != len(self.observation_ids):
            raise ValueError('observation count must match unique perspective IDs')
        if (self.status == 'unknown') != (self.point is None):
            raise ValueError('unknown must have null point')
        if self.status == 'unknown' and self.observation_count:
            raise ValueError('unknown estimate cannot cite contributing perspectives')
        if self.status == 'supported' and self.observation_count < 2:
            raise ValueError('supported estimate requires independent perspectives')
        return self

class TraitProfile(StrictModel):
    version: Literal['dispositions-v3-points-perspectives']
    dispositional_traits: dict[TraitKey, TraitEstimate]
    steadiness: TraitEstimate
    inferred_traits: dict[SlotKey, TraitEstimate] = Field(default_factory=dict)
    overrides: dict[SlotKey, TraitEdit] = Field(default_factory=dict)

    @model_validator(mode='after')
    def complete(self):
        if set(self.dispositional_traits) != {
            'integrity', 'caution', 'presence', 'forbearance',
            'diligence', 'curiosity', 'sharing', 'restlessness',
        }:
            raise ValueError('profile requires all eight directional traits')
        return self

class TraitEvidence(StrictModel):
    id: str
    trait: TraitKey
    evidence_kind: Literal['behavior', 'authored_disposition']
    situation_type: str
    polarity: Literal['low', 'high']
    justification: str
    perspective_id: str | None
    evidence_ids: list[str]
    source_group_id: str
    chronological_position: int
    policy_version: str
    revision_id: str | None = None
