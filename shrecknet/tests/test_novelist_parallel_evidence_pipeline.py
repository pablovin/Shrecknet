from __future__ import annotations

import asyncio
import json

import pytest

from app.jobs.novelist.novelist import NovelistOrchestrator
from app.jobs.novelist.structured_output import (
    CONTEXT_RESPONSE_FORMAT,
    CRITIC_RESPONSE_FORMAT,
    RETRIEVAL_QUESTIONS_RESPONSE_FORMAT,
)
from app.models.agent import Agent


class _ModelPolicy:
    model_novelist_planning = "planning-model"
    model_novelist_prose = "prose-model"
    model_novelist_critic = "critic-model"
    model_novelist_chapter_writer = "chapter-writer-model"
    model_agents_repair_json = "repair-model"

    def get_model(self, _task):  # type: ignore[no-untyped-def]
        return "fallback-model"


class _RecordingLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def chat(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        system = str(kwargs["messages"][0]["content"])
        if "planning retrieval questions" in system:
            text = '{"questions":["What happened?","What remains unresolved?"]}'
        elif "synthesizing graph-retrieved context" in system:
            text = json.dumps(
                {
                    "prior_events": "Earlier events.",
                    "relationship_summaries": "Relationships.",
                    "personality_reminders": "Personalities.",
                    "unresolved_tensions": "Tensions.",
                    "style_details": "Style.",
                    "contradiction_warnings": "None.",
                }
            )
        elif "literary critic" in system:
            text = '{"global_notes":[],"scenes":[]}'
        else:
            text = "<h1>Scene</h1><p>Final prose.</p>"
        if kwargs.get("return_metadata"):
            return {"text": text, "usage": {}, "response_metadata": {}}
        return text


@pytest.mark.asyncio
async def test_planning_context_and_prose_receive_complete_raw_evidence() -> None:
    llm = _RecordingLLM()
    orchestrator = NovelistOrchestrator(
        llm_client=llm,  # type: ignore[arg-type]
        model_policy=_ModelPolicy(),  # type: ignore[arg-type]
    )
    raw_text = "[P1] " + ("complete evidence " * 1000)
    chunk = {
        "scene_id": "scene-1",
        "name": "Scene One",
        "scene_summary": "Summary",
        "source_rawtext": raw_text,
    }

    questions = await orchestrator._plan_retrieval_questions_for_chunk(
        chunk=chunk,
        language="English",
        instructions="",
        conversation_id="run:step2:scene-1",
    )
    context = await orchestrator._build_chunk_context_v2(
        chunk=chunk,
        retrieval={"questions_answers": []},
        language="English",
        instructions="",
        conversation_id="run:step4_5:scene-1",
    )
    await orchestrator._generate_merged_chunk_draft_v2(
        chunk={**chunk, "v2_context": context},
        language="English",
        instructions="",
        conversation_id="run:step4_5:scene-1",
    )

    assert len(questions) == 2
    assert llm.calls[0]["response_format"] == RETRIEVAL_QUESTIONS_RESPONSE_FORMAT
    assert llm.calls[1]["response_format"] == CONTEXT_RESPONSE_FORMAT
    assert llm.calls[0]["use_conversation_memory"] is False
    for call in llm.calls[:3]:
        assert raw_text in str(call["messages"][-1]["content"])


@pytest.mark.asyncio
async def test_elder_questions_are_all_submitted_before_any_complete() -> None:
    expected_calls = 6
    submitted = 0
    all_submitted = asyncio.Event()

    async def elder_runner(_agent: Agent, query: str) -> list[dict[str, str]]:
        nonlocal submitted
        submitted += 1
        if submitted == expected_calls:
            all_submitted.set()
        await asyncio.wait_for(all_submitted.wait(), timeout=1)
        return [{"text": f"Answer for {query}"}]

    orchestrator = NovelistOrchestrator(
        llm_client=_RecordingLLM(),  # type: ignore[arg-type]
        model_policy=_ModelPolicy(),  # type: ignore[arg-type]
        max_concurrency=1,
        elder_query_concurrency=1,
        elder_query_runner=elder_runner,
    )
    scenes = [
        {
            "scene_id": f"scene-{index}",
            "name": f"Scene {index}",
            "prior_knowledge_needed": [
                {"question": f"Question {index}-{question}", "answer": ""}
                for question in range(3)
            ],
        }
        for index in range(2)
    ]

    _, retrieval, traces = await orchestrator._collect_scene_retrieval(
        agent=Agent(id="agent-1", name="Novelist", job="novelist", active=True),
        scene_packages=scenes,
        language="English",
        instructions="",
        conversation_id="run",
    )

    assert submitted == expected_calls
    assert len(traces) == expected_calls
    assert all(len(row["questions_answers"]) == 3 for row in retrieval.values())


@pytest.mark.asyncio
async def test_scene_bundles_run_in_parallel_and_retry_with_fresh_conversation() -> None:
    class _ParallelOrchestrator(NovelistOrchestrator):
        def __init__(self) -> None:
            super().__init__(
                llm_client=_RecordingLLM(),  # type: ignore[arg-type]
                model_policy=_ModelPolicy(),  # type: ignore[arg-type]
            )
            self.context_calls: list[tuple[str, str]] = []
            self._first_wave = asyncio.Event()

        async def _plan_retrieval_questions_for_chunk(self, **kwargs):  # type: ignore[no-untyped-def]
            return ["Question one?", "Question two?"]

        async def _collect_scene_retrieval(self, **kwargs):  # type: ignore[no-untyped-def]
            scenes = kwargs["scene_packages"]
            return scenes, {
                str(scene["scene_id"]): {"questions_answers": []}
                for scene in scenes
            }, []

        async def _build_chunk_context_v2(self, **kwargs):  # type: ignore[no-untyped-def]
            scene_id = str(kwargs["chunk"]["scene_id"])
            conversation_id = str(kwargs["conversation_id"])
            self.context_calls.append((scene_id, conversation_id))
            first_attempts = [
                row for row in self.context_calls if ":retry-" not in row[1]
            ]
            if len(first_attempts) == 3:
                self._first_wave.set()
            await asyncio.wait_for(self._first_wave.wait(), timeout=1)
            if scene_id == "scene-1" and ":retry-" not in conversation_id:
                raise RuntimeError("first attempt fails")
            return {}

        async def _generate_merged_chunk_draft_v2(self, **kwargs):  # type: ignore[no-untyped-def]
            return f"<p>{kwargs['chunk']['scene_id']}</p>"

        async def _critic_scene_set(self, **kwargs):  # type: ignore[no-untyped-def]
            return {"global_notes": [], "by_scene": {}}

        async def _revise_scene_set_v2(self, **kwargs):  # type: ignore[no-untyped-def]
            return {"final_text_html": "<p>Final</p>"}

    orchestrator = _ParallelOrchestrator()
    scenes = [
        {
            "scene_id": f"scene-{index}",
            "name": f"Scene {index}",
            "scene_summary": "Summary",
            "source_rawtext": "Raw",
        }
        for index in range(3)
    ]
    result = await orchestrator._execute_v2(
        agent=Agent(id="agent-1", name="Novelist", job="novelist", active=True),
        step_1_scenes=scenes,
        language="English",
        instructions="",
        conversation_id="run",
        stage_callback=None,
        artifacts={"stages": {}, "timings_ms": {}},
        started_total=0.0,
    )

    assert [row["scene_id"] for row in result["scene_results"]] == [
        "scene-0",
        "scene-1",
        "scene-2",
    ]
    retry_ids = [
        conversation
        for scene, conversation in orchestrator.context_calls
        if scene == "scene-1" and ":retry-" in conversation
    ]
    assert retry_ids == ["run:step4_5:scene-1:retry-1"]
    assert result["artifacts"]["llm_call_summary"]["estimated_v2_calls"] == 11


@pytest.mark.asyncio
async def test_critic_uses_schema_and_only_revision_requests_15000_tokens() -> None:
    llm = _RecordingLLM()
    orchestrator = NovelistOrchestrator(
        llm_client=llm,  # type: ignore[arg-type]
        model_policy=_ModelPolicy(),  # type: ignore[arg-type]
    )
    await orchestrator._critic_scene_set(
        scene_packages=[{"scene_id": "s1", "name": "Scene One"}],
        prose_by_scene=[{"scene_id": "s1", "name": "Scene One", "prose_html": "<p>Draft</p>"}],
        language="English",
        instructions="",
        conversation_id="run:step6_7",
    )
    await orchestrator._revise_scene_set_v2(
        prose_by_scene=[{"scene_id": "s1", "name": "Scene One", "prose_html": "<p>Draft</p>"}],
        critic={"global_notes": [], "by_scene": {}},
        language="English",
        instructions="",
        conversation_id="run:step6_7",
    )

    assert llm.calls[0]["response_format"] == CRITIC_RESPONSE_FORMAT
    assert llm.calls[0]["max_tokens"] is None
    assert llm.calls[0]["use_conversation_memory"] is False
    assert llm.calls[1]["max_tokens"] == 15_000
    assert llm.calls[1]["model"] == "chapter-writer-model"
    assert llm.calls[1]["use_conversation_memory"] is False
    revision_payload = json.loads(str(llm.calls[1]["messages"][-1]["content"]))
    assert revision_payload["draft_html"] == "<h1>Scene One</h1> <p>Draft</p>"
    assert revision_payload["critic"] == {"global_notes": [], "by_scene": {}}


@pytest.mark.asyncio
async def test_second_scene_bundle_failure_terminates_the_run() -> None:
    class _FailingOrchestrator(NovelistOrchestrator):
        def __init__(self) -> None:
            super().__init__(
                llm_client=_RecordingLLM(),  # type: ignore[arg-type]
                model_policy=_ModelPolicy(),  # type: ignore[arg-type]
            )
            self.conversation_ids: list[str] = []

        async def _plan_retrieval_questions_for_chunk(self, **kwargs):  # type: ignore[no-untyped-def]
            return ["Question one?", "Question two?"]

        async def _collect_scene_retrieval(self, **kwargs):  # type: ignore[no-untyped-def]
            scenes = kwargs["scene_packages"]
            return scenes, {"scene-1": {"questions_answers": []}}, []

        async def _build_chunk_context_v2(self, **kwargs):  # type: ignore[no-untyped-def]
            self.conversation_ids.append(str(kwargs["conversation_id"]))
            raise RuntimeError("context failed")

    orchestrator = _FailingOrchestrator()
    with pytest.raises(RuntimeError, match="failed after retry"):
        await orchestrator._execute_v2(
            agent=Agent(id="agent-1", name="Novelist", job="novelist", active=True),
            step_1_scenes=[
                {
                    "scene_id": "scene-1",
                    "name": "Scene One",
                    "scene_summary": "Summary",
                    "source_rawtext": "Raw evidence",
                }
            ],
            language="English",
            instructions="",
            conversation_id="run",
            stage_callback=None,
            artifacts={"stages": {}, "timings_ms": {}},
            started_total=0.0,
        )

    assert orchestrator.conversation_ids == [
        "run:step4_5:scene-1",
        "run:step4_5:scene-1:retry-1",
    ]
