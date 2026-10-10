"""Deterministic adjacent-beat grouping for bounded Novelist v4 writer calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.jobs.novelist.story_plan import StoryBeat


@dataclass(frozen=True)
class WritingBlock:
    block_id: str
    beat_ids: list[str]
    source_ids: list[str]
    target_words: int
    is_final: bool = False

    def as_prompt_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "beat_ids": self.beat_ids,
            "target_words": self.target_words,
            "is_final": self.is_final,
        }


_WORD_ESTIMATES = {"major": 500, "supporting": 300, "transition": 150}


def plan_blocks(beats: list[StoryBeat]) -> list[WritingBlock]:
    """Group ordered beats into sections targeting 800–1,200 prose words."""

    grouped: list[tuple[list[StoryBeat], int]] = []
    current: list[StoryBeat] = []
    estimated_words = 0
    for beat in beats:
        estimate = _WORD_ESTIMATES[beat.importance]
        if current and estimated_words + estimate > 1200:
            grouped.append((current, estimated_words))
            current, estimated_words = [], 0
        current.append(beat)
        estimated_words += estimate
    if current:
        grouped.append((current, estimated_words))

    blocks: list[WritingBlock] = []
    for index, (rows, estimate) in enumerate(grouped, start=1):
        source_ids = list(
            dict.fromkeys(source_id for row in rows for source_id in row.source_ids)
        )
        blocks.append(
            WritingBlock(
                block_id=f"block-{index:03d}",
                beat_ids=[row.beat_id for row in rows],
                source_ids=source_ids,
                target_words=max(800, min(1200, estimate)),
                is_final=index == len(grouped),
            )
        )
    return blocks
