"""LLM-only consolidation contracts and reference binding.

This adapter owns model fields and request-local references. Profile state,
transitions, and focus eligibility are exclusively owned by profile.py reducers.
Persisted update objects and timeline contracts remain unchanged.
"""
from __future__ import annotations

import copy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.jobs.character_agent.profile import _apply_aspect_ops, _apply_goal_ops, _stable_profile_id
from app.schemas.character_agent import (
    AspectUpdateData, GoalUpdateData, CharacterAspectCategory, CharacterGoalType,
    CharacterAspectStatus, CharacterGoalStatus,
)


class ProfileReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["existing", "new"]
    index: int = Field(strict=True, ge=1)


class _EvidenceOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    justification: str = Field(min_length=1, max_length=500)
    event_references: list[str] = Field(min_length=1)

    @field_validator("justification")
    @classmethod
    def nonblank_justification(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("justification must not be blank")
        return value


class AspectAdd(_EvidenceOperation):
    operation: Literal["add"]
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(max_length=1000)
    category: CharacterAspectCategory


class GoalAdd(_EvidenceOperation):
    operation: Literal["add"]
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(max_length=1000)
    goal_type: CharacterGoalType


class AspectUpdate(_EvidenceOperation):
    operation: Literal["update"]
    target: ProfileReference
    name: str | None = Field(min_length=1, max_length=255)
    description: str | None = Field(max_length=1000)
    category: CharacterAspectCategory | None

    @model_validator(mode="after")
    def meaningful_update(self):
        if self.operation == "update" and all(getattr(self, key) is None for key in ("name", "description", "category")):
            raise ValueError("update requires at least one content field")
        return self


class AspectReinforce(AspectUpdate):
    operation: Literal["reinforce"]


class GoalUpdate(_EvidenceOperation):
    operation: Literal["update"]
    target: ProfileReference
    title: str | None = Field(min_length=1, max_length=255)
    description: str | None = Field(max_length=1000)
    goal_type: CharacterGoalType | None

    @model_validator(mode="after")
    def meaningful_update(self):
        if self.operation == "update" and all(getattr(self, key) is None for key in ("title", "description", "goal_type")):
            raise ValueError("update requires at least one content field")
        return self


class GoalReinforce(GoalUpdate):
    operation: Literal["reinforce"]


class AspectStatusChange(_EvidenceOperation):
    operation: Literal["status"]
    target: ProfileReference
    status: CharacterAspectStatus


class GoalStatusChange(_EvidenceOperation):
    operation: Literal["status"]
    target: ProfileReference
    status: CharacterGoalStatus


class PsychologicalConsolidationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # anyOf with mutually exclusive operation literals works without provider-
    # specific discriminator/oneOf support; Pydantic enforces the same variants.
    aspect_operations: list[AspectAdd | AspectUpdate | AspectReinforce | AspectStatusChange]
    goal_operations: list[GoalAdd | GoalUpdate | GoalReinforce | GoalStatusChange]
    focused_aspects: list[ProfileReference] = Field(max_length=10)
    focused_goals: list[ProfileReference] = Field(max_length=10)


class ConsolidationEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consolidation: PsychologicalConsolidationOutput


def prepare_consolidation(value: ConsolidationEnvelope, *, events: list,
                          aspects: list[dict], goals: list[dict]) -> dict:
    """Bind model references, then validate using the same reducers as replay."""
    event_by_id = {f"event-{i:03d}": (i, event) for i, event in enumerate(events, 1)}
    result = {}
    for kind, current, operations, focus, update_type, reduce in (
        ("aspect", aspects, value.consolidation.aspect_operations, value.consolidation.focused_aspects, AspectUpdateData, _apply_aspect_ops),
        ("goal", goals, value.consolidation.goal_operations, value.consolidation.focused_goals, GoalUpdateData, _apply_goal_ops),
    ):
        state = copy.deepcopy(current)
        existing_ids = [str(item["id"]) for item in current]
        new_ids: list[str] = []
        updates = []
        label_key = "name" if kind == "aspect" else "title"

        def resolve(reference: ProfileReference) -> str:
            table = existing_ids if reference.scope == "existing" else new_ids
            if reference.index > len(table):
                raise ValueError(f"{kind} reference {reference.scope}:{reference.index} is unavailable (allowed 1..{len(table)})")
            return table[reference.index - 1]

        for operation in operations:
            data = operation.model_dump(mode="json", exclude={"target", "event_references"})
            cited = []
            for reference in operation.event_references:
                if reference not in event_by_id:
                    raise ValueError(f"consolidation cited unknown event {reference}; allowed: {list(event_by_id)}")
                position, event = event_by_id[reference]
                if event.kind != kind:
                    raise ValueError(f"consolidation {kind} operation cited {event.kind} event {reference}")
                cited.append((position, event))
            data["evidence_ids"] = sorted({f"scene:{event.scene_id}" for _, event in cited})
            data["event_references"] = [position for position, _ in cited]
            label = data.get(label_key)
            if label is not None:
                if not label.strip() or (kind == "aspect" and not label.strip().casefold().startswith("i ")):
                    raise ValueError("aspect name must be a first-person defining statement" if kind == "aspect" else "goal title must not be blank")
            if operation.operation == "add":
                data["candidate_id"] = _stable_profile_id(kind, label)
                new_ids.append(data["candidate_id"])
            else:
                data["target_id"] = resolve(operation.target)
                target = next(item for item in state if str(item["id"]) == data["target_id"])
                data[label_key] = label or target[label_key]
            update = update_type.model_validate(data)
            # All state/transition rules, including collisions, live in reducers.
            reduce(state, [update])
            updates.append(update)
        focused_ids = [resolve(reference) for reference in focus]
        reduce(state, [], focused_ids=focused_ids)
        result[f"{kind}_updates"] = updates
        result[f"focused_{'aspects' if kind == 'aspect' else 'goals'}"] = focused_ids
    return result
