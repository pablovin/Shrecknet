from __future__ import annotations

import json

import pytest
from novelist_test_support import candidate_payload, directed_payload, director_selection
from jsonschema import ValidationError as JSONSchemaValidationError
from jsonschema import validate as validate_json_schema
from pydantic import ValidationError

from app.integrations.llm.model_policy import ModelPolicy
from app.jobs.novelist.block_planner import plan_blocks
from app.jobs.novelist.orchestrator import NovelistOrchestrator
from app.jobs.novelist.prompts import (
    build_analysis_retry_prompt,
    build_reconciliation_prompt,
    build_story_plan_prompt,
)
from app.jobs.novelist.prose_quality import prose_to_html, validate_prose_text
from app.jobs.novelist.source_interpreter import interpret_source, segment_source
from app.jobs.novelist.story_plan import (
    STORY_PLAN_JSON_SCHEMA, CANDIDATE_JSON_SCHEMA, CandidateExtraction, DirectedStoryPlan,
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
                "source_ranges": [{"start_id": "passage-000001", "end_id": "passage-000001"}],
            }
        ],
        "continuity_notes": None,
    }


def test_story_plan_schema_requires_every_declared_field() -> None:
    assert set(STORY_PLAN_JSON_SCHEMA["required"]) == set(
        STORY_PLAN_JSON_SCHEMA["properties"]
    )
    beat_schema = STORY_PLAN_JSON_SCHEMA["$defs"]["StoryBeat"]
    assert set(beat_schema["required"]) == set(beat_schema["properties"])
    cast_schema = STORY_PLAN_JSON_SCHEMA["$defs"]["CastMember"]
    assert cast_schema["additionalProperties"] is False
    assert set(cast_schema["required"]) == set(cast_schema["properties"])
    validate_json_schema(_plan_payload(), STORY_PLAN_JSON_SCHEMA)


def test_story_plan_rejects_unknown_source_reference() -> None:
    plan = NarrativeStoryPlan.model_validate(_plan_payload())
    with pytest.raises(ValueError, match="Unknown source IDs"):
        validate_source_references(
            plan, [SourceSegment(id="passage-999999", text="Other source")]
        )


def _boundary_plan_payload() -> dict:
    return {
        "title": "t" * 120,
        "cast": [{"player": "p" * 60, "character": "c" * 60} for _ in range(10)],
        "beats": [
            {
                "beat_id": str(index).zfill(32),
                "summary": "s" * 600,
                "importance": "major",
                "source_ranges": [{"start_id": str(i).zfill(32), "end_id": str(i).zfill(32)} for i in range(8)],
            }
            for index in range(10)
        ],
        "continuity_notes": "n" * 2000,
    }


def test_story_plan_accepts_all_sizing_boundaries() -> None:
    payload = _boundary_plan_payload()
    validate_json_schema(payload, STORY_PLAN_JSON_SCHEMA)
    assert NarrativeStoryPlan.model_validate(payload).model_dump() == payload


def test_historical_story_plan_retains_unbounded_beat_count() -> None:
    payload = _plan_payload()
    payload["beats"] = [
        dict(payload["beats"][0], beat_id=f"beat-{index:03d}")
        for index in range(1, 51)
    ]
    assert "maxItems" not in STORY_PLAN_JSON_SCHEMA["properties"]["beats"]
    validate_json_schema(payload, STORY_PLAN_JSON_SCHEMA)
    plan = NarrativeStoryPlan.model_validate(payload)
    assert len(plan.beats) == 50


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("title",), "t" * 121),
        (("cast",), [{"player": "p", "character": "c"}] * 11),
        (("cast", 0, "player"), "p" * 61),
        (("cast", 0, "character"), "c" * 61),
        (("beats", 0, "summary"), "s" * 601),
        (("beats", 0, "beat_id"), "b" * 33),
        (("beats", 0, "source_ranges"), [{"start_id": "passage-000001", "end_id": "passage-000001"}] * 9),
        (("beats", 0, "source_ranges", 0, "start_id"), "s" * 33),
        (("continuity_notes",), "n" * 2001),
    ],
)
def test_story_plan_rejects_oversized_fields_in_schema_and_model(path, value) -> None:
    payload = _plan_payload()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(JSONSchemaValidationError):
        validate_json_schema(payload, STORY_PLAN_JSON_SCHEMA)
    with pytest.raises(ValidationError):
        NarrativeStoryPlan.model_validate(payload)


def test_all_analysis_prompts_include_the_compact_contract() -> None:
    segments = [SourceSegment(id="passage-000001", text="Tamura arrives.")]
    prompts = [
        build_story_plan_prompt(
            source_type="notes", language="en", instructions="", segments=segments
        ),
        build_reconciliation_prompt(segments=segments, partial_plans=[_plan_payload()]),
    ]
    for prompt in prompts:
        repair = build_analysis_retry_prompt(
            original_prompt=prompt,
            rejected="{}",
            error=None,
            schema=STORY_PLAN_JSON_SCHEMA,
        )
        for request in (prompt, repair):
            assert "hard maximum of 12 beats" in request
            assert "passage-by-passage evidence inventory" in request
            assert "1–600 characters" in request
            assert "title: null or at most 120 characters" in " ".join(request.split())
            assert "continuity_notes: null or at most 2000 characters" in " ".join(request.split())
            assert "6,000" in request


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
            source_ranges=[{"start_id": f"passage-{index:06d}", "end_id": f"passage-{index:06d}"}],
        )
        for index in range(1, 5)
    ]
    candidate = CandidateExtraction.model_validate(candidate_payload(dict(_plan_payload(), beats=[beat.model_dump() for beat in beats])))
    blocks = plan_blocks(DirectedStoryPlan.model_validate(directed_payload(candidate.model_dump())), candidate, [SourceSegment(id=f"passage-{index:06d}", text="source") for index in range(1, 5)])
    assert [beat_id for block in blocks for beat_id in block.beat_ids] == [
        beat.beat_id for beat in beats
    ]
    assert sum(block.target_words for block in blocks) == 700
    assert blocks[-1].is_final is True
    assert blocks[0].source_ids == ["passage-000001"]


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
    assert [call["max_tokens"] for call in client.calls] == [6000, 6000]


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", ["length", "max_tokens", "max_output_tokens"])
@pytest.mark.parametrize(
    "tag",
    [
        "novelist.analysis.story_plan",
        "novelist.analysis.story_plan_chunk",
        "novelist.analysis.story_plan_reconcile",
    ],
)
async def test_truncated_analysis_is_retried_even_when_json_is_valid(
    finish_reason: str, tag: str
) -> None:
    client = _StructuredClient(
        [
            {
                "text": json.dumps(_plan_payload()),
                "response_metadata": {"finish_reason": finish_reason},
            },
            {
                "text": json.dumps(_plan_payload()),
                "response_metadata": {"finish_reason": "stop"},
            },
        ]
    )
    orchestrator = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    assert (
        await orchestrator._json("prompt", STORY_PLAN_JSON_SCHEMA, tag)
        == _plan_payload()
    )
    assert len(client.calls) == 2
    assert [call["max_tokens"] for call in client.calls] == [6000, 6000]
    assert client.calls[1]["usage_tag"] == f"{tag}.repair"


@pytest.mark.asyncio
async def test_analysis_still_fails_after_truncating_the_bounded_retry() -> None:
    client = _StructuredClient(
        [
            {
                "text": '{"title": "Unfinished',
                "response_metadata": {"finish_reason": "length"},
            }
            for _ in range(2)
        ]
    )
    orchestrator = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    with pytest.raises(RuntimeError, match="failed after one retry.*truncated"):
        await orchestrator._json("prompt", STORY_PLAN_JSON_SCHEMA, "tag")
    assert len(client.calls) == 2
    assert [call["max_tokens"] for call in client.calls] == [6000, 6000]


@pytest.mark.asyncio
@pytest.mark.parametrize("repair_valid", [True, False])
async def test_analysis_repairs_provider_output_that_ignores_sizing_limits(
    repair_valid: bool,
) -> None:
    oversized = _plan_payload()
    oversized["beats"][0]["summary"] = "s" * 601
    client = _StructuredClient(
        [
            {
                "text": json.dumps(payload),
                "response_metadata": {"finish_reason": "stop"},
            }
            for payload in (oversized, _plan_payload() if repair_valid else oversized)
        ]
    )
    orchestrator = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    if repair_valid:
        assert (
            await orchestrator._json("prompt", STORY_PLAN_JSON_SCHEMA, "tag")
            == _plan_payload()
        )
    else:
        with pytest.raises(RuntimeError, match="failed after one retry"):
            await orchestrator._json("prompt", STORY_PLAN_JSON_SCHEMA, "tag")
    assert [call["max_tokens"] for call in client.calls] == [6000, 6000]
    assert all(
        call["response_format"]["json_schema"]["schema"] == STORY_PLAN_JSON_SCHEMA
        for call in client.calls
    )


@pytest.mark.asyncio
async def test_source_interpreter_passes_the_story_plan_schema() -> None:
    calls: list[tuple[dict, str]] = []

    async def call_json(prompt: str, schema: dict, tag: str, **kwargs) -> dict:
        assert "Every root field" in prompt
        calls.append((schema, tag))
        return candidate_payload(_plan_payload())

    plan, segments = await interpret_source(
        text="Pietro plays Tamura. Tamura reaches the village.",
        source_type="transcript",
        language="en",
        instructions="",
        call_json=call_json,
    )
    assert plan.cast[0].model_dump() == {"player": "Pietro", "character": "Tamura"}
    assert len(segments) == 1
    assert calls == [(CANDIDATE_JSON_SCHEMA, "novelist.analysis.story_plan")]


@pytest.mark.asyncio
async def test_writer_receives_no_json_schema_and_retries_truncation() -> None:
    client = _StructuredClient(
        [
            {"text": "word " * 300, "response_metadata": {"finish_reason": "length"}},
            {"text": "word " * 300, "response_metadata": {"finish_reason": "stop"}},
        ]
    )
    orchestrator = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    candidate = CandidateExtraction.model_validate(candidate_payload(_plan_payload()))
    directed = DirectedStoryPlan.model_validate(directed_payload(candidate.model_dump()))
    block = plan_blocks(directed, candidate, [SourceSegment(id="passage-000001", text="Tamura reaches the village.")])[0]
    prose, attempts, warnings = await orchestrator._write_block(
        block=block,
        plan=directed,
        segments=[SourceSegment(id="passage-000001", text="Tamura reaches the village.")],
        continuity={},
        payload=type("Payload", (), {"language": "en", "instructions": ""})(),
        previous_tail="",
    )
    assert prose.strip()
    assert attempts == 2
    assert warnings == []
    assert all("response_format" not in call for call in client.calls)
    assert all(call["model"] == orchestrator.writer_model for call in client.calls)


@pytest.mark.asyncio
async def test_v5_workflow_renders_safe_html_without_fidelity_call() -> None:
    prose = (
        "Tamura crossed the winter road and entered the waiting village. " * 35
    ).strip()
    client = _StructuredClient(
        [
            {
                "text": json.dumps(candidate_payload(_plan_payload())),
                "response_metadata": {"finish_reason": "stop"},
            },
            {"text": json.dumps(director_selection(directed_payload(candidate_payload(_plan_payload())))), "response_metadata": {"finish_reason": "stop"}},
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

    assert result["artifacts"]["pipeline_version"] == "v5"
    assert result["fidelity_status"] == "not_run"
    assert result["final_text_html"].startswith("<h1>Winter Road</h1>\n<p>")
    assert [call["usage_tag"] for call in client.calls] == [
        "novelist.analysis.story_plan",
        "novelist.analysis.director",
        "novelist.writer.section",
    ]
    assert "response_format" in client.calls[0]
    assert "response_format" not in client.calls[2]
