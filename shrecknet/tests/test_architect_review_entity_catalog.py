from __future__ import annotations

import pytest

from app.services.ontology_instance_service import OntologyInstanceService


class _Result:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    async def data(self) -> list[dict]:
        return self.rows


class _CatalogGraph:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def run(self, query: str, parameters: dict | None = None, **kwargs):
        del parameters
        self.calls.append((query, kwargs))
        if "LIMIT $fetch_limit" in query:
            return _Result([
                {"entity_instance_id": "entity-1", "entity_definition_id": 42,
                 "alias": "Robert Heinlein", "instance_id": "instance-1",
                 "instance_name": "Authors", "avatar_url": None, "agent_id": "agent-1",
                 "agent_name": "Julia", "agent_avatar_url": "https://example.test/julia.png",
                 "normalized_name": "robert heinlein"},
                {"entity_instance_id": "entity-2", "entity_definition_id": 42,
                 "alias": "Ursula Le Guin", "instance_id": "instance-2",
                 "instance_name": "Authors", "avatar_url": None, "agent_id": None,
                 "agent_name": None, "agent_avatar_url": None,
                 "normalized_name": "ursula le guin"},
            ])
        return _Result([
            {"entity_instance_id": "entity-2", "entity_definition_id": 42,
             "alias": "Ursula Le Guin", "instance_id": "instance-2",
             "instance_name": "Authors", "avatar_url": None, "agent_id": None,
             "agent_name": None, "agent_avatar_url": None},
        ])


@pytest.mark.asyncio
async def test_catalog_is_compact_and_uses_keyset_cursor() -> None:
    graph = _CatalogGraph()
    service = OntologyInstanceService(sql_session=None, graph_session=graph)

    response = await service.list_architect_review_entity_catalog(
        ontology_id=7, entity_definition_id=42, query=" Robert  Heinlein ", cursor=None, limit=1,
    )

    assert response.results[0].entity_instance_id == "entity-1"
    assert response.results[0].has_agent is True
    assert response.results[0].agent_avatar_url == "https://example.test/julia.png"
    assert response.next_cursor
    query, params = graph.calls[0]
    assert "entity.text" not in query
    assert "entity.properties" not in query
    assert "entity.normalized_name STARTS WITH $name_prefix" in query
    assert params["name_prefix"] == "robert heinlein"

    await service.list_architect_review_entity_catalog(
        ontology_id=7, entity_definition_id=42, query=None, cursor=response.next_cursor, limit=1,
    )
    assert graph.calls[1][1]["cursor_name"] == "robert heinlein"
    assert graph.calls[1][1]["cursor_id"] == "entity-1"


@pytest.mark.asyncio
async def test_catalog_resolver_preserves_order_and_hides_other_ids() -> None:
    service = OntologyInstanceService(sql_session=None, graph_session=_CatalogGraph())

    response = await service.resolve_architect_review_entity_catalog(
        ontology_id=7, entity_instance_ids=["entity-2", "missing", "entity-2"],
    )

    assert [item.entity_instance_id for item in response.results] == ["entity-2"]
    assert response.results[0].has_agent is False
    assert response.missing_entity_instance_ids == ["missing"]
