"""LLM consolidation contracts and backend binding for profile evidence.

New items own their ordered lifecycle changes in this response, so only existing
items need model supplied references. The backend binds events and IDs and runs
the shared profile reducers for all materialized changes.
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


class ExistingProfileReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["existing"]
    index: int = Field(strict=True, ge=1)


# Kept as an import compatibility alias for internal callers; new references
# are deliberately impossible in the consolidation contract.
ProfileReference = ExistingProfileReference


class _Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    justification: str = Field(min_length=1, max_length=500)
    event_references: list[str] = Field(min_length=1)

    @field_validator("justification")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("justification must not be blank")
        return value


class AspectChange(_Evidence):
    operation: Literal["update", "reinforce"]
    name: str | None = Field(min_length=1, max_length=255)
    description: str | None = Field(max_length=1000)
    category: CharacterAspectCategory | None

    @model_validator(mode="after")
    def meaningful(self):
        if self.operation == "update" and all(getattr(self, k) is None for k in ("name", "description", "category")):
            raise ValueError("update requires at least one content field")
        return self


class GoalChange(_Evidence):
    operation: Literal["update", "reinforce"]
    title: str | None = Field(min_length=1, max_length=255)
    description: str | None = Field(max_length=1000)
    goal_type: CharacterGoalType | None

    @model_validator(mode="after")
    def meaningful(self):
        if self.operation == "update" and all(getattr(self, k) is None for k in ("title", "description", "goal_type")):
            raise ValueError("update requires at least one content field")
        return self


class AspectTransition(_Evidence):
    operation: Literal["status"]
    status: CharacterAspectStatus


class GoalTransition(_Evidence):
    operation: Literal["status"]
    status: CharacterGoalStatus


class NewAspect(_Evidence):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(max_length=1000)
    category: CharacterAspectCategory
    in_focus: bool
    changes: list[AspectChange | AspectTransition]


class NewGoal(_Evidence):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(max_length=1000)
    goal_type: CharacterGoalType
    in_focus: bool
    changes: list[GoalChange | GoalTransition]


class AspectUpdate(_Evidence):
    operation: Literal["update", "reinforce"]
    target: ExistingProfileReference
    name: str | None = Field(min_length=1, max_length=255)
    description: str | None = Field(max_length=1000)
    category: CharacterAspectCategory | None

    @model_validator(mode="after")
    def meaningful(self):
        if self.operation == "update" and all(getattr(self, k) is None for k in ("name", "description", "category")):
            raise ValueError("update requires at least one content field")
        return self


class GoalUpdate(_Evidence):
    operation: Literal["update", "reinforce"]
    target: ExistingProfileReference
    title: str | None = Field(min_length=1, max_length=255)
    description: str | None = Field(max_length=1000)
    goal_type: CharacterGoalType | None

    @model_validator(mode="after")
    def meaningful(self):
        if self.operation == "update" and all(getattr(self, k) is None for k in ("title", "description", "goal_type")):
            raise ValueError("update requires at least one content field")
        return self


class ExistingAspectTransition(_Evidence):
    operation: Literal["status"]
    target: ExistingProfileReference
    status: CharacterAspectStatus


class ExistingGoalTransition(_Evidence):
    operation: Literal["status"]
    target: ExistingProfileReference
    status: CharacterGoalStatus


class PsychologicalConsolidationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aspect_operations: list[AspectUpdate | ExistingAspectTransition]
    goal_operations: list[GoalUpdate | ExistingGoalTransition]
    new_aspects: list[NewAspect]
    new_goals: list[NewGoal]
    focused_aspects: list[ExistingProfileReference] = Field(max_length=10)
    focused_goals: list[ExistingProfileReference] = Field(max_length=10)


class ConsolidationEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consolidation: PsychologicalConsolidationOutput


def prepare_consolidation(value: ConsolidationEnvelope, *, events: list,
                          aspects: list[dict], goals: list[dict]) -> dict:
    """Materialize self-contained additions and validate all ordered changes."""
    event_by_id = {f"event-{i:03d}": (i, event) for i, event in enumerate(events, 1)}
    result = {}
    output = value.consolidation
    for kind, current, existing_ops, additions, focus, update_type, reduce in (
        ("aspect", aspects, output.aspect_operations, output.new_aspects, output.focused_aspects, AspectUpdateData, _apply_aspect_ops),
        ("goal", goals, output.goal_operations, output.new_goals, output.focused_goals, GoalUpdateData, _apply_goal_ops),
    ):
        state = copy.deepcopy(current)
        existing_ids = [str(item["id"]) for item in current]
        updates = []
        label_key = "name" if kind == "aspect" else "title"

        def evidence(refs: list[str], data: dict) -> None:
            cited = []
            for reference in refs:
                if reference not in event_by_id:
                    raise ValueError(f"consolidation cited unknown event {reference}; allowed: {list(event_by_id)}")
                position, event = event_by_id[reference]
                if event.kind != kind:
                    raise ValueError(f"consolidation {kind} operation cited {event.kind} event {reference}")
                cited.append((position, event))
            data["evidence_ids"] = sorted({f"scene:{event.scene_id}" for _, event in cited})
            data["event_references"] = [position for position, _ in cited]

        def materialize(data: dict, label: str):
            if not label.strip() or (kind == "aspect" and not label.strip().casefold().startswith("i ")):
                raise ValueError("aspect name must be a first-person defining statement" if kind == "aspect" else "goal title must not be blank")
            data["candidate_id"] = _stable_profile_id(kind, label)
            update = update_type.model_validate(data)
            reduce(state, [update])
            updates.append(update)
            return update

        for operation in existing_ops:
            data = operation.model_dump(mode="json", exclude={"target", "event_references"})
            evidence(operation.event_references, data)
            if operation.target.index > len(existing_ids):
                raise ValueError(f"{kind} reference existing:{operation.target.index} is unavailable (allowed 1..{len(existing_ids)})")
            data["target_id"] = existing_ids[operation.target.index - 1]
            target = next(item for item in state if str(item["id"]) == data["target_id"])
            data[label_key] = data.get(label_key) or target[label_key]
            update = update_type.model_validate(data)
            reduce(state, [update])
            updates.append(update)

        for addition in additions:
            initial = addition.model_dump(mode="json", exclude={"changes", "event_references"})
            evidence(addition.event_references, initial)
            created = materialize({"operation": "add", **initial}, initial[label_key])
            item_id = str(created.candidate_id)
            for change in addition.changes:
                data = change.model_dump(mode="json", exclude={"event_references"})
                evidence(change.event_references, data)
                data["target_id"] = item_id
                target = next(item for item in state if str(item["id"]) == item_id)
                data[label_key] = data.get(label_key) or target[label_key]
                update = update_type.model_validate(data)
                reduce(state, [update])
                updates.append(update)

        focused_ids = []
        for reference in focus:
            if reference.index > len(existing_ids):
                raise ValueError(
                    f"{kind} focus reference existing:{reference.index} is unavailable "
                    f"(allowed 1..{len(existing_ids)})"
                )
            focused_ids.append(existing_ids[reference.index - 1])
        focused_ids.extend(str(item.candidate_id) for item in updates
                           if item.operation.value == "add" and item.in_focus)
        by_id = {str(item.get("id")): item for item in state}
        focused_ids = [item_id for item_id in focused_ids
                       if item_id in by_id and by_id[item_id].get("status", "active") == "active"]
        reduce(state, [], focused_ids=focused_ids)
        result[f"{kind}_updates"] = updates
        result[f"focused_{'aspects' if kind == 'aspect' else 'goals'}"] = focused_ids
    return result
