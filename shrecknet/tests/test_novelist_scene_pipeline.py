from __future__ import annotations

import json

import pytest

from app.jobs.novelist.novelist import NovelistOrchestrator
from app.models.agent import Agent
from app.models.novelist import NovelistStage
from app.schemas.novelist import NovelistRunCreate


class _FakeModelPolicy:
    model_novelist_draft = "draft-model"
    model_novelist = "core-model"

    def get_model(self, _task):
        return "default-model"


class _FakeLLM:
    async def chat(self, model, messages, temperature, conversation_id=None):
        del model, temperature, conversation_id
        system = ""
        for msg in messages:
            if msg.get("role") == "system":
                system = msg.get("content", "")
        user = messages[-1].get("content", "")

        if "extracting compact continuity context" in system:
            return "- Aria distrusts the council\n- Brenn protects her"

        if "segment a text into coherent narrative scenes" in user:
            return json.dumps(
                {
                    "scenes": [
                        {
                            "scene_id": 0,
                            "name": "Gate Arrival",
                            "description": "The party reaches the city gate.",
                            "start_paragraph": 1,
                            "end_paragraph": 1,
                        },
                        {
                            "scene_id": 1,
                            "name": "Council Chamber",
                            "description": "The council receives the party.",
                            "start_paragraph": 2,
                            "end_paragraph": 2,
                        },
                    ]
                }
            )

        if "Scenes to refine" in user:
            return json.dumps(
                {
                    "scenes": [
                        {
                            "scene_id": 0,
                            "name": "Gate Arrival",
                            "description": "The party reaches the city gate.",
                            "start_paragraph": 1,
                            "end_paragraph": 1,
                        },
                        {
                            "scene_id": 1,
                            "name": "Council Chamber",
                            "description": "The council receives the party.",
                            "start_paragraph": 2,
                            "end_paragraph": 2,
                        },
                    ]
                }
            )

        if "deciding which entities" in user:
            return json.dumps(
                {
                    "entities": [
                        {
                            "name": "Aria",
                            "ontology": "Character",
                            "confidence": 0.9,
                            "why": "Named directly in scene text.",
                        }
                    ]
                }
            )

        if "extract only graph-worthy milestones" in user:
            return json.dumps(
                {
                    "milestones": [
                        {
                            "title": "Gate challenge",
                            "description": "The guards question the party.",
                            "boundary_type": "begin",
                        },
                        {
                            "title": "Formal audience",
                            "description": "The party is invited inside.",
                            "boundary_type": "end",
                        },
                    ]
                }
            )

        if "normalizing Architect-derived scene scaffolding" in system:
            payload = json.loads(user)
            scenes = payload.get("scenes", [])
            return json.dumps(
                {
                    "scenes": [
                        {
                            "scene_id": scene["scene_id"],
                            "name": scene["name"],
                            "scene_summary": scene.get("scene_summary", ""),
                            "milestones": scene.get("milestones", []),
                            "related_entities": scene.get("related_entities", []),
                            "source_anchors": scene.get("source_anchors", []),
                            "new_or_update": "new",
                        }
                        for scene in scenes
                    ]
                }
            )

        if "converting a normalized scene scaffold" in system:
            payload = json.loads(user)
            scene = payload["scenes"][0]
            return json.dumps(
                {
                    "scene_packages": [
                        {
                            "scene_id": scene["scene_id"],
                            "source_paragraphs": scene.get("source_paragraphs", []),
                            "raw_scene_text": scene.get("raw_scene_text", ""),
                            "scene_summary": scene.get("scene_summary", ""),
                            "scene_goal": "Advance the negotiation.",
                            "milestones": scene.get("milestones", []),
                            "related_entities": scene.get("related_entities", []),
                            "temporal_position_hint": "middle",
                            "tone_hint": "tense",
                            "open_questions_for_retrieval": [
                                f"What prior tension shapes {scene['scene_id']}?"
                            ],
                        }
                    ]
                }
            )

        if "generating scene-local Elder retrieval questions" in system:
            return json.dumps(
                {
                    "queries": [
                        "What prior event is most relevant to this scene?"
                    ]
                }
            )

        if "drafting a compact scene intent" in system:
            return json.dumps(
                {
                    "scene_id": "scene-001",
                    "what_happens": ["The party requests support from the council."],
                    "emotional_progression": ["cautious", "defiant"],
                    "speaking_goals": ["convince", "avoid confession"],
                    "implied_history": ["Old betrayal is remembered."],
                    "forbidden_contradictions": ["Do not claim the treaty was signed."],
                }
            )

        if "writing one scene in third-person prose" in system:
            return "<p>Aria paused at the chamber threshold and weighed each face before speaking.</p>"

        if "structural critic over one complete chapter draft" in system:
            return json.dumps({"global_notes": ["Transitions need smoothing."], "by_scene": {}})

        if "Revise the full draft using critic feedback" in system:
            return "<p>The party crossed from suspicion to negotiation without breaking stride.</p><blockquote>\"We ask for terms, not mercy,\" Aria said.</blockquote>"

        return "{}"

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_scene_pipeline_stage_progression_and_artifacts() -> None:
    orchestrator = NovelistOrchestrator(
        llm_client=_FakeLLM(),
        model_policy=_FakeModelPolicy(),
        max_concurrency=10,
        elder_query_runner=_fake_elder_runner,
    )
    agent = Agent(id="agent-1", name="Novelist", job="novelist", active=True)
    payload = NovelistRunCreate(
        unstructured_text=(
            "Aria and Brenn arrived at the city gate under torchlight.\n\n"
            "Inside the council chamber, the elders demanded an oath."
        ),
        language="en",
        instructions="Keep names consistent.",
    )

    seen_stages: list[NovelistStage] = []

    async def stage_callback(stage: NovelistStage, data: dict):
        del data
        seen_stages.append(stage)

    result = await orchestrator.execute(
        agent=agent,
        payload=payload,
        conversation_id="test-run",
        stage_callback=stage_callback,
    )

    assert NovelistStage.INGEST in seen_stages
    assert NovelistStage.SCAFFOLDING in seen_stages
    assert NovelistStage.SCENE_PACKAGE in seen_stages
    assert NovelistStage.RETRIEVAL in seen_stages
    assert NovelistStage.INTENT_DRAFTING in seen_stages
    assert NovelistStage.PROSE_GENERATION in seen_stages
    assert NovelistStage.CRITIC in seen_stages
    assert NovelistStage.REVISION in seen_stages
    assert NovelistStage.MERGING in seen_stages

    artifacts = result["artifacts"]
    assert "stages" in artifacts
    assert "scaffolding" in artifacts["stages"]
    assert "scene_package" in artifacts["stages"]
    assert "retrieval" in artifacts["stages"]
    assert "intent_drafting" in artifacts["stages"]
    assert "prose_generation" in artifacts["stages"]
    assert "critic" in artifacts["stages"]
    assert "revision" in artifacts["stages"]
    assert "merging" in artifacts["stages"]
    assert "step_outputs" in artifacts
    assert "step_1" in artifacts["step_outputs"]
    assert "step_2" in artifacts["step_outputs"]
    assert "step_3" in artifacts["step_outputs"]
    assert "step_4" in artifacts["step_outputs"]
    assert "step_5" in artifacts["step_outputs"]
    assert "step_6" in artifacts["step_outputs"]
    assert "step_7" in artifacts["step_outputs"]
    assert artifacts["timings_ms"]["total"] >= 0

    assert "<h2>Scene 1" in result["draft_text"]
    assert (
        artifacts["step_outputs"]["step_7"]["final_rewritten_text"]
        == result["draft_text"]
    )
    assert len(result["scene_results"]) == 2
    assert result["timing_summary"]["scene_count"] == 2


def test_parse_milestones_enforces_begin_end_when_empty() -> None:
    milestones = NovelistOrchestrator._parse_milestones({"milestones": []})
    assert len(milestones) >= 2
    assert any("Scene opening beat" in item for item in milestones)
    assert any("Scene closing beat" in item for item in milestones)


def test_parse_milestones_enforces_begin_end_when_single() -> None:
    milestones = NovelistOrchestrator._parse_milestones(
        {
            "milestones": [
                {
                    "title": "Only beat",
                    "description": "Only one event.",
                    "boundary_type": "none",
                }
            ]
        }
    )
    assert len(milestones) >= 2
    assert all("Only beat: Only one event." in item for item in milestones[:2])


async def _fake_elder_runner(_agent: Agent, _query: str) -> list[dict[str, str]]:
    return [
        {"node_label": "Scene", "text": "Earlier, Aria refused the oath before the council."},
        {"node_label": "EntityInstance", "text": "Brenn's speaking style is blunt and confrontational."},
    ]


class _CaptureLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def chat(self, model, messages, temperature, conversation_id=None):
        del model, temperature, conversation_id
        system = ""
        for msg in messages:
            if msg.get("role") == "system":
                system = msg.get("content", "")
        user = messages[-1].get("content", "")
        self.calls.append((system, user))
        if "structural critic over one complete chapter draft" in system:
            return json.dumps({"global_notes": ["ok"], "by_scene": {}})
        if "Revise the full draft using critic feedback" in system:
            return "<p>Revised.</p><blockquote>\"Line.\"</blockquote>"
        if "writing one scene in third-person prose" in system:
            return "<p>A</p><blockquote>B</blockquote>"
        return "{}"


@pytest.mark.asyncio
async def test_prompt_payloads_do_not_include_debug_fields() -> None:
    llm = _CaptureLLM()
    orchestrator = NovelistOrchestrator(llm_client=llm, model_policy=_FakeModelPolicy())
    prose_html = await orchestrator._generate_scene_paragraph_single(
        scene_id="scene-001",
        delta_input={
            "what_happens": ["X"],
        },
        language="en",
        instructions="",
        conversation_id="cid",
    )
    assert prose_html.startswith("<p>")

    await orchestrator._critic_scene_set(
        scene_packages=[{"scene_id": "scene-001", "name": "S1"}],
        prose_by_scene=[{"scene_id": "scene-001", "name": "S1", "prose_html": prose_html}],
        language="en",
        instructions="",
        conversation_id="cid",
    )
    _, critic_user = llm.calls[-1]
    assert "scene_packages" not in critic_user
    assert "scene_drafts" not in critic_user


def test_scene_prose_limiter_enforces_single_paragraph_and_dialogue() -> None:
    orchestrator = NovelistOrchestrator(llm_client=_FakeLLM(), model_policy=_FakeModelPolicy())
    html = "<p>" + ("x" * 3000) + "</p><blockquote>" + ("y" * 3000) + "</blockquote>"
    limited = orchestrator._limit_scene_prose_html(html, max_chars=1400)
    assert limited.count("<p>") == 1
    assert limited.count("<blockquote>") <= 1
    assert len(limited) < 1700
