"""Shared source partitioning and validated state transitions for embodiment.

Chunks share a source-start snapshot. Reducers create a candidate state; identity
synthesis completes that state before callers publish it or advance to a source.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from app.jobs.character_agent.embody_agent import EmbodyAgent, EmbodimentGenerationError
from app.jobs.character_agent.profile import _apply_aspect_ops, _apply_goal_ops
from app.schemas.character_agent import (
    EmbodyAgentAnalysis, EmbodyAgentResult, IdentityDescription, SubtitleChangeProposal,
)
from app.schemas.character_traits import TraitProfile, TraitEvidence
from app.services.character_trait_service import merge_evidence

SCENE_ANALYSIS_CHUNK_SIZE = 5


@dataclass
class SourceState:
    profile: TraitProfile
    evidence: list[TraitEvidence]
    aspects: list[dict]
    goals: list[dict]
    subtitle: str | None
    identity: IdentityDescription | None = None


def apply_source_result(state: SourceState, result: EmbodyAgentResult) -> SourceState:
    """Build a candidate without mutating the accepted upstream state."""
    aspects, goals = copy.deepcopy(state.aspects), copy.deepcopy(state.goals)
    _apply_aspect_ops(aspects, result.aspect_updates, focused_ids=result.focused_aspects)
    _apply_goal_ops(goals, result.goal_updates, focused_ids=result.focused_goals)
    subtitle = state.subtitle
    if result.subtitle_change.operation == "set":
        subtitle = result.subtitle_change.subtitle
    elif result.subtitle_change.operation == "clear":
        subtitle = None
    return SourceState(result.trait_profile.model_copy(deep=True),
                       merge_evidence(state.evidence, result.trait_evidence),
                       aspects, goals, subtitle)


async def finalize_source_state(*, agent: EmbodyAgent, state: SourceState,
                                result: EmbodyAgentResult, canonical_identity: dict,
                                perspectives: list,
                                identity: IdentityDescription | None = None) -> SourceState:
    """Finish one source using either validated synthesis or its exact cached value."""
    candidate = apply_source_result(state, result)
    candidate.identity = identity or await agent.generate_identity_description(
        canonical_identity=canonical_identity, current_profile=candidate.profile,
        current_trait_evidence=candidate.evidence, current_aspects=candidate.aspects,
        current_goals=candidate.goals, current_perspectives=perspectives,
        source_entity_id=result.source_entity_id, source_entity_alias=result.source_entity_alias,
    )
    return candidate


def _merge_observations(target: Any, source: Any) -> Any:
    """Merge source observation lists into target in-place."""
    list_fields = [
        "recurring_behaviours", "motivations", "values", "fears",
        "conflicts", "relationships", "contradictions", "evidence_gaps", "trait_evidence",
    ]
    for field in list_fields:
        existing = list(getattr(target, field, None) or [])
        new_vals = list(getattr(source, field, None) or [])
        setattr(target, field, existing + new_vals)
    return target


def _scene_analysis_chunks(group: dict) -> list[list[dict]]:
    """Partition one source's ordered raw scenes for scene-local LLM work."""
    scenes = list(group["scenes"])
    return [
        scenes[index:index + SCENE_ANALYSIS_CHUNK_SIZE]
        for index in range(0, len(scenes), SCENE_ANALYSIS_CHUNK_SIZE)
    ]


def _merge_chunk_analyses(analyses: list[EmbodyAgentAnalysis]) -> EmbodyAgentAnalysis:
    """Merge validated scene-local chunk analyses before one profile update."""
    if not analyses:
        raise EmbodimentGenerationError("source has no scene analyses")
    first = analyses[0]
    observations = first.observations.model_copy(deep=True)
    for analysis in analyses[1:]:
        _merge_observations(observations, analysis.observations)
    subtitle = next(
        (analysis.subtitle_change for analysis in reversed(analyses)
         if analysis.subtitle_change.operation != "retain"),
        SubtitleChangeProposal(),
    )
    return first.model_copy(update={
        "scene_input_digests": {
            scene_id: digest
            for analysis in analyses for scene_id, digest in analysis.scene_input_digests.items()
        },
        "perspectives": [item for analysis in analyses for item in analysis.perspectives],
        "observations": observations,
        "subtitle_change": subtitle,
        "evidence_ids": set().union(*(analysis.evidence_ids for analysis in analyses)),
        "profile_events": [item for analysis in analyses for item in analysis.profile_events],
        "llm_calls": [item for analysis in analyses for item in analysis.llm_calls],
        "observations_unavailable": any(analysis.observations_unavailable for analysis in analyses),
    })
