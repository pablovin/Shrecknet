"""Deterministic adjacent-scene grouping for bounded Novelist v3 writer calls."""

from __future__ import annotations

from dataclasses import dataclass

from app.jobs.novelist.evidence_ledger import LedgerScene


@dataclass(frozen=True)
class WritingBlock:
    block_id: str
    scene_ids: list[str]
    target_words: int


def plan_blocks(scenes: list[LedgerScene], *, target_words: int = 1500) -> list[WritingBlock]:
    ordered = sorted(scenes, key=lambda scene: (scene.chronology, scene.scene_id))
    blocks: list[WritingBlock] = []
    current: list[LedgerScene] = []
    current_words = 0
    for scene in ordered:
        estimate = max(300, min(900, scene.narrative_weight * 350))
        if current and current_words + estimate > 1800:
            blocks.append(WritingBlock(f"block-{len(blocks) + 1:03d}", [row.scene_id for row in current], max(1200, min(1800, current_words))))
            current, current_words = [], 0
        current.append(scene)
        current_words += estimate
    if current:
        blocks.append(WritingBlock(f"block-{len(blocks) + 1:03d}", [row.scene_id for row in current], max(1200, min(1800, current_words or target_words))))
    return blocks
