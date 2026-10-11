"""One dramatic beat per writing unit, with bounded evidence subdivisions."""
from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Any
from app.jobs.novelist.story_plan import SourceSegment, StoryBeat, resolve_ranges


@dataclass(frozen=True)
class WritingBlock:
    block_id: str
    beat_ids: list[str]
    source_ids: list[str]
    target_words: int
    is_final: bool = False

    def as_prompt_dict(self) -> dict[str, Any]:
        return {"block_id": self.block_id, "beat_ids": self.beat_ids,
                "target_words": self.target_words, "is_final": self.is_final}


_WORD_ESTIMATES = {"major": 700, "supporting": 450, "transition": 200}


def plan_blocks(beats: list[StoryBeat], segments: list[SourceSegment], *, max_source_bytes: int = 16000) -> list[WritingBlock]:
    """Transport subdivisions retain their parent's narrative identity."""
    assignments = []
    for beat in beats:
        selected = resolve_ranges(beat.source_ranges, segments)
        groups: list[list[str]] = []
        current: list[str] = []
        size = 0
        for segment in selected:
            cost = len(json.dumps(segment.model_dump(), ensure_ascii=False).encode('utf-8')) + 2
            if cost > max_source_bytes:
                raise ValueError("Novelist writer context cannot fit one source passage; increase writer context or shorten instructions/continuity")
            if current and size + cost > max_source_bytes:
                groups.append(current)
                current, size = [], 0
            current.append(segment.id)
            size += cost
        if current:
            groups.append(current)
        for group in groups:
            assignments.append((beat, group, max(150, _WORD_ESTIMATES[beat.importance] // len(groups))))
    return [WritingBlock(
        block_id=f"block-{index:03d}", beat_ids=[beat.beat_id], source_ids=ids,
        target_words=words, is_final=index == len(assignments),
    ) for index, (beat, ids, words) in enumerate(assignments, start=1)]
