from __future__ import annotations

import asyncio

from app.tasks.architect_analysis import (
    _build_scene_proposals,
    _classify_entities,
    _classify_entities_with_reconciliation,
    _ensure_scene_milestone_boundaries,
    _extract_scene_entities,
    _flatten_scene_inputs,
    _format_ontology_definitions_from_entities,
    _resolve_local_tests_output_dir,
    _run_milestone_proposal_phase,
)


def test_resolve_analysis_output_dir_uses_required_layout(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.delenv("SHRECKNET_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    output_dir = _resolve_local_tests_output_dir("job-123")

    assert output_dir.as_posix().endswith(
        "local_tests/arhictect/Analyses/job-123"
    )
    assert output_dir.exists()


def test_flatten_scene_inputs_preserves_scene_fields() -> None:
    chunk_results = [
        {
            "status": "ok",
            "chunk_index": 2,
            "entity_instance_id": "ent-1",
            "entity_alias": "Narrator",
            "scenes": [
                {
                    "scene_id": 0,
                    "name": "Arrival",
                    "description": "The group enters the city.",
                    "text": "[P1] They arrive at dawn.",
                }
            ],
        },
        {
            "status": "error",
            "chunk_index": 3,
            "scenes": [],
        },
    ]

    flattened = _flatten_scene_inputs(chunk_results)

    assert len(flattened) == 1
    assert flattened[0]["scene_ref"] == "chunk_2_scene_0"
    assert flattened[0]["scene_name"] == "Arrival"
    assert flattened[0]["scene_description"] == "The group enters the city."
    assert flattened[0]["scene_text"] == "[P1] They arrive at dawn."


def test_classify_entities_splits_new_and_updated() -> None:
    scene_results = [
        {
            "scene_ref": "chunk_0_scene_0",
            "entities": [
                {
                    "name": "Jessie",
                    "ontology": "Character",
                    "confidence": 0.9,
                    "why": "Acts in scene",
                },
                {
                    "name": "Mithras",
                    "ontology": "Deity",
                    "confidence": 0.8,
                    "why": "Mentioned by name",
                },
            ],
        }
    ]
    existing_nodes = [
        {"node_id": "node-1", "alias": "Jessie Williams", "ontology": "Character"}
    ]

    classified = _classify_entities(scene_results, existing_nodes)

    assert classified["updated_count"] == 1
    assert classified["new_count"] == 1
    statuses = {item["name"]: item["status"] for item in classified["proposed_entities"]}
    assert statuses["Jessie"] == "updated"
    assert statuses["Mithras"] == "new"


def test_format_ontology_definitions_only_auto_generatable() -> None:
    class _Def:
        def __init__(self, name: str, desc: str, auto_generatable: bool) -> None:
            self.name = name
            self.description = desc
            self.auto_generatable = auto_generatable

    definitions = [
        _Def("Players", "Playable characters", True),
        _Def("Timeline", "Not auto generated", False),
    ]

    rendered = _format_ontology_definitions_from_entities(definitions)

    assert "Players" in rendered
    assert "Timeline" not in rendered


def test_extract_scene_entities_uses_scene_prompt_fields() -> None:
    class _FakeLLMClient:
        async def chat(self, *, model, messages, temperature):  # type: ignore[no-untyped-def]
            del model, messages, temperature
            return '{"entities": []}'

    scene_results = asyncio.run(
        _extract_scene_entities(
            run_id="run-1",
            llm_client=_FakeLLMClient(),
            model="test-model",
            ontology_definitions="- Players: Playable characters",
            allowed_ontology_names={"players": "Players"},
            scenes=[
                {
                    "scene_ref": "chunk_0_scene_0",
                    "scene_text": "[P1] Arthur arrives.",
                }
            ],
        )
    )

    assert len(scene_results) == 1
    assert scene_results[0]["status"] == "ok"
    assert scene_results[0]["entities"] == []


def test_scene_reconcile_dedups_alias_equivalents_without_graph_candidates() -> None:
    class _FakeLLMClient:
        async def chat(self, *, model, messages, temperature):  # type: ignore[no-untyped-def]
            del model, messages, temperature
            return '{"existing": [], "new": []}'

    scene_results = [
        {
            "scene_ref": "chunk_0_scene_0",
            "chunk_index": 0,
            "entities": [
                {
                    "name": "Sir Kay",
                    "ontology": "Players",
                    "confidence": 0.8,
                    "why": "Named knight",
                }
            ],
        },
        {
            "scene_ref": "chunk_1_scene_0",
            "chunk_index": 1,
            "entities": [
                {
                    "name": "Kay",
                    "ontology": "Players",
                    "confidence": 0.9,
                    "why": "Same character referenced shortly",
                }
            ],
        },
    ]

    classified = asyncio.run(
        _classify_entities_with_reconciliation(
            llm_client=_FakeLLMClient(),
            model="test-model",
            scene_results=scene_results,
            existing_nodes=[],
            ontology_definitions="- Players: Playable characters",
            allowed_ontology_names={"players": "Players"},
        )
    )

    assert len(classified["proposed_entities"]) == 1
    entity = classified["proposed_entities"][0]
    assert entity["status"] == "new"
    assert entity["proposal_type"] == "new_instance"
    assert sorted(entity["proposal_metadata"]["chunk_indices"]) == [0, 1]
    assert entity["proposal_metadata"]["mention_count"] == 2


def test_scene_reconcile_llm_match_adds_update_metadata() -> None:
    class _FakeLLMClient:
        async def chat(self, *, model, messages, temperature):  # type: ignore[no-untyped-def]
            del model, temperature
            prompt = messages[0]["content"]
            assert "Jessie" in prompt
            return (
                '{"existing": [{"proposed_name": "Jessie", "matched_node_id": '
                '"node-1", "ontology": "Players"}], "new": []}'
            )

    scene_results = [
        {
            "scene_ref": "chunk_0_scene_0",
            "chunk_index": 0,
            "entities": [
                {
                    "name": "Jessie",
                    "ontology": "Players",
                    "confidence": 0.9,
                    "why": "Acts in scene",
                }
            ],
        }
    ]
    existing_nodes = [
        {"node_id": "node-1", "alias": "Jessie Williams", "ontology": "Players"}
    ]

    classified = asyncio.run(
        _classify_entities_with_reconciliation(
            llm_client=_FakeLLMClient(),
            model="test-model",
            scene_results=scene_results,
            existing_nodes=existing_nodes,
            ontology_definitions="- Players: Playable characters",
            allowed_ontology_names={"players": "Players"},
        )
    )

    entity = classified["proposed_entities"][0]
    assert entity["status"] == "updated"
    assert entity["matched_node_id"] == "node-1"
    assert entity["entity_instance_id"] == "node-1"
    assert entity["proposal_type"] == "update_instance"
    assert entity["proposal_metadata"]["resolved_status"] == "existing"


def test_extract_scene_entities_drops_unknown_ontology_values() -> None:
    class _FakeLLMClient:
        async def chat(self, *, model, messages, temperature):  # type: ignore[no-untyped-def]
            del model, messages, temperature
            return (
                '{"entities":[{"name":"Arthur","ontology":"Unknown","confidence":0.9,'
                '"why":"Named in scene"},{"name":"Kay","ontology":"Players",'
                '"confidence":0.8,"why":"Named in scene"}]}'
            )

    scene_results = asyncio.run(
        _extract_scene_entities(
            run_id="run-1",
            llm_client=_FakeLLMClient(),
            model="test-model",
            ontology_definitions="- Players: Playable characters",
            allowed_ontology_names={"players": "Players"},
            scenes=[
                {
                    "scene_ref": "chunk_0_scene_0",
                    "scene_text": "[P1] Arthur and Kay arrive.",
                }
            ],
        )
    )

    assert scene_results[0]["status"] == "ok"
    assert len(scene_results[0]["entities"]) == 1
    assert scene_results[0]["entities"][0]["name"] == "Kay"
    assert scene_results[0]["entities"][0]["ontology"] == "Players"


def test_build_scene_proposals_includes_ordering_links() -> None:
    scenes = [
        {
            "scene_ref": "chunk_0_scene_0",
            "chunk_index": 0,
            "source_entity_instance_id": "source-1",
            "source_entity_alias": "Narrator",
            "scene_id": 0,
            "scene_name": "Arrival",
            "scene_description": "The group arrives.",
            "scene_text": "[P1] They arrive.",
        },
        {
            "scene_ref": "chunk_0_scene_1",
            "chunk_index": 0,
            "source_entity_instance_id": "source-1",
            "source_entity_alias": "Narrator",
            "scene_id": 1,
            "scene_name": "Debate",
            "scene_description": "A tense exchange.",
            "scene_text": "[P2] They argue.",
        },
    ]
    proposed_entities = [
        {
            "name": "Arthur",
            "canonical": "arthur",
            "scene_refs": ["chunk_0_scene_0", "chunk_0_scene_1"],
            "status": "new",
            "proposal_type": "new_instance",
            "entity_instance_id": None,
        }
    ]

    proposals = _build_scene_proposals(scenes, proposed_entities, author_id="agent-1")

    assert len(proposals) == 2
    assert proposals[0]["scene_order"] == 1
    assert proposals[1]["scene_order"] == 2
    assert proposals[0]["preceded_by"] is None
    assert proposals[1]["followed_by"] is None
    assert proposals[0]["followed_by"]["scene_ref"] == "chunk_0_scene_1"
    assert proposals[1]["preceded_by"]["scene_ref"] == "chunk_0_scene_0"


def test_ensure_scene_milestone_boundaries_empty_creates_begin_end() -> None:
    milestones = _ensure_scene_milestone_boundaries(
        [],
        scene_ref="scene-ref-1",
        scene_id="scene-1",
        source_entity_instance_id="entity-1",
        author_id="author-1",
    )
    assert len(milestones) == 2
    assert milestones[0]["boundary_type"] == "begin"
    assert milestones[1]["boundary_type"] == "end"
    assert milestones[0]["milestone_order"] == 1
    assert milestones[1]["milestone_order"] == 2
    assert milestones[0]["title"]
    assert milestones[1]["title"]


def test_ensure_scene_milestone_boundaries_single_expands_to_begin_end() -> None:
    milestones = _ensure_scene_milestone_boundaries(
        [
            {
                "milestone_ref": "m-1",
                "scene_ref": "scene-ref-1",
                "scene_id": "scene-1",
                "title": "Only beat",
                "description": "A single beat.",
                "boundary_type": "none",
            }
        ],
        scene_ref="scene-ref-1",
        scene_id="scene-1",
        source_entity_instance_id="entity-1",
        author_id="author-1",
    )
    assert len(milestones) == 2
    assert milestones[0]["boundary_type"] == "begin"
    assert milestones[1]["boundary_type"] == "end"


def test_ensure_scene_milestone_boundaries_forces_missing_begin_end() -> None:
    milestones = _ensure_scene_milestone_boundaries(
        [
            {
                "milestone_ref": "m-1",
                "scene_ref": "scene-ref-1",
                "scene_id": "scene-1",
                "title": "",
                "description": "First action",
                "boundary_type": "invalid",
            },
            {
                "milestone_ref": "m-2",
                "scene_ref": "scene-ref-1",
                "scene_id": "scene-1",
                "title": "",
                "description": "Second action",
                "boundary_type": "none",
            },
        ],
        scene_ref="scene-ref-1",
        scene_id="scene-1",
        source_entity_instance_id="entity-1",
        author_id="author-1",
    )
    assert len(milestones) == 2
    assert milestones[0]["boundary_type"] == "begin"
    assert milestones[1]["boundary_type"] == "end"
    assert milestones[0]["milestone_order"] == 1
    assert milestones[1]["milestone_order"] == 2
    assert milestones[0]["title"]
    assert milestones[1]["title"]


def test_run_milestone_proposal_phase_keeps_all_scenes_and_coerces_boundaries() -> None:
    class _FakeLLMClient:
        async def chat(self, *, model, messages, temperature):  # type: ignore[no-untyped-def]
            del model, temperature
            prompt = str(messages[0].get("content") or "")
            if "scene_ref: scene-1" in prompt:
                return '{"milestones":[]}'
            if "scene_ref: scene-2" in prompt:
                return '{"milestones":[{"title":"Middle beat","description":"Something happens","boundary_type":"none"}]}'
            return '{"milestones":[{"title":"","description":"Opens","boundary_type":"start"},{"title":"","description":"Closes","boundary_type":"finish"}]}'

    result = asyncio.run(
        _run_milestone_proposal_phase(
            run_id="run-1",
            llm_client=_FakeLLMClient(),
            model="test-model",
            proposed_scenes=[
                {
                    "scene_ref": "scene-1",
                    "scene_id": "s1",
                    "scene_name": "Scene One",
                    "scene_description": "Desc one",
                    "scene_text": "Text one",
                    "related_to": [],
                },
                {
                    "scene_ref": "scene-2",
                    "scene_id": "s2",
                    "scene_name": "Scene Two",
                    "scene_description": "Desc two",
                    "scene_text": "Text two",
                    "related_to": [],
                },
                {
                    "scene_ref": "scene-3",
                    "scene_id": "s3",
                    "scene_name": "Scene Three",
                    "scene_description": "Desc three",
                    "scene_text": "Text three",
                    "related_to": [],
                },
            ],
            author_id="author-1",
        )
    )

    assert result["removed_scene_count"] == 0
    assert result["removed_scene_refs"] == []
    assert len(result["per_scene"]) == 3
    for row in result["per_scene"]:
        milestones = row["milestones"]
        assert len(milestones) >= 2
        boundaries = {m["boundary_type"] for m in milestones}
        assert "begin" in boundaries
        assert "end" in boundaries


def test_build_scene_proposals_related_to_targets_deduped_entities() -> None:
    scenes = [
        {
            "scene_ref": "chunk_0_scene_0",
            "chunk_index": 0,
            "source_entity_instance_id": "source-1",
            "source_entity_alias": "Narrator",
            "scene_id": 0,
            "scene_name": "Arrival",
            "scene_description": "The group arrives.",
            "scene_text": "[P1] They arrive.",
        }
    ]
    proposed_entities = [
        {
            "name": "Arthur",
            "canonical": "arthur",
            "scene_refs": ["chunk_0_scene_0"],
            "status": "new",
            "proposal_type": "new_instance",
            "entity_instance_id": None,
        },
        {
            "name": "Londinium",
            "canonical": "londinium",
            "scene_refs": ["chunk_0_scene_0"],
            "status": "updated",
            "proposal_type": "update_instance",
            "entity_instance_id": "node-2",
        },
    ]

    proposals = _build_scene_proposals(scenes, proposed_entities, author_id="agent-1")

    related = proposals[0]["related_to"]
    assert len(related) == 2
    assert related[0]["proposal_index"] == 0
    assert related[0]["canonical"] == "arthur"
    assert related[1]["proposal_index"] == 1
    assert related[1]["canonical"] == "londinium"
    assert related[1]["entity_instance_id"] == "node-2"
