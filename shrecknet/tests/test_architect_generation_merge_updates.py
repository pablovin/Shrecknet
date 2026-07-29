from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.jobs.architect.schemas import PropertyUpdateResponse
from app.jobs.architect.entity_generator import EntityGenerator
from app.tasks.architect_generation import (
    _apply_enrichment_updates,
    _collect_scene_linked_entity_ids,
    _canonicalize_entity_proposals,
    _canonicalize_milestone_groups,
    _canonicalize_scene_proposals,
    _extract_effective_entity_instance_id,
    _extract_merge_update,
    _is_approved,
    _merge_ref_lists,
    _proposal_alias_keys,
    _resolve_related_target_entity_id,
    _resolve_maintained_entity_id,
    _select_created_bundle_scenes,
)


def test_extract_merge_update_returns_merge_payload() -> None:
    proposal = {
        "name": "Dumnonia",
        "updates": {
            "merge": {
                "maintained_alias": "Britain",
                "merged_into_proposal_id": "analysis-entity-0",
            }
        },
    }

    merge = _extract_merge_update(proposal)

    assert merge is not None
    assert merge["maintained_alias"] == "Britain"


def test_resolve_maintained_entity_id_by_explicit_instance_id() -> None:
    resolved = _resolve_maintained_entity_id(
        merge_update={"maintained_entity_instance_id": "entity-123"},
        alias_to_entity_id={},
        proposal_to_entity_id={},
    )

    assert resolved == "entity-123"


def test_resolve_maintained_entity_id_by_merged_into_proposal_id() -> None:
    resolved = _resolve_maintained_entity_id(
        merge_update={"merged_into_proposal_id": "analysis-entity-0"},
        alias_to_entity_id={},
        proposal_to_entity_id={0: "entity-britain"},
    )

    assert resolved == "entity-britain"


def test_resolve_maintained_entity_id_by_maintained_alias() -> None:
    resolved = _resolve_maintained_entity_id(
        merge_update={"maintained_alias": "Britain"},
        alias_to_entity_id={"britain": "entity-britain"},
        proposal_to_entity_id={},
    )

    assert resolved == "entity-britain"


def test_resolve_maintained_entity_id_prefers_maintained_alias_over_explicit_id() -> None:
    resolved = _resolve_maintained_entity_id(
        merge_update={
            "maintained_alias": "Britain",
            "maintained_entity_instance_id": "entity-explicit",
        },
        alias_to_entity_id={"britain": "entity-britain"},
        proposal_to_entity_id={},
    )

    assert resolved == "entity-britain"


def test_merge_ref_lists_keeps_order_and_deduplicates() -> None:
    merged = _merge_ref_lists(["chunk_0_scene_1", "chunk_0_scene_2"], ["chunk_0_scene_2", "chunk_0_scene_3"])

    assert merged == ["chunk_0_scene_1", "chunk_0_scene_2", "chunk_0_scene_3"]


def test_select_created_bundle_scenes_excludes_historical_scenes() -> None:
    source_group_scenes = [
        {"scene_id": "historical", "name": "Old"},
        {"scene_id": "created-2", "name": "Second"},
        {"scene_id": "created-1", "name": "First"},
    ]

    selected = _select_created_bundle_scenes(
        source_group_scenes,
        ["created-1", "created-2"],
    )

    assert [scene["scene_id"] for scene in selected] == [
        "created-1",
        "created-2",
    ]


def test_canonicalize_entity_proposals_applies_effective_fields() -> None:
    canonical = _canonicalize_entity_proposals(
        [
            {
                "name": "Rome",
                "status": "approved_with_updates",
                "proposal_type": "update_instance",
                "entity_instance_id": "entity-1",
                "scene_refs": ["chunk_0_scene_1", "chunk_0_scene_1"],
                "ontology": "Important Locations",
                "updates": {
                    "name": "Romess",
                    "proposal_type": "new_instance",
                    "entity_definition_id": 17,
                },
            }
        ]
    )

    assert len(canonical) == 1
    item = canonical[0]
    assert item["effective_name"] == "Romess"
    assert item["effective_status"] == "approved_with_updates"
    assert item["effective_proposal_type"] == "new_instance"
    assert item["effective_definition_id"] == 17
    assert item["effective_entity_instance_id"] == "entity-1"
    assert item["effective_scene_refs"] == ["chunk_0_scene_1"]


def test_canonicalize_scene_proposals_applies_related_updates() -> None:
    canonical = _canonicalize_scene_proposals(
        [
            {
                "scene_ref": "chunk_0_scene_1",
                "scene_name": "Old Scene Name",
                "status": "approved",
                "related_to": [
                    {
                        "proposal_index": 1,
                        "alias": "Rome",
                        "entity_instance_id": "entity-rome",
                    }
                ],
                "updates": {
                    "name": "New Scene Name",
                    "related_to": [
                        {
                            "proposal_index": 2,
                            "alias": "Britain",
                            "entity_instance_id": "entity-britain",
                        }
                    ],
                    "additional_related_entity_instance_ids": ["entity-extra"],
                },
            }
        ]
    )

    assert canonical[0]["effective_name"] == "New Scene Name"
    related = canonical[0]["effective_related_to"]
    assert [item["entity_instance_id"] for item in related] == ["entity-britain", "entity-extra"]


def test_canonicalize_milestone_groups_applies_relationship_deletions() -> None:
    canonical = _canonicalize_milestone_groups(
        [
            {
                "scene_ref": "chunk_0_scene_1",
                "milestones": [
                    {
                        "milestone_ref": "m-1",
                        "title": "Rome withdraws",
                        "status": "approved",
                        "related_to": [
                            {"entity": "Rome", "relationship_label": "withdraws"},
                            {"entity": "Britain", "relationship_label": "is_abandoned"},
                        ],
                        "updates": {
                            "relationship_deletions": [
                                {
                                    "operation": "delete",
                                    "relation_type": "related_to",
                                    "target_alias": "Rome",
                                }
                            ]
                        },
                    }
                ],
            }
        ]
    )

    milestone = canonical[0]["milestones"][0]
    assert milestone["effective_name"] == "Rome withdraws"
    assert milestone["effective_status"] == "approved"
    assert len(milestone["effective_related_to"]) == 1
    assert milestone["effective_related_to"][0]["entity"] == "Britain"


def test_resolve_related_target_prefers_proposal_mapping_over_stale_id() -> None:
    resolved = _resolve_related_target_entity_id(
        related={
            "proposal_index": 8,
            "alias": "Saxons",
            "entity_instance_id": "stale-old-id",
        },
        proposal_to_entity_id={8: "new-created-id"},
        alias_to_entity_id={"saxons": "new-created-id"},
        alias_candidates=["Saxons"],
        fallback_entity_instance_id="stale-old-id",
        valid_entity_ids={"new-created-id"},
    )

    assert resolved == "new-created-id"


def test_resolve_related_target_rejects_unknown_stale_id_without_mapping() -> None:
    resolved = _resolve_related_target_entity_id(
        related={
            "entity_instance_id": "stale-old-id",
        },
        proposal_to_entity_id={},
        alias_to_entity_id={},
        alias_candidates=[],
        fallback_entity_instance_id="stale-old-id",
        valid_entity_ids={"known-id"},
    )

    assert resolved is None


def test_extract_effective_entity_instance_id_reads_updates_entity_in_instance() -> None:
    proposal = {
        "entity_instance_id": None,
        "updates": {
            "proposal_type": "update_instance",
            "entityInInstance": {"entity_instance_id": "entity-nested-1"},
        },
    }

    assert _extract_effective_entity_instance_id(proposal) == "entity-nested-1"


def test_extract_effective_entity_instance_id_reads_camel_case_keys() -> None:
    proposal = {
        "entityInstanceId": None,
        "updates": {
            "proposal_type": "update_instance",
            "entityInstanceId": "entity-camel-1",
        },
    }

    assert _extract_effective_entity_instance_id(proposal) == "entity-camel-1"


def test_proposal_alias_keys_include_original_updated_and_canonical_names() -> None:
    keys = _proposal_alias_keys(
        {
            "name": "Lady Tamura Evrain",
            "effective_name": "Evrain",
            "canonical": "evrain",
            "updates": {"name": "Evrain"},
        }
    )

    assert "lady tamura evrain" in keys
    assert "evrain" in keys


def test_collect_scene_linked_entity_ids_unions_across_scenes() -> None:
    linked = _collect_scene_linked_entity_ids(
        {
            "chunk_0_scene_1": {"entity-a", "entity-b"},
            "chunk_0_scene_2": {"entity-b", "entity-c"},
            "chunk_0_scene_3": set(),
        }
    )

    assert linked == {"entity-a", "entity-b", "entity-c"}


def test_is_approved_requires_explicit_approved_status() -> None:
    assert _is_approved("approved")
    assert _is_approved("approved_with_updates")
    assert _is_approved("merged")
    assert not _is_approved(None)
    assert not _is_approved("")
    assert not _is_approved("pending")


def test_resolve_related_target_uses_original_alias_after_rename() -> None:
    alias_to_entity_id = {}
    for key in _proposal_alias_keys(
        {
            "name": "Lady Tamura Evrain",
            "effective_name": "Evrain",
            "canonical": "evrain",
            "updates": {"name": "Evrain"},
        }
    ):
        alias_to_entity_id[key] = "entity-evrain"

    resolved = _resolve_related_target_entity_id(
        related={"entity": "Lady Tamura Evrain"},
        proposal_to_entity_id={},
        alias_to_entity_id=alias_to_entity_id,
        alias_candidates=["Lady Tamura Evrain"],
        fallback_entity_instance_id=None,
        valid_entity_ids={"entity-evrain"},
    )

    assert resolved == "entity-evrain"


class _FakeResult:
    def __init__(self, *, single_value=None, data_rows=None):
        self._single_value = single_value
        self._data_rows = data_rows or []

    async def single(self):
        return self._single_value

    async def data(self):
        return self._data_rows


class _FakeGraphSession:
    def __init__(self):
        self.queries: list[str] = []

    async def run(self, query: str, **kwargs):
        self.queries.append(query)
        if "UNWIND $entity_ids AS entity_id" in query:
            # Hydrate only one entity; second remains unresolved fallback.
            rows = []
            for item in kwargs.get("entity_ids", []):
                if item == "entity-hydrated":
                    rows.append(
                        {
                            "entity_id": "entity-hydrated",
                            "alias": "Hydrated Entity",
                            "definition_id": None,
                            "text": "",
                            "autogenerated_text": "",
                            "properties": "{}",
                        }
                    )
            return _FakeResult(data_rows=rows)
        if "RETURN e.properties AS properties" in query:
            return _FakeResult(single_value={"properties": "{}"})
        if "RETURN existing.data AS existing_data" in query:
            return _FakeResult(single_value={"existing_data": None})
        return _FakeResult(single_value={})


class _FakeGenerator:
    def __init__(self):
        self.calls: list[dict] = []

    async def _extract_properties_and_relationships(self, **kwargs):
        self.calls.append(kwargs)
        return PropertyUpdateResponse(
            updated_autogenerated_summary="Updated summary",
            new_properties=[],
            new_relationships=[],
        )


@pytest.mark.asyncio
async def test_apply_enrichment_updates_processes_all_targets_without_skip() -> None:
    graph = _FakeGraphSession()
    generator = _FakeGenerator()

    stats = await _apply_enrichment_updates(
        graph_session=graph,
        generator=generator,
        debug_job_id=999,
        target_entity_ids=["entity-hydrated", "entity-missing"],
        entity_definitions_map={},
        existing_entities_map={},
        alias_to_entity_id={},
        scene_proposals=[],
        milestone_groups=[],
        scene_ref_to_entities={},
        proposal_scene_refs={},
        entity_scene_refs={},
        original_text="global context",
        author_id="system",
    )

    assert stats["scanned_entities"] == 2
    assert stats["processed_entities"] == 2
    assert stats["fallback_entities"] == 2
    assert stats["failed_entities"] == 0
    assert len(generator.calls) == 2
    assert all(call.get("debug_job_id") == 999 for call in generator.calls)


class _FakeModelPolicy:
    def get_model(self, _task):
        return "test-model"


class _FakeLLMClient:
    async def chat(self, **_kwargs):
        return json.dumps(
            {
                "properties_update": [],
                "relationships_update": [],
                "updated_autogenerated_summary": "ok",
            }
        )


@pytest.mark.asyncio
async def test_entity_generator_writes_single_enrich_debug_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "shrecknet" / "databases" / "local_test" / "architect").mkdir(parents=True, exist_ok=True)

    generator = EntityGenerator(
        llm_client=_FakeLLMClient(),
        model_policy=_FakeModelPolicy(),
    )
    await generator._extract_properties_and_relationships(
        entity_definition_id=1,
        entity_alias="Rome",
        entity_type_name="Location",
        properties_catalog=[],
        relationships_catalog=[],
        chunks=["Scene: Rome rises"],
        related_entities=[],
        original_text="Rome context",
        is_update=True,
        existing_text="",
        existing_autogenerated_text="",
        existing_properties=[],
        existing_relationships=[],
        debug_job_id=123,
        debug_entity_id="entity-rome-1",
        debug_anomalies=["no_context"],
        debug_context_package={"scenes": [], "milestones": []},
        update_response_mode="strict_name_based",
    )

    out_dir = tmp_path / "shrecknet" / "databases" / "local_test" / "architect" / "generate" / "123"
    files = list(out_dir.glob("*_enrich.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["llm_request"]["model"] == "test-model"
    assert payload["llm_response"]["raw_response"] is not None
    assert payload["context_package"] == {"scenes": [], "milestones": []}


def test_parse_extraction_response_strict_mode_rejects_legacy_keys() -> None:
    generator = EntityGenerator(
        llm_client=_FakeLLMClient(),
        model_policy=_FakeModelPolicy(),
    )
    response = json.dumps(
        {
            "new_properties": [],
            "new_relationships": [],
            "updated_autogenerated_summary": "legacy payload",
        }
    )
    parsed = generator._parse_extraction_response(
        response_text=response,
        is_update=True,
        update_response_mode="strict_name_based",
    )
    assert parsed is None


def test_related_entities_format_is_name_id_type() -> None:
    formatted = EntityGenerator._format_related_entities(
        [
            {
                "name": "Arthur",
                "entity_instance_id": "entity-arthur",
                "entity_type_name": "Players",
            }
        ]
    )
    assert 'name: "Arthur"' in formatted
    assert 'id: "entity-arthur"' in formatted
    assert 'type: "Players"' in formatted
