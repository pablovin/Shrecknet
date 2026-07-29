from __future__ import annotations

import asyncio
import json

from app.jobs.architect.structured_output import (
    ENTITY_EXTRACTION_RESPONSE_FORMAT,
    MILESTONE_EXTRACTION_RESPONSE_FORMAT,
    chat_with_structured_output,
)
from app.tasks.architect_analysis import (
    _architect_analysis_model,
    _extract_scene_entities,
    _run_milestone_proposal_phase,
)
from app.core.config_store import LLMModelTarget


def _scenes_payload(prompt: str) -> list[dict[str, object]]:
    marker = "Scenes payload:\n"
    payload_start = prompt.index(marker) + len(marker)
    payload, _ = json.JSONDecoder().raw_decode(prompt[payload_start:])
    return payload


class _ConcurrentSceneClient:
    def __init__(self, expected_calls: int, *, milestone: bool = False) -> None:
        self.expected_calls = expected_calls
        self.milestone = milestone
        self.calls: list[dict[str, object]] = []
        self._all_submitted = asyncio.Event()

    async def chat(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        if len(self.calls) >= self.expected_calls:
            self._all_submitted.set()
        await asyncio.wait_for(self._all_submitted.wait(), timeout=1)

        prompt = str(kwargs["messages"][0]["content"])
        scene_ref = str(_scenes_payload(prompt)[0]["scene_ref"])
        if self.milestone:
            return json.dumps(
                {
                    "scenes": [
                        {
                            "scene_ref": scene_ref,
                            "milestones": [
                                {
                                    "title": f"{scene_ref} begins",
                                    "description": "The scene begins.",
                                    "boundary_type": "begin",
                                    "adjacent_to": [],
                                    "related_to": [],
                                },
                                {
                                    "title": f"{scene_ref} ends",
                                    "description": "The scene ends.",
                                    "boundary_type": "end",
                                    "adjacent_to": [],
                                    "related_to": [],
                                },
                            ],
                        }
                    ]
                }
            )
        return json.dumps({"scenes": [{"scene_ref": scene_ref, "entities": []}]})


def test_entity_extraction_submits_every_scene_with_full_text_and_schema() -> None:
    long_texts = [f"[P1] scene-{idx} " + ("evidence " * 700) for idx in range(3)]
    scenes = [
        {
            "scene_ref": f"scene-{idx}",
            "scene_name": f"Scene {idx}",
            "scene_description": f"Description {idx}",
            "scene_text": text,
        }
        for idx, text in enumerate(long_texts)
    ]
    client = _ConcurrentSceneClient(len(scenes))

    result = asyncio.run(
        _extract_scene_entities(
            run_id="run-1",
            llm_client=client,  # type: ignore[arg-type]
            model="architect-model",
            repair_model="repair-model",
            ontology_definitions="- Person: named people",
            allowed_ontology_names={"person": "Person"},
            existing_nodes=[],
            scenes=scenes,
        )
    )

    assert [row["scene_ref"] for row in result] == [row["scene_ref"] for row in scenes]
    assert len(client.calls) == len(scenes)
    for call, expected_text in zip(client.calls, long_texts):
        assert call["model"] == "architect-model"
        assert call["response_format"] == ENTITY_EXTRACTION_RESPONSE_FORMAT
        assert expected_text in str(call["messages"][0]["content"])


def test_milestone_extraction_submits_one_full_scene_per_structured_call() -> None:
    long_texts = [f"[P1] scene-{idx} " + ("evidence " * 900) for idx in range(3)]
    scenes = [
        {
            "scene_ref": f"scene-{idx}",
            "scene_id": f"id-{idx}",
            "scene_name": f"Scene {idx}",
            "scene_description": f"Description {idx}",
            "scene_text": text,
            "related_to": [],
        }
        for idx, text in enumerate(long_texts)
    ]
    client = _ConcurrentSceneClient(len(scenes), milestone=True)

    result = asyncio.run(
        _run_milestone_proposal_phase(
            run_id="run-1",
            llm_client=client,  # type: ignore[arg-type]
            model="architect-model",
            repair_model="repair-model",
            proposed_scenes=scenes,
            author_id="architect-agent",
        )
    )

    assert [row["scene_ref"] for row in result["per_scene"]] == [
        row["scene_ref"] for row in scenes
    ]
    assert len(client.calls) == len(scenes)
    for call, expected_text in zip(client.calls, long_texts):
        assert call["model"] == "architect-model"
        assert call["response_format"] == MILESTONE_EXTRACTION_RESPONSE_FORMAT
        prompt = str(call["messages"][0]["content"])
        assert expected_text in prompt
        assert len(_scenes_payload(prompt)) == 1


def test_structured_output_retries_only_explicitly_unsupported_format() -> None:
    class _UnsupportedClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def chat(self, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append(kwargs)
            if kwargs.get("response_format"):
                raise RuntimeError("provider does not support response_format")
            return '{"scenes":[]}'

    client = _UnsupportedClient()
    response = asyncio.run(
        chat_with_structured_output(
            llm_client=client,  # type: ignore[arg-type]
            model="architect-model",
            messages=[{"role": "user", "content": "prompt"}],
            response_format=ENTITY_EXTRACTION_RESPONSE_FORMAT,
            temperature=0.1,
            usage_tag="architect.entity_extraction",
        )
    )

    assert response == '{"scenes":[]}'
    assert len(client.calls) == 2
    assert client.calls[0]["response_format"] == ENTITY_EXTRACTION_RESPONSE_FORMAT
    assert "response_format" not in client.calls[1]
    assert client.calls[1]["usage_tag"] == (
        "architect.entity_extraction.structured_fallback"
    )


def test_architect_analysis_uses_the_canonical_scene_model_target() -> None:
    class _Settings:
        model_architect_scene_chunking = LLMModelTarget(
            provider="openai",
            name="architect-model",
        )
        model_architect_entity_proposal = LLMModelTarget(
            provider="openai",
            name="deprecated-entity-model",
        )
        model_architect_milestone_proposal = LLMModelTarget(
            provider="openai",
            name="deprecated-milestone-model",
        )

    assert _architect_analysis_model(_Settings()).name == "architect-model"
