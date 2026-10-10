from __future__ import annotations

import json

import pytest
from jsonschema import validate as validate_json_schema

from app.integrations.llm.model_policy import ModelPolicy
from app.jobs.novelist.block_planner import plan_blocks
from app.jobs.novelist.orchestrator import NovelistOrchestrator
from app.jobs.novelist.prose_quality import prose_to_html, validate_prose_text
from app.jobs.novelist.source_interpreter import interpret_source, segment_source
from app.jobs.novelist.story_plan import (
    STORY_PLAN_JSON_SCHEMA,
    NarrativeStoryPlan,
    SourceSegment,
    StoryBeat,
    validate_source_references,
)
from app.schemas.novelist import NovelistRunCreate


def _plan_payload() -> dict:
    return {
        "title": "Winter Road",
        "cast": [{"player": "Pietro", "character": "Tamura"}],
        "beats": [
            {
                "beat_id": "beat-001",
                "summary": "Tamura reaches the village.",
                "importance": "major",
                "source_ids": ["source-0001"],
            }
        ],
        "continuity_notes": None,
    }


def test_story_plan_schema_requires_every_declared_field() -> None:
    assert set(STORY_PLAN_JSON_SCHEMA["required"]) == set(
        STORY_PLAN_JSON_SCHEMA["properties"]
    )
    beat_schema = STORY_PLAN_JSON_SCHEMA["properties"]["beats"]["items"]
    assert set(beat_schema["required"]) == set(beat_schema["properties"])
    cast_schema = STORY_PLAN_JSON_SCHEMA["properties"]["cast"]["items"]
    assert cast_schema["additionalProperties"] is False
    assert set(cast_schema["required"]) == set(cast_schema["properties"])
    validate_json_schema(_plan_payload(), STORY_PLAN_JSON_SCHEMA)


def test_story_plan_rejects_unknown_source_reference() -> None:
    plan = NarrativeStoryPlan.model_validate(_plan_payload())
    with pytest.raises(ValueError, match="Unknown source IDs"):
        validate_source_references(
            plan, [SourceSegment(id="source-9999", text="Other source")]
        )


def test_source_segmentation_bounds_a_single_oversized_paragraph() -> None:
    segments = segment_source("word " * 7000, max_chars=1000)
    assert len(segments) > 1
    assert all(len(segment.text) <= 1000 for segment in segments)


def test_blocks_are_ordered_bounded_and_retain_source_ids() -> None:
    beats = [
        StoryBeat(
            beat_id=f"beat-{index:03d}",
            summary=f"Event {index}",
            importance="major",
            source_ids=[f"source-{index:04d}"],
        )
        for index in range(1, 5)
    ]
    blocks = plan_blocks(beats)
    assert [beat_id for block in blocks for beat_id in block.beat_ids] == [
        beat.beat_id for beat in beats
    ]
    assert all(800 <= block.target_words <= 1200 for block in blocks)
    assert blocks[-1].is_final is True
    assert blocks[0].source_ids == ["source-0001", "source-0002"]


def test_plain_prose_checks_and_backend_html_escaping() -> None:
    prose = "First <dangerous> paragraph with enough shape.\n\nA short dramatic line."
    assert "<dangerous>" not in prose_to_html(prose)
    assert "&lt;dangerous&gt;" in prose_to_html(prose)
    assert (
        "generation was truncated"
        in validate_prose_text("word " * 300, target_words=800, finish_reason="length")[
            0
        ]
    )
    assert (
        validate_prose_text("word " * 300, target_words=800, finish_reason="stop") == []
    )


class _StructuredClient:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_analysis_sends_complete_schema_to_analysis_model_on_both_attempts() -> None:
    client = _StructuredClient(
        [
            {
                "text": '{"Pietro":"Tamura"}',
                "response_metadata": {"finish_reason": "stop"},
            },
            {
                "text": json.dumps(_plan_payload()),
                "response_metadata": {"finish_reason": "stop"},
            },
        ]
    )
    orchestrator = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())

    parsed = await orchestrator._json(
        "Source-bearing prompt.", STORY_PLAN_JSON_SCHEMA, "novelist.analysis.story_plan"
    )

    assert parsed == _plan_payload()
    assert len(client.calls) == 2
    for call in client.calls:
        assert call["response_format"]["type"] == "json_schema"
        supplied = call["response_format"]["json_schema"]["schema"]
        assert supplied == STORY_PLAN_JSON_SCHEMA
        assert call["model"] == orchestrator.analysis_model
        assert call["messages"][0]["role"] == "user"
    assert client.calls[1]["usage_tag"].endswith(".repair")


@pytest.mark.asyncio
async def test_truncated_analysis_is_retried_even_when_json_is_valid() -> None:
    client = _StructuredClient(
        [
            {
                "text": json.dumps(_plan_payload()),
                "response_metadata": {"finish_reason": "length"},
            },
            {
                "text": json.dumps(_plan_payload()),
                "response_metadata": {"finish_reason": "stop"},
            },
        ]
    )
    orchestrator = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    assert (
        await orchestrator._json("prompt", STORY_PLAN_JSON_SCHEMA, "tag")
        == _plan_payload()
    )
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_source_interpreter_passes_the_story_plan_schema() -> None:
    calls: list[tuple[dict, str]] = []

    async def call_json(prompt: str, schema: dict, tag: str) -> dict:
        assert "Every root field" in prompt
        calls.append((schema, tag))
        return _plan_payload()

    plan, segments = await interpret_source(
        text="Pietro plays Tamura. Tamura reaches the village.",
        source_type="transcript",
        language="en",
        instructions="",
        call_json=call_json,
    )
    assert plan.cast[0].model_dump() == {"player": "Pietro", "character": "Tamura"}
    assert len(segments) == 1
    assert calls == [(STORY_PLAN_JSON_SCHEMA, "novelist.analysis.story_plan")]


@pytest.mark.asyncio
async def test_long_source_uses_same_schema_for_chunks_and_reconciliation() -> None:
    calls: list[tuple[dict, str]] = []

    async def call_json(prompt: str, schema: dict, tag: str) -> dict:
        calls.append((schema, tag))
        source_id = "source-0001"
        for candidate in range(1, 7):
            value = f"source-{candidate:04d}"
            if value in prompt:
                source_id = value
                break
        payload = _plan_payload()
        payload["beats"][0]["source_ids"] = [source_id]
        return payload

    await interpret_source(
        text="\n\n".join("x" * 12000 for _ in range(5)),
        source_type="notes",
        language="en",
        instructions="",
        call_json=call_json,
    )

    assert len(calls) == 6
    assert all(schema == STORY_PLAN_JSON_SCHEMA for schema, _ in calls)
    assert [tag for _, tag in calls].count("novelist.analysis.story_plan_chunk") == 5
    assert calls[-1][1] == "novelist.analysis.story_plan_reconcile"


@pytest.mark.asyncio
async def test_writer_receives_no_json_schema_and_retries_truncation() -> None:
    client = _StructuredClient(
        [
            {"text": "word " * 300, "response_metadata": {"finish_reason": "length"}},
            {"text": "word " * 300, "response_metadata": {"finish_reason": "stop"}},
        ]
    )
    orchestrator = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    block = plan_blocks(NarrativeStoryPlan.model_validate(_plan_payload()).beats)[0]
    prose, attempts = await orchestrator._write_block(
        block=block,
        plan=NarrativeStoryPlan.model_validate(_plan_payload()),
        segments=[SourceSegment(id="source-0001", text="Tamura reaches the village.")],
        continuity={},
        payload=type("Payload", (), {"language": "en", "instructions": ""})(),
        previous_tail="",
    )
    assert prose.strip()
    assert attempts == 2
    assert all("response_format" not in call for call in client.calls)
    assert all(call["model"] == orchestrator.writer_model for call in client.calls)


@pytest.mark.asyncio
async def test_v4_workflow_renders_safe_html_without_fidelity_call() -> None:
    prose = (
        "Tamura crossed the winter road and entered the waiting village. " * 35
    ).strip()
    client = _StructuredClient(
        [
            {
                "text": json.dumps(_plan_payload()),
                "response_metadata": {"finish_reason": "stop"},
            },
            {"text": prose, "response_metadata": {"finish_reason": "stop"}},
        ]
    )
    orchestrator = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())

    result = await orchestrator.execute(
        agent=None,
        payload=NovelistRunCreate(
            unstructured_text="Pietro plays Tamura. Tamura reaches the village.",
            language="en",
            source_type="transcript",
        ),
    )

    assert result["artifacts"]["pipeline_version"] == "v4"
    assert result["fidelity_status"] == "not_run"
    assert result["final_text_html"].startswith("<h1>Winter Road</h1>\n<p>")
    assert [call["usage_tag"] for call in client.calls] == [
        "novelist.analysis.story_plan",
        "novelist.writer.section",
    ]
    assert "response_format" in client.calls[0]
    assert "response_format" not in client.calls[1]
