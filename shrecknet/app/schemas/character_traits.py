"""Authoritative dispositional constructs, scale, and evidence boundary contracts.

The eight directional traits describe centres; STEADINESS describes comparable
within-character spread. All narrative estimates are engineering judgments.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

TraitKey = Literal['integrity', 'caution', 'presence', 'forbearance', 'diligence', 'curiosity', 'sharing', 'restlessness']
SlotKey = Literal['integrity', 'caution', 'presence', 'forbearance', 'diligence', 'curiosity', 'sharing', 'restlessness', 'steadiness']
Point = Annotated[int, Field(strict=True, ge=1, le=9)]
ANCHORS = (-1.9, -1.2, -0.7, -0.3, 0.0, 0.3, 0.7, 1.2, 1.9)
PERCENTILES = (3, 12, 24, 38, 50, 62, 76, 88, 97)
SPEC_VERSION = 'dispositions-v1'

@dataclass(frozen=True)
class TraitDefinition:
    key: str
    display_name: str
    construct: str
    definition: str
    low_pole: str
    high_pole: str
    diagnostic_situations: tuple[str, ...]
    boundary_notes: str
    kind: str = 'directional'

TRAIT_DEFINITIONS = (
    TraitDefinition('integrity', 'INTEGRITY', 'Honesty-Humility — HEXACO',
        "Will not gain at someone else's expense even when the gain is free, safe, and untraceable.",
        'Takes available advantage; accepts profitable exploitation and self-serving deception.',
        'Refuses unfair advantage or untraceable exploitation, even at personal cost.',
        ('exploitation', 'self_serving_deception'),
        'Not taking unfairly, not giving. Generosity, compassion, friendliness, and forgiveness alone are not evidence.'),
    TraitDefinition('caution', 'CAUTION', 'Emotionality — HEXACO',
        'Registers danger early and seeks security or guarantees before exposure or uncertain reliance.',
        'Physically fearless, little worry or need for reassurance; less dependent and sentimental.',
        'Threat-sensitive, anxious, seeks guarantees, protection and support; strong attachments and fear of loss.',
        ('uncertain_threat', 'uncertain_dependence', 'reliance_without_guarantees'),
        'Includes attachment and dependence, not just risk-taking. High values often seek guarantees or withdraw. Low values are not inherently virtuous courage.'),
    TraitDefinition('presence', 'PRESENCE', 'eXtraversion — HEXACO',
        'Speaks first, stands at the front, and seeks company and social visibility.',
        'Stays at the edge, avoids attention, and is drained by company.',
        'Takes the floor, joins and approaches others; socially bold and energized by company.',
        ('social_visibility', 'social_approach', 'voluntary_contact'),
        'Not dominance, leadership, warmth, kindness, or cooperation. Forbidden speech is not low presence.'),
    TraitDefinition('forbearance', 'FORBEARANCE', 'Agreeableness — HEXACO',
        'Absorbs insult, obstruction, or betrayal rather than retaliating.',
        'Retaliates, holds grudges, becomes angry quickly, and refuses to bend.',
        'Forgives, compromises, remains mild, lets provocations pass and defuses conflict.',
        ('provocation', 'betrayal', 'obstruction', 'retaliation'),
        'Response after being wronged, not general compassion or integrity. An honest character can consistently retaliate.'),
    TraitDefinition('diligence', 'DILIGENCE', 'Conscientiousness — HEXACO',
        'Finishes properly what nobody is checking.',
        'Cuts corners, improvises, abandons tasks, acts impulsively, or is disorganized.',
        'Organizes, persists, deliberates, fulfills obligations, and completes work thoroughly without supervision.',
        ('unattended_duty', 'delayed_payoff', 'cutting_corners', 'persistence'),
        'Not competence: careful work that fails can be strong high-pole evidence.'),
    TraitDefinition('curiosity', 'CURIOSITY', 'Openness — HEXACO',
        'Goes and looks at the strange thing.',
        'Prefers familiar routes and finds unfamiliar or unconventional things off-putting.',
        'Investigates, seeks information, explores unfamiliar phenomena, and enjoys unconventional ideas.',
        ('novelty', 'exploration', 'puzzle', 'unknown_information'),
        'Not intelligence or successful understanding. Known fatal danger confounds avoidance. One investigation does not establish restlessness or low caution.'),
    TraitDefinition('sharing', 'SHARING', 'Social Value Orientation — SVO',
        'When jointly controlled resources are divided, moves the allocation toward the other party, including costless giving.',
        'Keeps the larger share, maximizes own allocation, or values being ahead.',
        'Divides evenly or in the other party\'s favor; may maximize joint benefit.',
        ('resource_allocation', 'spoils', 'rewards'),
        'Giving/allocating, not refraining from exploitation. Do not automatically infer integrity.'),
    TraitDefinition('restlessness', 'RESTLESSNESS', 'Openness-to-Change vs Conservation — Schwartz values',
        'Values the new and self-chosen over the settled, traditional, and safe.',
        'Values security, order, tradition, conformity, and stability.',
        'Values novelty, autonomy, stimulation, self-direction, and change for its own sake.',
        ('value_conflict', 'recurring_value_preference'),
        'A motivational value orientation, not simple exploration. Requires explicit value choices or recurring motivated preferences.'),
    TraitDefinition('steadiness', 'STEADINESS', 'Behavioural consistency / spread of the behavioural distribution',
        'Behaves similarly when genuinely comparable circumstances recur.',
        'Broader variation around the same dispositional centres.',
        'Narrower variation; dispositions predict behavior more tightly in comparable situations.',
        (), 'A second moment, not a situation predictor, morality, calmness, or model temperature. Requires repeated comparable behavior.', 'spread'),
)
DIRECTIONAL_TRAITS = tuple(d.key for d in TRAIT_DEFINITIONS if d.kind == 'directional')
TRAIT_BY_KEY = {d.key: d for d in TRAIT_DEFINITIONS}


def point_to_z(point: int) -> float:
    if type(point) is not int or not 1 <= point <= 9:
        raise ValueError('point must be an integer from 1 through 9')
    return ANCHORS[point - 1]


def z_to_point(z: float) -> int:
    if isinstance(z, bool) or not isinstance(z, (int, float)) or not math.isfinite(z) or not -1.9 <= z <= 1.9:
        raise ValueError('z must be finite and between -1.9 and 1.9')
    return min(range(9), key=lambda i: (round(abs(ANCHORS[i] - z), 12), abs(i - 4))) + 1


def trait_metadata() -> dict[str, Any]:
    return {'version': SPEC_VERSION, 'traits': [asdict(d) for d in TRAIT_DEFINITIONS],
            'scale': [{'point': i + 1, 'z': z, 'percentile': PERCENTILES[i]} for i, z in enumerate(ANCHORS)],
            'percentile_reference': 'general human population'}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TraitEstimate(StrictModel):
    z: float | None = None
    status: Literal['unknown', 'provisional', 'supported', 'contested', 'manual'] = 'unknown'
    observation_ids: list[str] = Field(default_factory=list)
    qualifying_count: int = Field(0, ge=0)
    uncertainty: list[str] = Field(default_factory=list)
    accepted_count: int = Field(0, ge=0)
    comparison_start: int = Field(0, ge=0)

    @model_validator(mode='before')
    @classmethod
    def decode_point(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            if isinstance(value.get('z'), bool):
                raise ValueError('z cannot be boolean')
            if 'point' in value:
                supplied = value.pop('point')
                expected = z_to_point(value['z']) if value.get('z') is not None else None
                if supplied != expected or isinstance(supplied, bool):
                    raise ValueError('point must match z')
        return value

    @model_validator(mode='after')
    def valid_estimate(self):
        if self.z is not None and self.z not in ANCHORS:
            raise ValueError('stored z must be a scale anchor')
        if self.status == 'unknown' and self.z is not None:
            raise ValueError('unknown is not a midpoint')
        if self.status in ('provisional', 'supported', 'manual') and self.z is None:
            raise ValueError('estimated state requires z')
        return self

    @computed_field
    @property
    def point(self) -> int | None:
        return z_to_point(self.z) if self.z is not None else None


class TraitEdit(StrictModel):
    point: Point | None
    reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode='after')
    def nonblank(self):
        if not self.reason.strip():
            raise ValueError('manual edit requires a reason')
        return self


class TraitProfile(StrictModel):
    version: str = SPEC_VERSION
    dispositional_traits: dict[TraitKey, TraitEstimate] = Field(default_factory=lambda: {key: TraitEstimate() for key in DIRECTIONAL_TRAITS})
    steadiness: TraitEstimate = Field(default_factory=TraitEstimate)
    inferred_traits: dict[SlotKey, TraitEstimate] = Field(default_factory=dict)
    overrides: dict[SlotKey, TraitEdit] = Field(default_factory=dict)

    @model_validator(mode='after')
    def complete(self):
        if self.version != SPEC_VERSION or set(self.dispositional_traits) != set(DIRECTIONAL_TRAITS):
            raise ValueError('profile must contain the current eight directional dispositions')
        return self

    def estimate(self, key: str) -> TraitEstimate:
        return self.steadiness if key == 'steadiness' else self.dispositional_traits[key]

    def storage_json(self) -> str:
        return json.dumps(self.model_dump(mode='json', round_trip=True))


class ChoiceCondition(StrictModel):
    status: Literal['supported', 'contradicted', 'unknown']
    justification: str = Field(min_length=1)


class ChoiceConditions(StrictModel):
    knowledge: ChoiceCondition
    capability: ChoiceCondition
    options: ChoiceCondition
    freedom: ChoiceCondition


class TraitObservation(StrictModel):
    trait: TraitKey
    evidence_kind: Literal['behavior', 'authored_disposition'] = 'behavior'
    situation_type: str
    direction: Literal['low', 'midpoint', 'high']
    expression_point: Point | None
    diagnosticity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    behavior: str = Field(min_length=1)
    justification: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    available_after_scene_id: str | None = None
    conditions: ChoiceConditions
    comparison_context: str | None = Field(None, max_length=1000)

    @model_validator(mode='after')
    def diagnostic(self):
        if self.situation_type not in TRAIT_BY_KEY[self.trait].diagnostic_situations:
            raise ValueError('situation is not diagnostic of this trait')
        if self.expression_point is not None:
            direction = 'low' if self.expression_point < 5 else 'high' if self.expression_point > 5 else 'midpoint'
            if self.direction != direction:
                raise ValueError('direction and expression point disagree')
        return self


class TraitEvidence(TraitObservation):
    id: str
    source_group_id: str
    chronological_position: int = Field(ge=0)
    eligible: bool
    exclusions: list[str] = Field(default_factory=list)


class TraitProposal(StrictModel):
    trait: TraitKey
    point: Point
    observation_ids: list[str] = Field(min_length=1)
    justification: str = Field(min_length=1)
    addresses_contradictions: str = Field(min_length=1)


class TraitChange(StrictModel):
    trait: SlotKey
    previous: TraitEstimate
    current: TraitEstimate
    justification: str
    observation_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
