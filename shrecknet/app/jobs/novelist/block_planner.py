"""Director-owned narrative arcs mapped to bounded sequential writing assignments."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from typing import Any

from app.jobs.novelist.story_plan import CandidateExtraction, DirectedStoryPlan, SourceSegment, resolve_ranges

MAX_UNIT_WORDS = 1400
_IMPORTANCE_WEIGHTS = {"major": 3, "supporting": 2, "transition": 1}


@dataclass(frozen=True)
class WritingBlock:
    block_id: str
    arc_id: str
    beat_ids: list[str]
    source_ids: list[str]
    target_words: int
    developments: list[dict[str, Any]]
    part_index: int = 1
    part_count: int = 1
    is_final: bool = False

    def as_prompt_dict(self) -> dict[str, Any]:
        return {"block_id": self.block_id, "arc_id": self.arc_id,
                "beat_ids": self.beat_ids, "target_words": self.target_words,
                "developments": self.developments, "part_index": self.part_index,
                "part_count": self.part_count, "is_final": self.is_final}


def chapter_word_target(segments: list[SourceSegment]) -> int:
    """Source-scale chapter budget independent of the number of extracted beats."""
    return max(700, min(6000, sum(len(s.text.split()) for s in segments) * 2))


def arc_developments(candidate: CandidateExtraction, beat_ids: list[str]) -> list[dict[str, Any]]:
    wanted = set(beat_ids)
    return [{"beat_id": beat.beat_id, "summary": beat.summary,
             "confirmed_facts": beat.confirmed_facts, "unresolved_facts": beat.unresolved_facts}
            for beat in candidate.beats if beat.beat_id in wanted]


def plan_blocks(
    plan: DirectedStoryPlan, candidate: CandidateExtraction, segments: list[SourceSegment],
    *, max_source_bytes: int | dict[str, int] = 16000, target_words: int | None = None,
) -> list[WritingBlock]:
    """Split only for source context or output limits, retaining all selected evidence.

    Subdivisions carry an explicit part index and only overlapping candidate
    developments. Shared boundary evidence remains lower authority than the unit's
    exclusive passage assignment, so writers must not re-render earlier parts.
    """
    target = target_words if target_words is not None else chapter_word_target(segments)
    if target < 1:
        raise ValueError("Chapter word target must be positive")
    weights = [_IMPORTANCE_WEIGHTS[arc.importance] for arc in plan.arcs]
    # Largest-remainder allocation preserves the exact chapter budget.
    quotas = [target * weight / sum(weights) for weight in weights]
    allocations = [math.floor(quota) for quota in quotas]
    for i in sorted(range(len(quotas)), key=lambda i: (-(quotas[i] - allocations[i]), i))[:target - sum(allocations)]:
        allocations[i] += 1
    by_id = {beat.beat_id: beat for beat in candidate.beats}
    blocks = []
    for arc, words in zip(plan.arcs, allocations):
        source_limit = max_source_bytes[arc.arc_id] if isinstance(max_source_bytes, dict) else max_source_bytes
        selections = {beat_id: {s.id for s in resolve_ranges(by_id[beat_id].source_ranges, segments)} for beat_id in arc.beat_ids}
        selected = [s for s in segments if any(s.id in ids for ids in selections.values())]
        groups: list[list[SourceSegment]] = []
        current: list[SourceSegment] = []
        size = 0
        for segment in selected:
            cost = len(json.dumps({"id": segment.id, "text": segment.text}, ensure_ascii=False).encode("utf-8")) + 2
            if cost > source_limit:
                raise ValueError("Novelist writer context cannot fit one source passage; shorten instructions/continuity or use a supported larger context")
            if current and size + cost > source_limit:
                groups.append(current)
                current, size = [], 0
            current.append(segment)
            size += cost
        if current:
            groups.append(current)
        # Output-limited arcs need real, nonoverlapping source assignments too.
        required = max(1, math.ceil(words / MAX_UNIT_WORDS))
        while len(groups) < required:
            largest = max(range(len(groups)), key=lambda i: len(groups[i]))
            group = groups[largest]
            if len(group) < 2:
                raise ValueError("Arc output budget requires subdivision but its source has no passage boundary; reduce the chapter target")
            middle = len(group) // 2
            groups[largest:largest + 1] = [group[:middle], group[middle:]]
        for part_index, group in enumerate(groups, 1):
            ids = [s.id for s in group]
            beat_ids = [beat_id for beat_id in arc.beat_ids if selections[beat_id].intersection(ids)]
            blocks.append(WritingBlock(
                block_id=f"block-{len(blocks) + 1:03d}", arc_id=arc.arc_id,
                beat_ids=beat_ids, source_ids=ids,
                target_words=words // len(groups) + (part_index <= words % len(groups)),
                developments=arc_developments(candidate, beat_ids),
                part_index=part_index, part_count=len(groups),
            ))
    if blocks:
        blocks[-1] = replace(blocks[-1], is_final=True)
    return blocks
