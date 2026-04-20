from __future__ import annotations

import pytest

from app.jobs.architect.scene_centric_chunking import (
    _normalize_scene_ranges,
    extract_paragraphs_from_sources,
    segment_chunk_into_scenes,
)


class _FakeLLMClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def chat(self, *, model: str, messages: list[dict[str, str]], temperature: float) -> str:
        del model, temperature
        self.prompts.append(messages[0]["content"])
        if not self._responses:
            raise RuntimeError("No fake responses left")
        return self._responses.pop(0)


def test_extract_paragraphs_groups_heading_with_next_paragraph() -> None:
    source = "<h2>TITLE</h2><p>First paragraph.</p><p>Second paragraph.</p>"
    paragraphs = extract_paragraphs_from_sources(source, None)

    assert paragraphs == ["TITLE First paragraph.", "Second paragraph."]


def test_extract_paragraphs_handles_plain_text_blocks() -> None:
    source = "Alpha line one\nAlpha line two\n\nBeta line"
    paragraphs = extract_paragraphs_from_sources(source, None)

    assert paragraphs == ["Alpha line one Alpha line two", "Beta line"]


def test_normalize_scene_ranges_accepts_zero_based_model_output() -> None:
    scenes = [
        {
            "scene_id": 0,
            "name": "Opening",
            "description": "Starts the scene",
            "start_paragraph": 0,
            "end_paragraph": 1,
        },
        {
            "scene_id": 1,
            "name": "Closing",
            "description": "Ends the scene",
            "start_paragraph": 2,
            "end_paragraph": 2,
        },
    ]

    normalized = _normalize_scene_ranges(scenes, paragraph_count=3)

    assert normalized[0]["start_paragraph"] == 1
    assert normalized[0]["end_paragraph"] == 2
    assert normalized[1]["start_paragraph"] == 3
    assert normalized[1]["end_paragraph"] == 3


def test_normalize_scene_ranges_rejects_gaps() -> None:
    scenes = [
        {
            "scene_id": 0,
            "name": "Only Scene",
            "description": "Skips middle paragraph",
            "start_paragraph": 1,
            "end_paragraph": 1,
        },
        {
            "scene_id": 1,
            "name": "Last",
            "description": "Starts too late",
            "start_paragraph": 3,
            "end_paragraph": 3,
        },
    ]

    with pytest.raises(ValueError, match="coverage"):
        _normalize_scene_ranges(scenes, paragraph_count=3)


def test_normalize_scene_ranges_repairs_gaps_in_tolerant_mode() -> None:
    scenes = [
        {
            "scene_id": 0,
            "name": "Early",
            "description": "Starts correctly",
            "start_paragraph": 1,
            "end_paragraph": 14,
        },
        {
            "scene_id": 1,
            "name": "Late",
            "description": "Starts too late and leaves a gap",
            "start_paragraph": 15,
            "end_paragraph": 18,
        },
    ]

    normalized = _normalize_scene_ranges(scenes, paragraph_count=26, strict=False)

    assert normalized[0]["start_paragraph"] == 1
    assert normalized[0]["end_paragraph"] >= 1
    assert normalized[1]["start_paragraph"] == normalized[0]["end_paragraph"] + 1
    assert normalized[-1]["end_paragraph"] == 26


@pytest.mark.asyncio
async def test_segment_chunk_into_scenes_applies_unifier_merge() -> None:
    client = _FakeLLMClient(
        responses=[
            '{"scenes":[{"scene_id":0,"name":"A","description":"A","start_paragraph":1,"end_paragraph":1},{"scene_id":1,"name":"B","description":"B","start_paragraph":2,"end_paragraph":2},{"scene_id":2,"name":"C","description":"C","start_paragraph":3,"end_paragraph":3}]}',
            '{"scenes":[{"scene_id":0,"name":"AB","description":"Merged","start_paragraph":1,"end_paragraph":2},{"scene_id":1,"name":"C","description":"C","start_paragraph":3,"end_paragraph":3}]}',
        ]
    )

    paragraphs = ["one", "two", "three"]
    scenes = await segment_chunk_into_scenes(
        llm_client=client,
        model="test-model",
        marked_paragraphs="[P1] one\n[P2] two\n[P3] three",
        paragraph_count=3,
        paragraphs=paragraphs,
    )

    assert len(scenes) == 2
    assert scenes[0]["start_paragraph"] == 1
    assert scenes[0]["end_paragraph"] == 2
    assert "[P2] two" in scenes[0]["text"]
    assert "Scenes to refine:" in client.prompts[1]


@pytest.mark.asyncio
async def test_segment_chunk_into_scenes_falls_back_when_unifier_invalid() -> None:
    client = _FakeLLMClient(
        responses=[
            '{"scenes":[{"scene_id":0,"name":"A","description":"A","start_paragraph":1,"end_paragraph":1},{"scene_id":1,"name":"B","description":"B","start_paragraph":2,"end_paragraph":3}]}',
            '{"scenes":[{"scene_id":0,"name":"Broken","description":"Bad coverage","start_paragraph":2,"end_paragraph":3}]}',
        ]
    )

    paragraphs = ["one", "two", "three"]
    scenes = await segment_chunk_into_scenes(
        llm_client=client,
        model="test-model",
        marked_paragraphs="[P1] one\n[P2] two\n[P3] three",
        paragraph_count=3,
        paragraphs=paragraphs,
    )

    assert len(scenes) == 2
    assert scenes[0]["start_paragraph"] == 1
    assert scenes[0]["end_paragraph"] == 1
    assert scenes[1]["start_paragraph"] == 2
    assert scenes[1]["end_paragraph"] == 3
