import json

import pytest

from app.jobs.character_incorporation import (
    CharacterIncorporationError,
    NeutralAnswer,
    cited_ids,
    incorporate_character,
    normalize_target_language,
    render_answer,
)


class RendererLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    async def chat(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _answer() -> NeutralAnswer:
    return NeutralAnswer.model_validate({
        "claims": [
            {"id": "claim-1", "text": "One action.", "citations": ["source-1"]},
            {
                "id": "claim-2",
                "text": "One casting.",
                "citations": ["source-1", "source-2"],
            },
        ],
        "uncertainty": None,
    })


def _kwargs(llm):
    return {
        "llm_client": llm,
        "model": "fast-model",
        "original_query": "How are you, and what happened?",
        "target_language": "en",
        "agent_name": "Morgana",
        "agent_description": "An old friend and learned professor.",
        "writing_style": "Warm and direct",
        "answer": _answer(),
        "usage_tag": "test.character",
        "renderer_name": "test_character",
    }


def test_language_tags_are_normalized_conservatively():
    assert normalize_target_language("pt-br") == "pt-BR"
    assert normalize_target_language("zh-hant") == "zh-Hant"
    assert normalize_target_language("not a locale") == "und"
    assert normalize_target_language(None) == "und"


@pytest.mark.asyncio
async def test_renderer_receives_safe_claims_and_composes_in_model_order():
    llm = RendererLLM(json.dumps({
        "rendered_passages": [{
            "text": "My friend, one casting followed the decisive action.",
            "claim_ids": ["claim-2", "claim-1"],
        }]
    }))
    rendered = await incorporate_character(**_kwargs(llm))

    payload = json.loads(llm.calls[0]["messages"][1]["content"])
    assert set(payload) == {"original_query", "target_language", "agent", "claims"}
    assert payload["agent"] == {
        "name": "Morgana",
        "description": "An old friend and learned professor.",
        "writing_style": "Warm and direct",
    }
    assert all(set(claim) == {"id", "text"} for claim in payload["claims"])
    assert "source-1" not in json.dumps(payload)

    answer = render_answer(
        _answer(),
        rendered=rendered,
        citation_order=["source-1", "source-2"],
    )
    assert answer == "My friend, one casting followed the decisive action. ¹ ²"
    assert cited_ids(_answer(), rendered=rendered) == {"source-1", "source-2"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        '{"rendered_passages":[{"text":"Only one.","claim_ids":["claim-1"]}]}',
        '{"rendered_passages":[{"text":"A","claim_ids":["claim-1"]},'
        '{"text":"B","claim_ids":["claim-1","claim-2"]}]}',
        '{"rendered_passages":[{"text":"A","claim_ids":["unknown"]}]}',
        '{"rendered_passages":[{"text":"","claim_ids":["claim-1","claim-2"]}]}',
        '{"rendered_passages":[{"text":"A source-7","claim_ids":["claim-1","claim-2"]}]}',
        '{"rendered_passages":[{"text":"A","claim_ids":[]}]}',
    ],
)
async def test_invalid_renderer_contract_falls_back_to_neutral(response):
    rendered = await incorporate_character(**_kwargs(RendererLLM(response)))
    assert rendered is None
    assert render_answer(
        _answer(),
        rendered=rendered,
        citation_order=["source-1", "source-2"],
    ) == "One action. ¹\n\nOne casting. ¹ ²"


@pytest.mark.asyncio
async def test_required_renderer_repairs_invalid_composition_with_global_model():
    class RepairingLLM:
        def __init__(self):
            self.calls = []

        async def chat(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return '{"rendered_passages":[]}'
            return json.dumps({
                "rendered_passages": [{
                    "text": "Observe: one action, followed by one casting.",
                    "claim_ids": ["claim-1", "claim-2"],
                }]
            })

    llm = RepairingLLM()
    rendered = await incorporate_character(
        **_kwargs(llm),
        required=True,
        repair_model="global-repair-model",
    )

    assert rendered is not None
    assert rendered[0].claim_ids == ["claim-1", "claim-2"]
    assert len(llm.calls) == 2
    assert llm.calls[1]["model"] == "global-repair-model"
    assert llm.calls[1]["usage_tag"] == "test.character.repair"


@pytest.mark.asyncio
async def test_required_renderer_fails_after_two_invalid_calls():
    llm = RendererLLM('{"rendered_passages":[]}')

    with pytest.raises(CharacterIncorporationError, match="invalid output twice"):
        await incorporate_character(**_kwargs(llm), required=True)

    assert len(llm.calls) == 2


def test_superscript_markers_follow_source_order_and_support_double_digits():
    claims = [
        {
            "id": f"claim-{index}",
            "text": f"Fact {index}.",
            "citations": [f"source-{index}"],
        }
        for index in range(1, 11)
    ]
    answer = NeutralAnswer.model_validate({"claims": claims, "uncertainty": None})

    rendered = None
    text = render_answer(
        answer,
        rendered=rendered,
        citation_order=[f"source-{index}" for index in range(1, 11)],
    )

    assert "Fact 1. ¹" in text
    assert "Fact 10. ¹⁰" in text
    assert "{cite" not in text
    assert "[Fact" not in text
