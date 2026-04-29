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


def test_extract_paragraphs_fallback_splits_transcript_markers() -> None:
    source = (
        "● Recap of plot thread\n"
        "wrapped continuation line\n"
        "still same bullet\n"
        "● Next event marker (00:15:36)\n"
        "more detail on next event\n"
        "2. Numbered beat starts here\n"
        "continues here"
    )

    paragraphs = extract_paragraphs_from_sources(source, None)

    assert len(paragraphs) == 3
    assert paragraphs[0].startswith("● Recap of plot thread wrapped continuation line")
    assert "(00:15:36)" in paragraphs[1]
    assert paragraphs[2].startswith("2. Numbered beat starts here continues here")


def test_extract_paragraphs_fallback_does_not_over_split_plain_wrapped_text() -> None:
    source = (
        "This is a wrapped prose paragraph line one\n"
        "line two with no bullet or timestamp markers\n"
        "line three continues the same thought"
    )

    paragraphs = extract_paragraphs_from_sources(source, None)

    assert paragraphs == [
        (
            "This is a wrapped prose paragraph line one line two with no bullet or "
            "timestamp markers line three continues the same thought"
        )
    ]


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
async def test_segment_chunk_into_scenes_returns_scene_milestones_in_single_call() -> None:
    client = _FakeLLMClient(
        responses=[
            '{"scenes":[{"scene_id":0,"name":"A","description":"A","start_paragraph":1,"end_paragraph":2,"milestones":[{"title":"Open","description":"Opens","boundary_type":"begin"},{"title":"Close","description":"Closes","boundary_type":"end"}]},{"scene_id":1,"name":"B","description":"B","start_paragraph":3,"end_paragraph":3,"milestones":[{"title":"Turn","description":"Turns","boundary_type":"begin"},{"title":"End","description":"Ends","boundary_type":"end"}]}]}',
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
    assert len(client.prompts) == 1
    assert len(scenes[0]["milestones"]) == 2


@pytest.mark.asyncio
async def test_segment_chunk_into_scenes_scene_without_milestones_defaults_to_empty() -> None:
    client = _FakeLLMClient(
        responses=[
            '{"scenes":[{"scene_id":0,"name":"A","description":"A","start_paragraph":1,"end_paragraph":1},{"scene_id":1,"name":"B","description":"B","start_paragraph":2,"end_paragraph":3,"milestones":[{"title":"Close","description":"Closes","boundary_type":"end"}]}]}',
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
    assert scenes[0]["milestones"] == []
    assert len(scenes[1]["milestones"]) == 1
