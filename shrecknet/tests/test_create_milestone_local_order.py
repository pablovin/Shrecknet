from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.schemas.ontology_instance import MilestoneCreate
from app.services.ontology_instance_service import OntologyInstanceService


class _FakeTx:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict]] = []

    async def run(self, query: str, **params):
        self.queries.append((query, params))

    async def commit(self):
        pass

    async def rollback(self):
        pass

    async def close(self):
        pass


class _FakeGraphSession:
    def __init__(self) -> None:
        self.tx = _FakeTx()

    async def begin_transaction(self):
        return self.tx


@pytest.mark.asyncio
async def test_create_milestone_persists_local_order_edges() -> None:
    graph = _FakeGraphSession()
    service = OntologyInstanceService(sql_session=None, graph_session=graph)

    async def _get_scene(instance_id: str, scene_id: str):
        assert instance_id == "inst-1"
        assert scene_id == "scene-1"
        return SimpleNamespace(ontology_id=9)

    async def _get_milestone(instance_id: str, scene_id: str, milestone_id: str):
        return SimpleNamespace(id=milestone_id, scene_id=scene_id, instance_id=instance_id)

    async def _validate(*, instance_id: str, entity_instance_id: str):
        assert instance_id == "inst-1"
        assert entity_instance_id == "entity-1"

    async def _milestone_ids_for_scene(instance_id: str, scene_id: str):
        assert instance_id == "inst-1"
        assert scene_id == "scene-1"
        return []

    service.get_scene = _get_scene  # type: ignore[method-assign]
    service.get_milestone = _get_milestone  # type: ignore[method-assign]
    service._milestone_ids_for_scene = _milestone_ids_for_scene  # type: ignore[method-assign]
    service._validate_milestone_derived_from = _validate  # type: ignore[method-assign]

    payload = MilestoneCreate(
        id="milestone-2",
        name="Second",
        description="Second ordered milestone",
        created_by_type="human",
        created_by_author="user-1",
        derived_from={"entity_instance_id": "entity-1"},
        local_order={"preceded_by_milestone_id": "milestone-1"},
    )

    await service.create_milestone(
        "inst-1",
        "scene-1",
        payload,
        trigger_background_jobs=False,
    )

    queries = "\n".join(query for query, _params in graph.tx.queries)
    assert "MERGE (source)-[:PRECEDED_BY]->(target)" in queries
    assert "MERGE (target)-[:FOLLOWED_BY]->(source)" in queries
    assert any(
        params.get("target_milestone_id") == "milestone-1"
        for _query, params in graph.tx.queries
    )
