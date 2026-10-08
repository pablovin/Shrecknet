import pytest

from app.integrations.llm.model_policy import ModelPolicy
from app.jobs.novelist.block_planner import plan_blocks
from app.jobs.novelist.evidence_ledger import LedgerFact, LedgerScene, NarrativeEvidenceLedger, SourceSegment, validate_provenance
from app.jobs.novelist.orchestrator import NovelistOrchestrator
from app.jobs.novelist.prose_quality import validate_prose_html


def _scene(number: int, weight: int = 3) -> LedgerScene:
    return LedgerScene(scene_id=f"scene-{number:03d}", chronology=number, narrative_weight=weight, facts=[LedgerFact(claim="A supported fact", classification="explicit", source_ids=["source-0001"])])


def test_ledger_rejects_claim_without_source_provenance() -> None:
    ledger = NarrativeEvidenceLedger(scenes=[_scene(1)])
    assert validate_provenance(ledger, [SourceSegment(id="source-0001", text="evidence")]) is ledger
    ledger.scenes[0].facts[0].source_ids = ["missing"]
    with pytest.raises(ValueError, match="Unknown source IDs"):
        validate_provenance(ledger, [SourceSegment(id="source-0001", text="evidence")])


def test_blocks_are_chronological_adjacent_and_bounded() -> None:
    blocks = plan_blocks([_scene(3), _scene(1), _scene(2)])
    assert [scene_id for block in blocks for scene_id in block.scene_ids] == ["scene-001", "scene-002", "scene-003"]
    assert all(1200 <= block.target_words <= 1800 for block in blocks)


def test_quality_gate_rejects_lists_repetition_and_short_paragraph_cascade() -> None:
    bad = "<p>Small.</p><p>Small.</p><p>Small.</p>\n- list item"
    errors = validate_prose_html(bad)
    assert "markdown-style list" in errors
    assert "repeated paragraphs" in errors
    assert "three consecutive very short paragraphs" in errors


def test_quality_gate_accepts_normal_html_prose() -> None:
    html = "<p>" + "word " * 45 + "</p><p>" + "other " * 48 + "</p>"
    assert validate_prose_html(html) == []


class _MalformedStructuredOutputClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("response_format"):
            return {"text": "I cannot provide that format."}
        return {"text": '{"status":"ok"}'}


@pytest.mark.asyncio
async def test_analysis_retries_malformed_native_structured_output_with_source_prompt() -> None:
    client = _MalformedStructuredOutputClient()
    orchestrator = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())

    parsed = await orchestrator._json(
        "Source-bearing prompt.",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["status"],
            "properties": {"status": {"type": "string"}},
        },
        "novelist.analysis.interpret",
    )

    assert parsed == {"status": "ok"}
    assert len(client.calls) == 2
    assert [call["messages"][0]["role"] for call in client.calls] == ["user", "user"]
    assert client.calls[0]["response_format"]["type"] == "json_schema"
    assert "response_format" not in client.calls[1]
    assert client.calls[1]["usage_tag"] == "novelist.analysis.interpret.malformed_structured_fallback"
    assert "Source-bearing prompt." in client.calls[1]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_analysis_fallback_repairs_an_unwrapped_player_character_mapping() -> None:
    class IncompleteLedgerClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def chat(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("response_format"):
                return {"text": '{"Alice":"Alicia"}'}
            return {"text": '{"chapter_title":null,"player_character_mapping":{"Alice":"Alicia"},"scenes":[{}]}'}

    client = IncompleteLedgerClient()
    orchestrator = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["chapter_title", "player_character_mapping", "scenes"],
        "properties": {
            "chapter_title": {"type": ["string", "null"]},
            "player_character_mapping": {"type": "object", "additionalProperties": {"type": "string"}},
            "scenes": {"type": "array", "minItems": 1, "items": {"type": "object"}},
        },
    }

    parsed = await orchestrator._json("Source-bearing prompt.", schema, "novelist.analysis.interpret")

    assert parsed["player_character_mapping"] == {"Alice": "Alicia"}
    fallback_prompt = client.calls[1]["messages"][0]["content"]
    assert 'Rejected response: {"Alice":"Alicia"}' in fallback_prompt
    assert "never return the value of a nested field" in fallback_prompt


@pytest.mark.asyncio
async def test_analysis_uses_targeted_repair_when_mapping_is_unwrapped_twice() -> None:
    class RepeatedUnwrappedMappingClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def chat(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) < 3:
                return {"text": '{"Alice":"Alicia"}'}
            return {"text": '{"chapter_title":null,"player_character_mapping":{"Alice":"Alicia"},"scenes":[{}]}'}

    client = RepeatedUnwrappedMappingClient()
    orchestrator = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["chapter_title", "player_character_mapping", "scenes"],
        "properties": {
            "chapter_title": {"type": ["string", "null"]},
            "player_character_mapping": {"type": "object", "additionalProperties": {"type": "string"}},
            "scenes": {"type": "array", "minItems": 1, "items": {"type": "object"}},
        },
    }

    parsed = await orchestrator._json("Source-bearing prompt.", schema, "novelist.analysis.interpret")

    assert parsed["player_character_mapping"] == {"Alice": "Alicia"}
    assert len(client.calls) == 3
    assert client.calls[2]["usage_tag"] == "novelist.analysis.interpret.unwrapped_mapping_repair"
    assert "include at least one source-backed scene" in client.calls[2]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_writer_submits_the_narrative_prompt_as_a_user_turn() -> None:
    class WriterClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def chat(self, **kwargs):
            self.calls.append(kwargs)
            return {"text": "<p>Rendered prose.</p>"}

    client = WriterClient()
    orchestrator = NovelistOrchestrator(llm_client=client, model_policy=ModelPolicy())

    result = await orchestrator._write("Render this evidence.", usage_tag="novelist.writer.block")

    assert result == "<p>Rendered prose.</p>"
    assert client.calls[0]["messages"] == [
        {"role": "user", "content": "Render this evidence."},
    ]
