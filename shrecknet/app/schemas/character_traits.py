"""Authoritative dispositional constructs, scale, and evidence boundary contracts.

Eight directional points describe recurring choices; STEADINESS describes
observed cross-scene dispositional consistency. All estimates are engineering judgments.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TraitKey = Literal['integrity', 'caution', 'presence', 'forbearance', 'diligence', 'curiosity', 'sharing', 'restlessness']
SlotKey = Literal['integrity', 'caution', 'presence', 'forbearance', 'diligence', 'curiosity', 'sharing', 'restlessness', 'steadiness']
LEGACY_SPEC_VERSION = 'dispositions-v3-points-perspectives'
WEIGHTED_SPEC_VERSION = 'dispositions-v4-weighted-scenes'
SPEC_VERSION = 'dispositions-v5-neutral-prior'
PROFILE_VERSIONS = (LEGACY_SPEC_VERSION, WEIGHTED_SPEC_VERSION, SPEC_VERSION)
EVIDENCE_POLICY_VERSION = 'point-evidence-v2-weighted-scenes'

@dataclass(frozen=True)
class TraitDefinition:
    key: str
    display_name: str
    construct: str
    definition: str
    left_pole: str
    right_pole: str
    diagnostic_situations: tuple[str, ...]
    boundary_notes: str
    kind: str = 'directional'

    @property
    def compact_poles(self) -> tuple[str, str]:
        return COMPACT_POLES[self.key]

# Compact model-facing poles remain owned by the authoritative construct registry.
COMPACT_POLES = {
    'integrity': ('Exploits others for personal gain', 'Rejects unfair advantage, even at a cost'),
    'caution': ('Fearless, little need for reassurance', 'Sensitive to threats, seeks security and reassurance'),
    'presence': ('Avoids social attention and company', 'Socially bold, seeks interaction and visibility'),
    'forbearance': ('Retaliates, holds grudges', 'Forgives, compromises, tolerates provocation'),
    'diligence': ('Cuts corners, impulsive, disorganized', 'Plans, persists, fulfills obligations thoroughly'),
    'curiosity': ('Prefers familiar ideas and experiences', 'Explores unfamiliar ideas and information'),
    'sharing': ('Favors own share of resources', 'Favors fair or mutually beneficial allocation'),
    'restlessness': ('Values stability, tradition and order', 'Values autonomy, novelty and change'),
}

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
        'Expresses the same personality dispositions consistently across independent scenes.',
        'Broader variation around the same dispositional centres.',
        'Narrower variation; dispositions express the same direction across independent scenes.',
        (), 'Observed cross-scene consistency cannot distinguish unstable dispositions from stable context-sensitive behavior. Baseline 5 is a computational prior, not observed consistency.', 'spread'),
)
DIRECTIONAL_TRAITS = tuple(d.key for d in TRAIT_DEFINITIONS if d.kind == 'directional')
TRAIT_BY_KEY = {d.key: d for d in TRAIT_DEFINITIONS}


def trait_metadata() -> dict[str, Any]:
    return {'version': SPEC_VERSION, 'traits': [asdict(d) for d in TRAIT_DEFINITIONS],
            'point_range': {'minimum': 1, 'maximum': 9},
            'point_reference': '1 low pole, 5 baseline or observed mixed/centre (see status), 9 high pole',
            'situation_context': {
                'format': '<diagnostic>:<relationship>:<stakes> or unspecified',
                'relationships': ['friend', 'enemy', 'other'],
                'stakes': ['ordinary', 'high_stakes'],
                'unspecified': 'legacy context; new extraction does not classify situations',
            }}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TraitEstimate(StrictModel):
    point: int | None = Field(default=None, ge=1, le=9, strict=True)
    status: Literal['unknown', 'baseline', 'provisional', 'supported', 'manual'] = 'unknown'
    observation_count: int = Field(default=0, ge=0)
    observation_ids: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def valid_estimate(self):
        if self.observation_count != len(self.observation_ids) or len(set(self.observation_ids)) != len(self.observation_ids):
            raise ValueError('observation count must match unique perspective IDs')
        if (self.status == 'unknown') != (self.point is None):
            raise ValueError('unknown must have null point; estimated states require a point')
        if self.status == 'unknown' and self.observation_count:
            raise ValueError('unknown estimate cannot cite contributing perspectives')
        if self.status == 'baseline' and (self.point != 5 or self.observation_count):
            raise ValueError('baseline requires point 5 and no observations')
        if self.status == 'supported' and self.observation_count < 2:
            raise ValueError('supported estimate requires independent perspectives')
        return self


class TraitEdit(StrictModel):
    point: int | None = Field(ge=1, le=9, strict=True)
    reason: str = Field(min_length=1, max_length=2000)


class TraitHistoryEstimate(StrictModel):
    """Read-only inference details; never stored in the authoritative profile."""
    inferred_continuous_value: float | None = Field(default=None, ge=1, le=9)
    high_count: int | None = Field(default=None, ge=0)
    low_count: int | None = Field(default=None, ge=0)
    observation_count: int | None = Field(default=None, ge=0)
    availability: Literal['available', 'unavailable'] = 'unavailable'
    reason: str | None = None
    calculation_version: str = 'recency-points-v1'


class TraitProfile(StrictModel):
    version: str = SPEC_VERSION
    dispositional_traits: dict[TraitKey, TraitEstimate] = Field(default_factory=lambda: {key: TraitEstimate(point=5, status='baseline') for key in DIRECTIONAL_TRAITS})
    steadiness: TraitEstimate = Field(default_factory=lambda: TraitEstimate(point=5, status='baseline'))
    observation_groups: dict[str, str] = Field(default_factory=dict)
    observation_group_positions: dict[str, int] = Field(default_factory=dict)
    unresolved_observation_ids: list[str] = Field(default_factory=list)
    inferred_traits: dict[SlotKey, TraitEstimate] = Field(default_factory=dict)
    overrides: dict[SlotKey, TraitEdit] = Field(default_factory=dict)

    @model_validator(mode='before')
    @classmethod
    def legacy_defaults(cls, value):
        if isinstance(value, dict) and value.get('version') in {LEGACY_SPEC_VERSION, WEIGHTED_SPEC_VERSION}:
            value = dict(value)
            value.setdefault('dispositional_traits', {key: TraitEstimate().model_dump() for key in DIRECTIONAL_TRAITS})
            if value['version'] == LEGACY_SPEC_VERSION:
                value.setdefault('steadiness', TraitEstimate().model_dump())
        return value

    @model_validator(mode='after')
    def complete(self):
        if self.version not in PROFILE_VERSIONS or set(self.dispositional_traits) != set(DIRECTIONAL_TRAITS):
            raise ValueError('profile must contain the current eight directional dispositions')
        if self.version != SPEC_VERSION and any(value.status == 'baseline' for value in self.dispositional_traits.values()):
            raise ValueError('baseline is only valid for Steadiness')
        if self.version != SPEC_VERSION and any(value.status == 'baseline' for key, value in self.inferred_traits.items() if key != 'steadiness'):
            raise ValueError('baseline is only valid for Steadiness')
        return self

    def estimate(self, key: str) -> TraitEstimate:
        return self.steadiness if key == 'steadiness' else self.dispositional_traits[key]

    def storage_json(self) -> str:
        return json.dumps(self.model_dump(mode='json', round_trip=True))


class TraitObservation(StrictModel):
    trait: TraitKey
    evidence_kind: Literal['behavior', 'authored_disposition'] = 'behavior'
    polarity: Literal['low', 'high']
    source: Literal['canonical', 'perspective'] = 'canonical'
    observation_group_id: str | None = Field(default=None, min_length=1)
    independence_status: Literal['independent', 'unresolved'] = 'independent'
    situation_type: str = Field(default='unspecified', min_length=1)
    justification: str = Field(min_length=1)
    perspective_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def diagnostic(self):
        parts = self.situation_type.split(':')
        diagnostic = parts[0] in TRAIT_BY_KEY[self.trait].diagnostic_situations
        comparable = (len(parts) == 3 and parts[1] in {'friend', 'enemy', 'other'}
                      and parts[2] in {'ordinary', 'high_stakes'})
        if self.situation_type != 'unspecified' and not diagnostic:
            raise ValueError('situation is not diagnostic of this trait')
        if self.evidence_kind == 'behavior' and self.situation_type != 'unspecified' and not comparable:
            raise ValueError('behavioral situation requires diagnostic:relationship:stakes')
        if self.evidence_kind == 'behavior' and not self.perspective_id:
            raise ValueError('behavioral evidence requires a perspective ID')
        if self.evidence_kind == 'authored_disposition' and self.perspective_id:
            raise ValueError('authored disposition cannot claim a perspective')
        return self


class TraitEvidence(TraitObservation):
    id: str
    source_group_id: str
    chronological_position: int = Field(ge=0)
    policy_version: Literal['point-evidence-v1', 'point-evidence-v2-weighted-scenes']
    revision_id: str | None = None

    @model_validator(mode='before')
    @classmethod
    def explicit_weighted_source(cls, value):
        if (isinstance(value, dict)
                and value.get('policy_version') == EVIDENCE_POLICY_VERSION
                and 'source' not in value):
            raise ValueError('weighted evidence requires an explicit source')
        return value


class TraitChange(StrictModel):
    trait: SlotKey
    previous: TraitEstimate
    current: TraitEstimate
    justification: str
    observation_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class DecisionDisposition(StrictModel):
    """Effective ordinal direction without provenance or confidence proxies."""

    value: int | None = Field(ge=1, le=9, strict=True)
    status: Literal["unknown", "baseline", "provisional", "supported", "manual"]

    @model_validator(mode="after")
    def known_value(self):
        if (self.status == "unknown") != (self.value is None):
            raise ValueError("unknown dispositions require null values")
        if self.status == "baseline" and self.value != 5:
            raise ValueError("baseline dispositions require value 5")
        return self


class DecisionSteadiness(DecisionDisposition):
    """Aggregate spread kept distinct from the eight directional constructs."""

    status: Literal["unknown", "baseline", "provisional", "supported", "manual"]
    calculation_version: Literal["comparable-steadiness-v1", "cross-scene-steadiness-v2"] = "cross-scene-steadiness-v2"
    observation_count: int = Field(ge=0, strict=True)
    availability: Literal["available", "unavailable"]
    reason: str | None
    inferred_value: int | None = Field(ge=1, le=9, strict=True)

    @model_validator(mode="after")
    def verified_inference(self):
        if self.status == "baseline" and (self.value != 5 or self.observation_count or self.availability != "unavailable"):
            raise ValueError("baseline requires 5 without observed consistency")
        if self.availability == "available":
            if self.inferred_value is None or self.value is None or self.observation_count < 2:
                raise ValueError("available consistency requires contributing inference")
        elif self.inferred_value is not None:
            raise ValueError("unavailable consistency cannot claim inferred variation")
        return self


class DecisionPersonalityContext(StrictModel):
    """Compact service-to-job boundary; never a public decision request field."""

    version: Literal["decision-personality-v1"] = "decision-personality-v1"
    dispositions: dict[TraitKey, DecisionDisposition]
    steadiness: DecisionSteadiness

    @model_validator(mode="after")
    def complete(self):
        if set(self.dispositions) != set(DIRECTIONAL_TRAITS):
            raise ValueError("decision context requires all eight dispositions")
        return self
