"""Pure provenance validation shared by Architect generation and tests."""
from __future__ import annotations
from typing import Any


def milestone_source_passage(
    milestone: dict[str, Any], scene: dict[str, Any],
) -> dict[str, Any]:
    """Keep only paragraph IDs demonstrated within the containing Scene window.

    The evidence uses one-based paragraph indices into a pinned Markdown
    revision, not reflowed text. Unknown or unsupported citations are
    explicitly downgraded to Scene-inherited/document-only references.
    """
    start, end = scene.get("start_paragraph"), scene.get("end_paragraph")
    valid_span = (
        isinstance(start, int) and not isinstance(start, bool)
        and isinstance(end, int) and not isinstance(end, bool)
        and 1 <= start <= end and end - start <= 100_000
    )
    if not valid_span:
        return {
            "start_paragraph": None,
            "end_paragraph": None,
            "source_paragraphs_absolute": [],
            "provenance_status": "document_only",
        }

    raw_scene_ids = scene.get("source_paragraphs_absolute") or []
    scene_ids = raw_scene_ids if (
        isinstance(raw_scene_ids, list)
        and raw_scene_ids == sorted(set(raw_scene_ids))
        and all(type(i) is int and start <= i <= end for i in raw_scene_ids)
    ) else []
    raw = milestone.get("source_paragraphs_absolute")
    if (
        milestone.get("provenance_status") == "architect"
        and isinstance(raw, list) and raw
        and all(type(i) is int and start <= i <= end for i in raw)
        and raw == sorted(set(raw))
        and (not scene_ids or set(raw).issubset(scene_ids))
    ):
        return {
            "start_paragraph": raw[0],
            "end_paragraph": raw[-1],
            "source_paragraphs_absolute": raw,
            "provenance_status": "architect",
        }
    return {
        "start_paragraph": start,
        "end_paragraph": end,
        "source_paragraphs_absolute": scene_ids,
        "provenance_status": "scene_inherited",
    }
