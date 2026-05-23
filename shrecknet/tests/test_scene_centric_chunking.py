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

    async def chat(self, *, model: str, messages: list[dict[str, str]], temperature: float, **kwargs: object) -> str:
        del model, temperature, kwargs
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


def test_normalize_scene_ranges_preserves_gaps_without_repair() -> None:
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

    normalized = _normalize_scene_ranges(scenes, paragraph_count=3)
    assert normalized[0]["start_paragraph"] == 1
    assert normalized[0]["end_paragraph"] == 1
    assert normalized[1]["start_paragraph"] == 3
    assert normalized[1]["end_paragraph"] == 3


def test_normalize_scene_ranges_does_not_repair_gaps() -> None:
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

    normalized = _normalize_scene_ranges(scenes, paragraph_count=26)
    assert normalized[0]["start_paragraph"] == 1
    assert normalized[0]["end_paragraph"] == 14
    assert normalized[1]["start_paragraph"] == 15
    assert normalized[1]["end_paragraph"] == 18


@pytest.mark.asyncio
async def test_segment_chunk_into_scenes_preserves_out_of_bounds_without_annotation() -> None:
    client = _FakeLLMClient(
        responses=[
            '{"scenes":[{"scene_id":0,"name":"A","description":"A desc","start_paragraph":57,"end_paragraph":100}]}',
        ]
    )
    scenes = await segment_chunk_into_scenes(
        llm_client=client,
        model="test-model",
        marked_paragraphs="[P1] one\\n[P2] two\\n[P3] three",
        paragraph_count=3,
        paragraphs=["one", "two", "three"],
    )
    assert len(scenes) == 1
    assert scenes[0]["start_paragraph"] == 57
    assert scenes[0]["end_paragraph"] == 100
    assert scenes[0]["description"] == "A desc"
    assert scenes[0]["text"] == "[P3] three"


@pytest.mark.asyncio
async def test_segment_chunk_into_scenes_uses_single_scene_only_call() -> None:
    client = _FakeLLMClient(
        responses=[
            '{"scenes":[{"scene_id":0,"name":"A","description":"A","start_paragraph":1,"end_paragraph":2},{"scene_id":1,"name":"B","description":"B","start_paragraph":3,"end_paragraph":3}]}',
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
    assert "milestones" not in scenes[0]
    assert "extract graph-worthy milestones" not in client.prompts[0].lower()


@pytest.mark.asyncio
async def test_segment_chunk_into_scenes_discards_milestone_fields() -> None:
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
    assert "milestones" not in scenes[0]
    assert "milestones" not in scenes[1]
