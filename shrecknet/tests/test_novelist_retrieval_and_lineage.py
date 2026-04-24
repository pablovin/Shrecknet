from __future__ import annotations

import asyncio

from app.jobs.novelist.novelist import NovelistOrchestrator


class _DummyPolicy:
    model_novelist_draft = "draft"
    model_novelist_critic = "critic"

    def get_model(self, _task):
        return "default"


class _DummyLLM:
    async def chat(self, *args, **kwargs):
        del args, kwargs
        return "{}"


def test_retrieval_filtering_keeps_scene_relevant_buckets() -> None:
    orchestrator = NovelistOrchestrator(
        llm_client=_DummyLLM(),
        model_policy=_DummyPolicy(),
    )
    buckets = orchestrator._filter_scene_retrieval(
        [
            {"node_label": "Scene", "text": "Earlier, the pact failed and tension rose."},
            {
                "node_label": "EntityInstance",
                "text": "Aria's personality is stubborn and her speaking style is clipped.",
            },
            {
                "node_label": "Milestone",
                "text": "This contradicts the oath made in the previous council hearing.",
            },
            {"node_label": "EntityInstance", "text": "Their relationship remains hostile."},
        ]
    )

    assert buckets["prior_events"]
    assert buckets["personality_reminders"]
    assert buckets["relationship_summaries"]
    assert buckets["contradiction_warnings"]
    assert len(buckets["prior_events"]) <= 6


def test_scene_result_lineage_uses_revision_actions() -> None:
    scene_packages = [
        {"scene_id": "scene-001", "scene_summary": "A", "scene_goal": "A", "source_paragraphs": [1]},
        {"scene_id": "scene-002", "scene_summary": "B", "scene_goal": "B", "source_paragraphs": [2]},
    ]
    retrieval_by_scene = {
        "scene-001": {"buckets": {}},
        "scene-002": {"buckets": {}},
    }
    intents_by_scene = {
        "scene-001": {"scene_id": "scene-001"},
        "scene-002": {"scene_id": "scene-002"},
    }
    prose_by_scene = [
        {"scene_id": "scene-001", "prose_html": "<p>A</p>"},
        {"scene_id": "scene-002", "prose_html": "<p>B</p>"},
    ]
    critic = {
        "by_scene": {
            "scene-001": {"missing_transitions": ["bridge"]},
            "scene-002": {"missing_transitions": []},
        }
    }
    revision = {
        "lineage": {
            "scene-001": {"source_scene_ids": ["scene-001"], "action": "split"},
            "scene-002": {"source_scene_ids": ["scene-002"], "action": "merged"},
        }
    }

    scene_results = NovelistOrchestrator._build_scene_results(
        scene_packages=scene_packages,
        retrieval_by_scene=retrieval_by_scene,
        intents_by_scene=intents_by_scene,
        prose_by_scene=prose_by_scene,
        critic=critic,
        revision=revision,
    )

    scene1 = next(item for item in scene_results if item["scene_id"] == "scene-001")
    scene2 = next(item for item in scene_results if item["scene_id"] == "scene-002")
    assert scene1["revision_action"] == "split"
    assert scene2["revision_action"] == "merged"
    assert scene1["critic_issue_count"] == 1


def test_scene_retrieval_trace_contains_timeout_and_queue_metrics() -> None:
    async def _slow_runner(_agent, _query):
        await asyncio.sleep(0.05)
        return []

    orchestrator = NovelistOrchestrator(
        llm_client=_DummyLLM(),
        model_policy=_DummyPolicy(),
        elder_query_runner=_slow_runner,
        elder_query_concurrency=1,
        elder_query_timeout_s=0.01,
    )

    scene_packages = [
        {
            "scene_id": "scene-001",
            "open_questions_for_retrieval": ["Who is blocking the envoy?"],
        }
    ]

    _enhanced, _grouped, traces = asyncio.run(
        orchestrator._collect_scene_retrieval(
            agent=object(),
            scene_packages=scene_packages,
            language="en",
            instructions="",
            conversation_id=None,
        )
    )

    assert len(traces) == 1
    trace = traces[0]
    assert trace["fallback_reason"] == "timeout"
    assert isinstance(trace["queue_wait_ms"], float)
    assert trace["queue_wait_ms"] >= 0.0
    assert isinstance(trace["retrieval_total_ms"], float)
    assert trace["retrieval_total_ms"] >= 0.0
