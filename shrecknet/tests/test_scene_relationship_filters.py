from __future__ import annotations

import pytest

from app.services.ontology_instance_service import OntologyInstanceService


class _FakeResult:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self._rows = rows or []

    async def data(self):
        return self._rows

    async def single(self):
        return self._rows[0] if self._rows else None


class _SceneFilterGraphSession:
    def __init__(self, *, missing_scene_ontology: bool = False) -> None:
        self.missing_scene_ontology = missing_scene_ontology

    async def run(self, query: str, *args, **kwargs):
        del args
        if "RETURN i.ontology_id AS ontology_id" in query:
            return _FakeResult(rows=[{"ontology_id": 42}])

        if "RETURN DISTINCT scene" in query:
            scene = {
                "id": "scene-1",
                "instance_id": kwargs.get("instance_id", "inst-1"),
                "name": "Scene One",
                "description": "Loaded by filter",
                "created_by_type": "human",
                "created_by_author": "tester",
                "created_at": "2026-04-21T10:00:00.000000Z",
                "updated_at": "2026-04-21T10:00:00.000000Z",
            }
            if not self.missing_scene_ontology:
                scene["ontology_id"] = 42
            return _FakeResult(rows=[{"scene": scene}])

        if "RETURN count(scene) AS count" in query:
            return _FakeResult(rows=[{"count": 1}])

        if "MATCH (:Scene {id: $scene_id})-[:DERIVED_FROM]->(entity:EntityInstance)" in query:
            return _FakeResult(rows=[{"entity_instance_id": "entity-1"}])

        if "OPTIONAL MATCH (scene)-[:FOLLOWED_BY]->(followed:Scene)" in query:
            return _FakeResult(
                rows=[{"followed_by_scene_id": None, "preceded_by_scene_id": None}]
            )

        if "AND 'Milestone' IN labels(milestone)" in query and "RETURN milestone" in query:
            return _FakeResult(
                rows=[
                    {
                        "milestone": {
                            "id": "milestone-1",
                            "scene_id": kwargs.get("scene_id", "scene-1"),
                            "instance_id": kwargs.get("instance_id", "inst-1"),
                            "ontology_id": 42,
                            "name": "Milestone One",
                            "description": "Has all ids",
                            "created_by_type": "human",
                            "created_by_author": "tester",
                            "temporal_type": "other",
                            "boundary_type": "none",
                            "created_at": "2026-04-21T10:00:00.000000Z",
                            "updated_at": "2026-04-21T10:00:00.000000Z",
                        },
                        "derived_from_entity_id": "entity-1",
                        "relates": [
                            {
                                "entity_instance_id": kwargs.get(
                                    "entity_instance_id", "entity-9"
                                ),
                                "label": "involves",
                            }
                        ],
                        "followed_by_milestone_id": None,
                        "preceded_by_milestone_id": None,
                    }
                ]
            )

        return _FakeResult(rows=[])


@pytest.mark.asyncio
async def test_list_scenes_by_derived_from_returns_scenes_with_milestones() -> None:
    service = OntologyInstanceService(
        sql_session=None,
        graph_session=_SceneFilterGraphSession(),
    )

    scenes = await service.list_scenes_by_derived_from("inst-1", "entity-1")

    assert len(scenes) == 1
    assert scenes[0].id == "scene-1"
    assert scenes[0].derived_from.entity_instance_id == "entity-1"
    assert len(scenes[0].milestones) == 1
    assert scenes[0].milestones[0].relates_to[0].entity_instance_id


@pytest.mark.asyncio
async def test_list_scenes_by_related_to_returns_scenes_with_milestones() -> None:
    service = OntologyInstanceService(
        sql_session=None,
        graph_session=_SceneFilterGraphSession(),
    )

    scenes = await service.list_scenes_by_related_to("inst-1", "entity-9")

    assert len(scenes) == 1
    assert scenes[0].id == "scene-1"
    assert len(scenes[0].milestones) == 1
    assert scenes[0].milestones[0].relates_to[0].entity_instance_id == "entity-9"


@pytest.mark.asyncio
async def test_list_scenes_by_related_to_rejects_missing_scene_ontology_id() -> None:
    service = OntologyInstanceService(
        sql_session=None,
        graph_session=_SceneFilterGraphSession(missing_scene_ontology=True),
    )

    with pytest.raises(ValueError, match="missing ontology_id"):
        await service.list_scenes_by_related_to("inst-1", "entity-9")
