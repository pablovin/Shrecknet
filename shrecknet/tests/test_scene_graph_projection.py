from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.user import UserRole
from app.services.ontology_instance_service import OntologyInstanceService


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def __aiter__(self):
        async def iterator():
            for row in self.rows:
                yield row
        return iterator()


class _Graph:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def run(self, query: str, **kwargs):
        self.calls.append((query, kwargs))
        if "CASE WHEN type(rel) = 'DERIVED_FROM'" in query:
            return _Result([
                {"id": "entity-1", "label": "derived_from"},
                {"id": "entity-1", "label": "participant"},
                {"id": "entity-2", "label": "witness"},
            ])
        if "UNWIND $entity_ids AS entity_id" in query:
            return _Result([
                {"id": "entity-1", "alias": "Captain Ada", "avatar_url": "https://avatar/ada"},
                {"id": "entity-2", "alias": "The Archive", "avatar_url": None},
            ])
        raise AssertionError(query)


class _SqlSession:
    def __init__(self) -> None:
        self.get_calls: list[tuple[object, int]] = []

    async def get(self, model, identifier: int):
        self.get_calls.append((model, identifier))
        return object()

    async def scalar(self, query):  # pragma: no cover - privileged roles bypass it
        raise AssertionError(query)


@pytest.mark.asyncio
async def test_scene_graph_entities_are_deduplicated_and_batch_hydrated():
    graph = _Graph()
    service = OntologyInstanceService(sql_session=None, graph_session=graph)

    entities = await service._scene_graph_entities(instance_id="page-1", scene_id="scene-1")

    assert [entity.id for entity in entities] == ["entity-1", "entity-2"]
    assert entities[0].canonical_content_slug == "captain-ada"
    assert entities[0].relation_labels == ["derived_from", "participant"]
    metadata_call = next(call for call in graph.calls if "UNWIND $entity_ids" in call[0])
    assert set(metadata_call[1]["entity_ids"]) == {"entity-1", "entity-2"}


@pytest.mark.asyncio
async def test_scene_graph_read_access_uses_configured_sql_session_for_privileged_user():
    sql_session = _SqlSession()
    service = OntologyInstanceService(sql_session=sql_session, graph_session=None)

    await service.assert_ontology_graph_read_access(
        ontology_id=7,
        actor=SimpleNamespace(id=4, role=UserRole.WRITER),
    )

    assert sql_session.get_calls[0][1] == 7
